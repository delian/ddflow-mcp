"""The projection's residue model: projected rows, tables, reconcile and similarity.

`_Row` / `_Table` / `_TABLES` describe the tables once, `_project` resolves every row
ONE time, `_write_rows` inserts them whole and `_reconcile_tables` inserts only what a
delta changed. ONE projector means an incremental never disagrees with a rebuild.
`similar_records` / `_insert_similar` feed the similarity engine's index.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from ...core import participation as PA
from ...core import textsim
from ...core.model import State
from ._fingerprint import _SEARCH_COLS


@dataclass(frozen=True)
class _Row:
    """One projected row: its primary key, its values (the insert's arguments) and its
    FTS5 row when its table has one. The full writer inserts every row; the reconciler
    differs them -- residues ONE shape, so the two never disagree."""

    key: tuple
    values: tuple
    fts: tuple | None = None


@dataclass(frozen=True)
class _Table:
    """One projected table: its insert statements and how its rows are keyed. `key_len`
    leading columns are the primary key (0 for `cadences`, whose identity is the whole
    row; 2 for the `(session, seq)` of `prompts`), and a 1-keyed table's key IS its FTS
    id, which is what an FTS/trigram delete needs (prompts' is not, so its old FTS rows
    cannot stay behind by deletion -- and they never need to: prompts only ever grow)."""

    name: str
    cols: tuple[str, ...]
    key_len: int
    fts: str | None
    fts_w: int

    @property
    def insert(self) -> str:
        return f"insert or replace into {self.name} values({','.join('?' * len(self.cols))})"

    @property
    def fts_insert(self) -> str:
        return f"insert or replace into {self.fts} values({','.join('?' * self.fts_w)})"

    def where(self, key: tuple) -> str:
        cols = self.cols[: self.key_len or len(self.cols)]
        return f"delete from {self.name} where {' and '.join(c + ' = ?' for c in cols)}"  # nosec B608


#: Every projected table, in ITS insert order (`SCHEMA`'s column order, byte-faithful).
_TABLES = {
    "items": _Table(
        "items",
        (
            "id",
            "kind",
            "title",
            "parent",
            "state",
            "needs",
            "globs",
            "tags",
            "priority",
            "body",
            "worktree",
            "branch",
            "merged_sha",
            "blocked_reason",
            "lease_holder",
            "lease_expires",
            "gates",
            "created_at",
            "completed_at",
        ),
        1,
        "items_fts",
        4,
    ),
    "lessons": _Table(
        "lessons",
        ("id", "title", "rule", "why", "how", "seen_in", "tags", "at", "superseded_by"),
        1,
        "lessons_fts",
        6,
    ),
    "research": _Table(
        "research",
        ("id", "question", "claim", "verdict", "probe", "sources", "at", "item"),
        1,
        "research_fts",
        5,
    ),
    "decisions": _Table(
        "decisions",
        (
            "id",
            "title",
            "context",
            "decision",
            "consequences",
            "alternatives",
            "globs",
            "tags",
            "status",
            "decided_by",
            "superseded_by",
            "at",
            "item",
        ),
        1,
        "decisions_fts",
        6,
    ),
    "memories": _Table(
        "memories",
        ("id", "text", "tags", "at", "origin_at", "by", "source"),
        1,
        "memories_fts",
        3,
    ),
    "bugs": _Table(
        "bugs",
        (
            "id",
            "item",
            "summary",
            "found_at",
            "fixed_at",
            "regression_test",
            "lesson",
            "invalid_at",
            "invalid_reason",
            "evidence",
        ),
        1,
        "bugs_fts",
        3,
    ),
    "prompts": _Table(
        "prompts",
        ("session", "seq", "at", "item", "text", "role"),
        2,
        "prompts_fts",
        2,
    ),
    "records": _Table(
        "records",
        ("id", "kind", "rid", "title", "body", "status", "at"),
        1,
        "records_fts",
        3,
    ),
    "cadences": _Table("cadences", ("name", "at", "by", "result"), 0, None, 0),
}


def _rows_items(rows: dict[str, list[_Row]], state: State) -> None:
    for it in state.items.values():
        if it.removed:
            continue
        rows["items"].append(
            _Row(
                (it.id,),
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
                (it.id, it.title, it.body, " ".join(it.tags)),
            )
        )


def _rows_lessons(rows: dict[str, list[_Row]], state: State) -> None:
    # A contested lesson (B230e852e10) is not advice until `resolve` settles it.
    for ls in (x for x in state.lessons.values() if not x.contested):
        rows["lessons"].append(
            _Row(
                (ls.id,),
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
                (ls.id, ls.title, ls.rule, ls.why, ls.how, " ".join(ls.tags)),
            )
        )


def _rows_research(rows: dict[str, list[_Row]], state: State) -> None:
    for rn in state.research.values():
        rows["research"].append(
            _Row(
                (rn.id,),
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
                (rn.id, rn.question, rn.claim, rn.probe, rn.verdict),
            )
        )


def _rows_decisions(rows: dict[str, list[_Row]], state: State) -> None:
    for dc in state.decisions.values():
        rows["decisions"].append(
            _Row(
                (dc.id,),
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
                (dc.id, dc.title, dc.context, dc.decision, dc.consequences, dc.alternatives),
            )
        )


def _rows_memories(rows: dict[str, list[_Row]], state: State) -> None:
    # LIVE memories only: a forgotten one stays in the log and in `memory list --all`,
    # but recall exists to change what an agent does next.
    for m in state.memories.values():
        if not m.live:
            continue
        rows["memories"].append(
            _Row(
                (m.id,),
                (
                    m.id,
                    m.text,
                    json.dumps(m.tags),
                    m.at,
                    m.origin_at,
                    m.by,
                    m.source,
                ),
                (m.id, m.text, " ".join(m.tags)),
            )
        )


def _rows_bugs(rows: dict[str, list[_Row]], state: State) -> None:
    for bg in state.bugs.values():
        rows["bugs"].append(
            _Row(
                (bg.id,),
                (
                    bg.id,
                    bg.item,
                    bg.summary,
                    bg.found_at,
                    bg.fixed_at,
                    bg.regression_test,
                    bg.lesson,
                    bg.invalid_at,
                    bg.invalid_reason,
                    bg.evidence,
                ),
                (bg.id, bg.summary, bg.lesson),
            )
        )


def _rows_prompts(rows: dict[str, list[_Row]], state: State) -> None:
    """Prompts, notes AND the session's summary, one table, distinguished by `role`;
    each role's ``seq`` sits in a band the others cannot reach (`_role_seq`), so the
    session's own seqs can never collide on the primary key (the FTS id's letter
    maps back)."""
    for sess in state.sessions.values():
        entries = [(pr, "prompt") for pr in sess.prompts] + [(nt, "note") for nt in sess.notes]
        if sess.summary:  # what `session end --summary` said the session did (B194)
            entries.append(({"at": sess.ended_at, "text": sess.summary, "seq": 0}, "summary"))
        for n, (entry, role) in enumerate(entries):
            seq = _role_seq(role, int(entry.get("seq", n)))
            rows["prompts"].append(
                _Row(
                    (sess.id, seq),
                    (
                        sess.id,
                        seq,
                        # `origin_at` when the note records something that happened before
                        # it was written down -- so `recall` dates it by when it was TRUE.
                        entry.get("origin_at") or entry.get("at", ""),
                        entry.get("item", ""),
                        entry.get("text", ""),
                        role,
                    ),
                    (f"{sess.id}#{role[0]}{int(entry.get('seq', n))}", entry.get("text", "")),
                )
            )


def _rows_records(rows: dict[str, list[_Row]], state: State) -> None:
    """The ACTIVE records of the declared kinds that are recalled, in the ``records``
    table: a retired, superseded or merged one no longer changes what an agent should
    do. The id is ``<kind>:<id>``."""
    for rec in state.defs.values():
        if not rec.live:
            continue
        for d in PA.recalled():
            if d.def_kind != rec.kind:
                continue
            key = f"{d.name}:{rec.id}"
            title = str(rec.fields.get(d.title, ""))
            body = d.text({k: v for k, v in rec.fields.items() if k != d.title})
            rows["records"].append(
                _Row(
                    (key,),
                    (key, d.name, rec.id, title, body, rec.status, rec.updated_at or rec.at),
                    (key, title, body),
                )
            )


def _rows_cadences(rows: dict[str, list[_Row]], state: State) -> None:
    for name, runs in state.cadences.items():
        for r in runs:
            rows["cadences"].append(_Row((), (name, r["at"], r["by"], r["result"]), None))


def _project(state: State) -> dict[str, list[_Row]]:
    """Every table's rows as they are resolved THE one time: `_write_rows` inserts them
    whole, `_reconcile_tables` inserts only what differs. ONE projector means an
    incremental never disagrees with a rebuild -- it is the rebuild, minus the rows
    that were already there."""
    rows: dict[str, list[_Row]] = {name: [] for name in _TABLES}
    _rows_items(rows, state)
    _rows_lessons(rows, state)
    _rows_research(rows, state)
    _rows_decisions(rows, state)
    _rows_memories(rows, state)
    _rows_bugs(rows, state)
    _rows_prompts(rows, state)
    _rows_records(rows, state)
    _rows_cadences(rows, state)
    return rows


def _write_rows(con, proj: dict[str, list[_Row]], fts: bool) -> None:
    """The full writer: every projected row, base and FTS, into a fresh index."""
    for name, rows in proj.items():
        t = _TABLES[name]
        for r in rows:
            con.execute(t.insert, r.values)
            if fts and r.fts is not None:
                con.execute(t.fts_insert, r.fts)


#: The ranked text of a trigram row, per table: the SAME columns `_SEARCH_COLS` ranks on,
#: joined -- `_fill_trigram` copies them all, a reconcile only the changed ids.
_TRI_EXPR = {
    t: " || ' ' || ".join(f"coalesce({c}, '')" for c in cols) for t, cols in _SEARCH_COLS.items()
}


def _fill_trigram(con) -> None:
    """Copy each table's ranked text from its porter FTS5 table into the trigram one."""
    for table, _cols in _SEARCH_COLS.items():
        con.execute(
            # bandit B608: names come from the fixed `_SEARCH_COLS`.
            f"insert into {table}_tri(id, text) select id, {_TRI_EXPR[table]} from {table}_fts"  # nosec B608
        )


