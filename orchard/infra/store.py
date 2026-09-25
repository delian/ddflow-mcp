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

from ..config import Config
from ..core.model import State, fold
from ..infra.log import EventLog

SCHEMA = 6

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
        self.fts = _has_fts5() and self.cfg.lessons.search_backend == "fts5"

    def _ensure_dir(self) -> None:
        """Create `.orchard/` only when something is actually about to be written.

        It used to happen in `__init__`, and `Ctx` builds a Store for EVERY command —
        so `orchard status` in a repository that had never run `orchard init` created
        `.orchard/` and exited 0, as though the project had adopted the tool. A
        read-only question must not leave a mark. The same rule was already applied to
        the MCP server's handshake for the same reason; this is the second door onto
        the same mistake.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        self._ensure_dir()
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
        create table if not exists decisions(
            id text primary key, title text, context text, decision text,
            consequences text, alternatives text, globs text, tags text,
            status text, decided_by text, superseded_by text, at text, item text);
        create table if not exists bugs(
            id text primary key, item text, summary text, found_at text,
            fixed_at text, regression_test text, lesson text);
        create table if not exists prompts(
            session text, seq integer, at text, item text, text text,
            role text default 'prompt',
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
            create virtual table if not exists decisions_fts using fts5(
                id unindexed, title, context, decision, consequences, alternatives,
                tokenize='porter unicode61');
            create virtual table if not exists prompts_fts using fts5(
                id unindexed, text, tokenize='porter unicode61');
            create virtual table if not exists bugs_fts using fts5(
                id unindexed, summary, lesson, tokenize='porter unicode61');
            create virtual table if not exists items_fts using fts5(
                id unindexed, title, body, tags, tokenize='porter unicode61');
            """)
        con.execute("insert or replace into meta values('schema', ?)", (str(SCHEMA),))

    def stale(self, log: EventLog) -> bool:
        """Is the index behind the log? Compared by (schema, shard count, byte total,
        last lamport) — four cheap numbers, any of which changing means re-derive.

        Bytes and shards, not an event count: two agents can append concurrently, so
        the highest Lamport can stay put while a second agent's shard grows.
        """
        if not self.path.exists():
            return True
        try:
            with closing(self.connect()) as con:
                row = {r["k"]: r["v"] for r in con.execute("select k,v from meta")}
        except sqlite3.DatabaseError:
            return True
        if row.get("schema") != str(SCHEMA):
            return True
        # `EventLog.head()` is O(shards); the old comparison called `read_all()` to
        # decide whether `read_all()` was needed, which is the shape of the problem
        # rather than a solution to it.
        shards, lamport, size = log.head()
        return (
            row.get("lamport") != str(lamport)
            or row.get("bytes") != str(size)
            or row.get("shards") != str(shards)
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
        # The fingerprint FIRST, then the events it describes. Taken afterwards it
        # describes a log NEWER than the projection: an append landing between the two
        # is in the fingerprint and not in the index, so the next `stale()` matches,
        # returns False, and those events are never projected -- `recall` silently
        # omits them, permanently, if the log then stops growing. Taken first the error
        # is in the safe direction: the fingerprint is at or behind what was projected,
        # so at worst one unnecessary rebuild, never a missed event. (Raised
        # THEORETICAL by the cross-family critic 2026-09-24; probe in
        # `tests/test_critic_findings.py`.)
        fingerprint = log.head()
        events = log.read_all()
        state = fold(events, strict=False)
        # `rebuild` writes its temp database beside the index rather than through
        # `connect`, so it needs the directory itself. Both write paths ask; no read
        # path does, which is the whole point — `orchard status` must not adopt a
        # repository that never ran `orchard init`.
        self._ensure_dir()
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
            _insert_decisions(con, state, self.fts)
            _insert_bugs(con, state, self.fts)
            _insert_sessions(con, state, self.fts)
            for name, runs in state.cadences.items():
                for r in runs:
                    con.execute(
                        "insert into cadences values(?,?,?,?)",
                        (name, r["at"], r["by"], r["result"]),
                    )
            con.execute("insert or replace into meta values('events', ?)", (str(len(events)),))
            # Written from the SAME cheap read `stale()` will use -- not recomputed
            # from `events`, which cannot produce a byte count at all -- and captured
            # BEFORE the events were read, so it can only under-report the log.
            shards, high, size = fingerprint
            for k, v in (("shards", shards), ("bytes", size)):
                con.execute("insert or replace into meta values(?, ?)", (k, str(v)))
            con.execute(
                "insert or replace into meta values('lamport', ?)",
                (str(high),),
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
            "decisions": ("title", "context", "decision", "consequences", "alternatives"),
            "research": ("question", "claim", "probe"),
            "bugs": ("summary", "lesson"),
            "prompts": ("text",),
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
                    if table == "prompts":
                        out = []
                        for ident in ids:
                            # `<session>#p<seq>` or `<session>#n<seq>` — the letter says
                            # whether it was the operator speaking or the agent noting.
                            sess, _, tail = ident.partition("#")
                            role, digits = (
                                (tail[:1], tail[1:]) if tail[:1].isalpha() else ("p", tail)
                            )
                            seq = int(digits or 0) + (10_000 if role == "n" else 0)
                            r2 = con.execute(
                                "select * from prompts where session=? and seq=?",
                                (sess, seq),
                            ).fetchone()
                            if r2:
                                row = dict(r2)
                                row["id"] = ident
                                out.append(row)
                        out.sort(key=lambda r: scores.get(r["id"], 0.0))
                        return out
                    ph = ",".join("?" * len(ids))
                    full = con.execute(f"select * from {table} where id in ({ph})", ids).fetchall()
                    out = [dict(r) for r in full]
                    out.sort(key=lambda r: scores.get(r["id"], 0.0))
                    return out
            # `>=`, matching the FTS5 tokenizer above. The two disagreed by one, so
            # searching "db" found a lesson on the machine whose SQLite has FTS5 and
            # found nothing on the machine whose SQLite does not — the same query,
            # two answers, decided by a build flag nobody sets deliberately.
            terms = [t for t in re.split(r"\W+", query) if len(t) >= MIN_TERM_CHARS][:8]
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


#: What `recall` searches, in the order a reader should weigh them. Decisions first
#: because they are binding, lessons next because they are transferable, then evidence,
#: then history. The order is the answer to "which of these should change what I do".
RECALL_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("decisions", "DECISION", "binding — follow it unless the operator says otherwise"),
    ("lessons", "LESSON", "learned the hard way here"),
    ("research", "RESEARCH", "already investigated; check the verdict before redoing it"),
    ("bugs", "BUG", "this has broken before"),
    ("items", "TASK", "similar work already planned or done"),
    ("prompts", "PROMPT/NOTE", "the operator asked, or an agent recorded, something like this"),
)


def summarise_row(table: str, row: dict[str, Any], width: int = 240) -> tuple[str, str]:
    """(headline, body) for one hit, per source table."""
    if table == "decisions":
        head = f"{row.get('title', '')}"
        if row.get("status") != "accepted" or row.get("superseded_by"):
            head += f"  [{row.get('status')}"
            head += f" -> {row['superseded_by']}]" if row.get("superseded_by") else "]"
        return head, (row.get("decision") or "")[:width]
    if table == "lessons":
        return row.get("title", ""), (row.get("rule") or "")[:width]
    if table == "research":
        return (
            f"{row.get('question', '')}  [{row.get('verdict', '')}]",
            (row.get("claim") or "")[:width],
        )
    if table == "bugs":
        state = "fixed" if row.get("fixed_at") else "OPEN"
        return f"{row.get('summary', '')}  [{state}]", (row.get("lesson") or "")[:width]
    if table == "items":
        return f"{row.get('id', '')} — {row.get('title', '')}", (row.get("body") or "")[:width]
    if table == "prompts":
        text = (row.get("text") or "").strip().replace("\n", " ")
        # A note is the AGENT's record of the work — a dead end, a surprise, why it
        # changed approach — and attributing one to the operator would put words in
        # their mouth, which is worse than not surfacing it at all.
        who = "the agent noted:" if row.get("role") == "note" else "operator asked:"
        return f"{row.get('at', '')[:10]} {who}", text[:width]
    return row.get("id", ""), ""


def _insert_sessions(con, state, fts: bool) -> None:
    """Prompts AND notes, in one table, distinguished by `role`.

    A note is what the agent recorded about the work — a dead end, a surprise, why it
    changed approach — and it was folded, replayed and then never indexed, so `recall`
    could not find the one record of why something was abandoned. Notes are shifted by
    10,000 so a note and a prompt with the same `seq` do not collide on the primary key.
    """
    for sess in state.sessions.values():
        entries = [(pr, "prompt") for pr in sess.prompts] + [(nt, "note") for nt in sess.notes]
        for n, (entry, role) in enumerate(entries):
            seq = int(entry.get("seq", n))
            con.execute(
                "insert or replace into prompts values(?,?,?,?,?,?)",
                (
                    sess.id,
                    seq if role == "prompt" else 10_000 + seq,
                    # `origin_at` when the note records something that happened before
                    # it was written down -- an imported journal entry or memory --
                    # so `recall` dates it by when it was TRUE, not by when the import
                    # ran and stamped every one of them with today.
                    entry.get("origin_at") or entry.get("at", ""),
                    entry.get("item", ""),
                    entry.get("text", ""),
                    role,
                ),
            )
            if fts:
                con.execute(
                    "insert into prompts_fts values(?,?)",
                    (f"{sess.id}#{role[0]}{seq}", entry.get("text", "")),
                )


def _insert_decisions(con, state, fts: bool) -> None:
    """Decisions + their search index. Split out of `rebuild` because the per-table
    inserts are independent and reading six of them in one function obscured that."""
    for dc in state.decisions.values():
        con.execute(
            "insert or replace into decisions values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                dc.id,
                dc.title,
                dc.context,
                dc.decision,
                dc.consequences,
                dc.alternatives,
                json.dumps(dc.globs),
                json.dumps(dc.tags),
                dc.status,
                dc.decided_by,
                dc.superseded_by,
                dc.at,
                dc.item,
            ),
        )
        if fts:
            con.execute(
                "insert into decisions_fts values(?,?,?,?,?,?)",
                (dc.id, dc.title, dc.context, dc.decision, dc.consequences, dc.alternatives),
            )


def _insert_bugs(con, state, fts: bool) -> None:
    for bg in state.bugs.values():
        if fts:
            con.execute("insert into bugs_fts values(?,?,?)", (bg.id, bg.summary, bg.lesson))
        con.execute(
            "insert or replace into bugs values(?,?,?,?,?,?,?)",
            (bg.id, bg.item, bg.summary, bg.found_at, bg.fixed_at, bg.regression_test, bg.lesson),
        )
