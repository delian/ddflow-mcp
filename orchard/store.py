"""The SQLite projection — a cache and a search index, never a source of truth.

This database is **gitignored, disposable and rebuildable**. Delete it and
``orchard rebuild`` re-derives every row from the event log. That is not a slogan; it
is enforced by ``tests/test_store.py::test_dropping_the_db_changes_nothing``, which
compares every query before and after ``rm``.

Keeping the index non-authoritative buys three things a database-of-record cannot:

* two agents on two git branches never conflict, because they merge *log files*, not
  database pages;
* a corrupted index is a 200 ms annoyance instead of a lost project;
* the index schema can change without a migration — bump ``SCHEMA`` and rebuild.

What it is genuinely *for* is retrieval. Folding the log answers "what is the state";
it does not answer "which four of my 400 lessons bear on the task I am about to
start". That is a ranking problem, and FTS5's BM25 is a better answer than anything
worth hand-rolling. Where SQLite was built without FTS5 the store degrades to LIKE
scanning, which is slower and worse-ranked but never absent — a search that vanishes
on some machines is worse than one that is merely weaker.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .config import Config
from .events import EventLog
from .model import State, fold

SCHEMA = 3

#: Shortest token kept from a user query. One-character tokens match almost everything
#: and rank nothing, so they cost index time and return noise.
MIN_TERM_CHARS = 2


def _has_fts5() -> bool:
    with closing(sqlite3.connect(":memory:")) as c:
        return any("FTS5" in r[0] for r in c.execute("pragma compile_options"))


class Store:
    def __init__(self, root: Path, cfg: Config | None = None) -> None:
        self.root = Path(root)
        self.cfg = cfg or Config.load(root)
        self.path = self.root / ".orchard" / "index.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fts = _has_fts5() and self.cfg.lessons.search_backend == "fts5"

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("pragma journal_mode=WAL")
        con.execute("pragma synchronous=NORMAL")
        con.execute("pragma busy_timeout=30000")
        return con

    # -- schema ---------------------------------------------------------------------
    def init(self, con: sqlite3.Connection) -> None:
        con.executescript("""
        create table if not exists meta(k text primary key, v text);
        create table if not exists items(
            id text primary key, kind text, title text, parent text, state text,
            needs text, globs text, tags text, priority integer, body text,
            worktree text, branch text, merged_sha text, blocked_reason text,
            lease_holder text, lease_expires real, gates text,
            created_at text, completed_at text);
        create index if not exists items_parent on items(parent);
        create index if not exists items_state  on items(state);
        create table if not exists lessons(
            id text primary key, title text, rule text, why text, how text,
            seen_in text, tags text, at text, superseded_by text);
        create table if not exists research(
            id text primary key, question text, claim text, verdict text,
            probe text, sources text, at text, item text);
        create table if not exists bugs(
            id text primary key, item text, summary text, found_at text,
            fixed_at text, regression_test text, lesson text);
        create table if not exists prompts(
            session text, seq integer, at text, item text, text text,
            primary key(session, seq));
        create table if not exists cadences(name text, at text, by text, result text);
        """)
        if self.fts:
            con.executescript("""
            create virtual table if not exists lessons_fts using fts5(
                id unindexed, title, rule, why, how, tags,
                tokenize='porter unicode61');
            create virtual table if not exists research_fts using fts5(
                id unindexed, question, claim, probe, verdict,
                tokenize='porter unicode61');
            create virtual table if not exists items_fts using fts5(
                id unindexed, title, body, tags, tokenize='porter unicode61');
            """)
        con.execute("insert or replace into meta values('schema', ?)", (str(SCHEMA),))

    def stale(self, log: EventLog) -> bool:
        """Is the index behind the log? Compared by (schema, event count, last lamport)
        — three cheap numbers, any of which changing means re-derive."""
        if not self.path.exists():
            return True
        try:
            with closing(self.connect()) as con:
                row = {r["k"]: r["v"] for r in con.execute("select k,v from meta")}
        except sqlite3.DatabaseError:
            return True
        if row.get("schema") != str(SCHEMA):
            return True
        evs = log.read_all()
        return row.get("events") != str(len(evs)) or row.get("lamport") != str(
            max((e.lamport for e in evs), default=0)
        )

    # -- projection -----------------------------------------------------------------
    def rebuild(self, log: EventLog) -> State:
        """Drop everything and re-derive from the log. The ONLY write path.

        There is deliberately no incremental update. An incremental projector is a
        second implementation of `fold` that can disagree with it, and a cache that
        disagrees with its source silently is worse than no cache. Rebuild measured
        at ~12k events/second, which for any realistic backlog is far below the cost
        of the git operations surrounding it.
        """
        events = log.read_all()
        state = fold(events, strict=False)
        tmp = self.path.with_suffix(".rebuilding")
        for p in (tmp, tmp.with_name(tmp.name + "-wal"), tmp.with_name(tmp.name + "-shm")):
            p.unlink(missing_ok=True)
        con = sqlite3.connect(tmp, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("pragma journal_mode=WAL")
        try:
            self.init(con)
            con.execute("BEGIN IMMEDIATE")
            for it in state.items.values():
                if it.removed:
                    continue
                con.execute(
                    "insert or replace into items values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        it.id,
                        it.kind,
                        it.title,
                        it.parent,
                        it.state,
                        json.dumps(it.needs),
                        json.dumps(it.globs),
                        json.dumps(it.tags),
                        it.priority,
                        it.body,
                        it.worktree,
                        it.branch,
                        it.merged_sha,
                        it.blocked_reason,
                        it.lease.holder if it.lease else "",
                        (it.lease.renewed_at + it.lease.ttl_s) if it.lease else 0.0,
                        json.dumps({g: asdict(r) for g, r in it.gates.items()}),
                        it.created_at,
                        it.completed_at,
                    ),
                )
                if self.fts:
                    con.execute(
                        "insert into items_fts values(?,?,?,?)",
                        (it.id, it.title, it.body, " ".join(it.tags)),
                    )
            for ls in state.lessons.values():
                con.execute(
                    "insert or replace into lessons values(?,?,?,?,?,?,?,?,?)",
                    (
                        ls.id,
                        ls.title,
                        ls.rule,
                        ls.why,
                        ls.how,
                        json.dumps(ls.seen_in),
                        json.dumps(ls.tags),
                        ls.at,
                        ls.superseded_by,
                    ),
                )
                if self.fts:
                    con.execute(
                        "insert into lessons_fts values(?,?,?,?,?,?)",
                        (ls.id, ls.title, ls.rule, ls.why, ls.how, " ".join(ls.tags)),
                    )
            for rn in state.research.values():
                con.execute(
                    "insert or replace into research values(?,?,?,?,?,?,?,?)",
                    (
                        rn.id,
                        rn.question,
                        rn.claim,
                        rn.verdict,
                        rn.probe,
                        json.dumps(rn.sources),
                        rn.at,
                        rn.item,
                    ),
                )
                if self.fts:
                    con.execute(
                        "insert into research_fts values(?,?,?,?,?)",
                        (rn.id, rn.question, rn.claim, rn.probe, rn.verdict),
                    )
            for bg in state.bugs.values():
                con.execute(
                    "insert or replace into bugs values(?,?,?,?,?,?,?)",
                    (
                        bg.id,
                        bg.item,
                        bg.summary,
                        bg.found_at,
                        bg.fixed_at,
                        bg.regression_test,
                        bg.lesson,
                    ),
                )
            for s in state.sessions.values():
                for pr in s.prompts:
                    con.execute(
                        "insert or replace into prompts values(?,?,?,?,?)",
                        (s.id, pr["seq"], pr["at"], pr.get("item", ""), pr["text"]),
                    )
            for name, runs in state.cadences.items():
                for r in runs:
                    con.execute(
                        "insert into cadences values(?,?,?,?)",
                        (name, r["at"], r["by"], r["result"]),
                    )
            con.execute("insert or replace into meta values('events', ?)", (str(len(events)),))
            con.execute(
                "insert or replace into meta values('lamport', ?)",
                (str(max((e.lamport for e in events), default=0)),),
            )
            con.execute(
                "insert or replace into meta values('built_at', ?), ('fts', ?)",
                (str(time.time()), "1" if self.fts else "0"),
            )
            con.execute("COMMIT")
        finally:
            con.close()
        # Publish atomically: readers see either the old index or the new one, never
        # a half-built one. WAL sidecars are removed first so the replaced file is
        # self-contained.
        for suf in ("-wal", "-shm"):
            tmp.with_name(tmp.name + suf).unlink(missing_ok=True)
            self.path.with_name(self.path.name + suf).unlink(missing_ok=True)
        tmp.replace(self.path)
        return state

    def ensure(self, log: EventLog) -> State:
        if self.stale(log):
            return self.rebuild(log)
        return fold(log.read_all(), strict=False)

    # -- search ---------------------------------------------------------------------
    def search(self, table: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Rank ``table`` against ``query``. BM25 when FTS5 exists, LIKE otherwise.

        The user query is passed through ``_fts_query``, which strips FTS operator
        characters and ORs the terms. Raw user text reaching an FTS5 MATCH is a syntax
        error waiting to happen — an unbalanced quote or a bare ``-`` raises, and a
        search that throws on a normal question is a search nobody uses.
        """
        cols = {
            "lessons": ("title", "rule", "why", "how"),
            "research": ("question", "claim", "probe"),
            "items": ("title", "body"),
        }[table]
        with closing(self.connect()) as con:
            self.init(con)
            if self.fts:
                q = _fts_query(query)
                if not q:
                    return []
                try:
                    rows = con.execute(
                        f"select f.id as id, bm25({table}_fts) as score "
                        f"from {table}_fts f where {table}_fts match ? "
                        f"order by score limit ?",
                        (q, limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
                if rows:
                    ids = [r["id"] for r in rows]
                    scores = {r["id"]: r["score"] for r in rows}
                    ph = ",".join("?" * len(ids))
                    full = con.execute(f"select * from {table} where id in ({ph})", ids).fetchall()
                    out = [dict(r) for r in full]
                    out.sort(key=lambda r: scores.get(r["id"], 0.0))
                    return out
            terms = [t for t in re.split(r"\W+", query) if len(t) > MIN_TERM_CHARS][:8]
            if not terms:
                return []
            where = " or ".join(f"{c} like ?" for c in cols for _ in terms)
            args = [f"%{t}%" for _ in cols for t in terms]
            rows = con.execute(
                f"select * from {table} where {where} limit ?", (*args, limit)
            ).fetchall()
            return [dict(r) for r in rows]

    def query(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with closing(self.connect()) as con:
            self.init(con)
            return [dict(r) for r in con.execute(sql, args)]


def _fts_query(text: str) -> str:
    """Turn arbitrary user text into a safe FTS5 expression.

    Terms are quoted individually and ORed. Quoting is what makes an operator
    character inert; ORing is what makes a multi-word question behave like a
    relevance query instead of a conjunction that matches nothing.
    """
    terms = [t for t in re.split(r"[^\w]+", text) if len(t) >= MIN_TERM_CHARS]
    return " OR ".join(f'"{t}"' for t in terms[:12])