def _reconcile_tables(con, proj: dict[str, list[_Row]], fts: bool, trig: bool) -> None:
    """The incremental writer: read the index's rows once, write only what differs.
    Raises `RuntimeError` on a count that disagrees -- the caller answers with a full
    rebuild, never with a wrong row."""
    for name, rows in proj.items():
        t = _TABLES[name]
        cols = ", ".join(t.cols)  # nosec B608: fixed at import
        want = {r.key: r for r in rows} if t.key_len else {r.values: r for r in rows}
        have = {
            (tuple(r)[: t.key_len] if t.key_len else tuple(r)): tuple(r)
            for r in con.execute(f"select {cols} from {t.name}")  # nosec B608
        }
        touched: list[_Row] = []
        gone: list[tuple] = []
        for key, r in want.items():
            if have.get(key) != r.values:
                con.execute(t.insert, r.values)
                if fts and r.fts is not None:
                    # FTS5 replaces on rowid, which a text id never equals -- a plain
                    # insert-or-replace would append a duplicate row for the same id.
                    # Delete by the id first (the arity is irrelevant: the id column IS
                    # r.fts[0]); the row just written replaces.
                    con.execute(f"delete from {t.fts} where id = ?", (r.fts[0],))  # nosec B608
                    con.execute(t.fts_insert, r.fts)
                touched.append(r)
        for key in set(have) - set(want):
            con.execute(t.where(key), key)
            if fts and t.fts is not None and t.key_len == 1:
                # A 1-keyed table's key IS its FTS id; prompts' (key_len 2) is not,
                # and prompts only ever grow, so a stale FTS row there cannot be left
                # behind -- no gone prompt has a key-deletable FTS row.
                con.execute(f"delete from {t.fts} where id = ?", (key[0],))  # nosec B608
            gone.append(key)
        if t.name == "prompts" and gone:
            # A gone prompt disobeys "prompts only ever grow" -- its stale FTS/trigram
            # row cannot be deleted by base key -- and doubt is a rebuild, not a residue.
            raise RuntimeError(f"incremental {t.name}: a projected prompt row is gone")
        if con.execute(f"select count(*) from {t.name}").fetchone()[0] != len(rows):  # nosec B608
            raise RuntimeError(f"incremental {t.name}: reconciled rows disagree")
        if not trig or not t.fts:
            continue
        if gone and t.key_len == 1:
            for chunk in _chunks([k[0] for k in gone], _RECONCILE_BATCH):
                phs = ",".join("?" * len(chunk))
                con.execute(
                    f"delete from {t.name}_tri where id in ({phs})",  # nosec B608
                    chunk,
                )
        ids = [r.fts[0] for r in touched if r.fts is not None]
        for chunk in _chunks(ids, _RECONCILE_BATCH):
            phs = ",".join("?" * len(chunk))
            con.execute(
                f"delete from {t.name}_tri where id in ({phs})",  # nosec B608
                chunk,
            )
            con.execute(
                "insert into "
                f"{t.name}_tri(id, text) select id, {_TRI_EXPR[t.name]} "
                f"from {t.fts} where id in ({phs})",  # nosec B608
                chunk,
            )


def _chunks(items: list, n: int) -> list:
    """``items`` in slices of ``n``, so an ``id in (?, ...)`` list stays far under
    SQLite's bound-variable limit (999 on older builds): a delta large enough to
    exceed it batches, instead of the transaction dying mid-reconcile."""
    return [items[i : i + n] for i in range(0, len(items), n)]


#: Row cap per ``id in (...)`` clause in a reconcile (``_RECONCILE_BATCH``):
#: 900 bounds the variables below any SQLite build's limit; a bigger delta just
#: batches. A batch that FAILED would answer the whole reconcile with a rebuild.
_RECONCILE_BATCH = 900


#: Two roles' rows sit in `seq` bands no other role can reach, however many entries
#: one session holds: a prompt's seq IS its count (>= 0, what `sessions.start`
#: assigns), a note's is its count, so notes sit at -(seq+1) and the summary at one
#: fixed negative slot -- a prompt, a note and the summary can never collide on the
#: primary key at any size a log fits on. The FTS id's letter maps the row back
#: (`_seq_role`), which is what makes the bands invertible instead of shifted.
_SUMMARY_SLOT = 1 << 62  # negative for the summary; 2**62 notes away from every note


def _role_seq(role: str, seq: int) -> int:
    """The ``seq`` a ``prompts`` row keeps, from the entry's own seq and its role."""
    if role == "note":
        return -(int(seq) + 1)
    if role == "summary":
        return int(seq) - _SUMMARY_SLOT
    return int(seq)


def _seq_role(role: str, seq: int) -> int:
    """The inverse of ``_role_seq``: the seq an FTS id's digits carried."""
    if role == "note":
        return -(int(seq) + 1)
    if role == "summary":
        return int(seq) + _SUMMARY_SLOT
    return int(seq)


def similar_records(state: State) -> list[dict[str, str]]:
    """Every record a new one could duplicate, as the similarity engine reads them: id,
    kind, title, body, and item (what a bug was filed against, a task's phase).

    Which fields are a record's text is decided here, once, for the stored weights and
    for anyone building a matcher from a State. Removed items and forgotten memories
    are out, as they are out of the rest of the index; closed and invalid records stay
    in -- a new bug that repeats a fixed one is exactly what an add should be shown.
    """
    out = [
        {"id": it.id, "kind": it.kind, "title": it.title, "body": it.body, "item": it.parent}
        for it in state.items.values()
        if not it.removed
    ]
    out += [
        {"id": b.id, "kind": "bug", "title": b.title, "body": b.summary, "item": b.item}
        for b in state.bugs.values()
    ]
    out += [
        {
            "id": ls.id,
            "kind": "lesson",
            "title": ls.title,
            "body": "\n".join(x for x in (ls.rule, ls.why, ls.how) if x),
            "item": "",
        }
        for ls in state.lessons.values()
        if not getattr(ls, "contested", None)
    ]
    out += [
        {
            "id": d.id,
            "kind": "decision",
            "title": d.title,
            "body": "\n".join(x for x in (d.context, d.decision) if x),
            "item": d.item,
        }
        for d in state.decisions.values()
        if not getattr(d, "contested", None)
    ]
    out += [
        {
            "id": r.id,
            "kind": "research",
            "title": r.question,
            "body": "\n".join(x for x in (r.claim, r.mechanism) if x),
            "item": r.item,
        }
        for r in state.research.values()
    ]
    out += [
        {"id": m.id, "kind": "memory", "title": "", "body": m.text, "item": ""}
        for m in state.memories.values()
        if m.live
    ]
    return out


def _insert_similar(con, records: list[dict[str, str]], clear: bool = False) -> None:
    """The similarity engine's index (``services.similar``): one row per record, and
    per term its postings -- record ordinals and TF-IDF weights, packed. With `clear`
    (a reconcile decided the corpus changed) every previous row is replaced from
    `records`; the caller must pass the COMPLETE current record set for that -- a
    partial list would wipe the unchanged records' postings with reclaiming."""
    if clear:
        con.execute("delete from similar_docs")
        con.execute("delete from similar_terms")
    con.executemany(
        "insert into similar_docs values(?,?,?,?,?)",
        [
            (i, r["id"], r["kind"], r["item"], textsim.digest(r["title"], r["body"]))
            for i, r in enumerate(records)
        ],
    )
    _df, post = textsim.invert([textsim.tokens(r["title"], r["body"]) for r in records])
    con.executemany(
        "insert into similar_terms values(?,?,?)",
        [(t, d.tobytes(), w.tobytes()) for t, (d, w) in post.items()],
    )
    con.execute(
        "insert or replace into meta values('similar_n', ?), ('similar_version', ?)",
        (str(len(records)), str(textsim.VERSION)),
    )
