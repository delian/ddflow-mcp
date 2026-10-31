"""Record ids for shell completion, read straight from the derived sqlite index.

IMPORT-LIGHT BY CONTRACT: standard library only (os, re, sqlite3). It never imports
`ddflow.api`, never reads the event log, and never raises: any failure (no index, a
locked or corrupt database, an odd schema) yields no candidates, because a TAB that
prints a traceback is worse than a TAB that offers nothing.

The kinds are not a list kept here: they are the tables of the index that carry an `id`
column (plus the `kind` values inside `items` and `records`), so a new record kind that
the index stores completes by itself.

`prefix`, `kind` and `state` come from a shell word and are untrusted: they only ever
travel as bound parameters, and `%`/`_`/`\\` in a prefix are literal.

The index file name embeds the code fingerprint, so several can exist; the newest by
mtime wins, and its `-wal`/`-shm`/`-lock` companions are never mistaken for an index.
"""

from __future__ import annotations

import os
import re
import sqlite3

#: A file name that is an index (hex fingerprint), not a sidecar or a rebuild temp.
INDEX_NAME = re.compile(r"^index\.db-[0-9a-f]{6,}$")
DEFAULT_LIMIT = 200
#: Columns that hold a record's state, in the order they are preferred.
STATE_COLUMNS = ("state", "status")
#: State derived from timestamp columns, where a table has no state column at all.
_DERIVED = (
    (
        ("fixed_at", "invalid_at"),
        "case when invalid_at is not null then 'invalid' "
        "when fixed_at is not null then 'fixed' else 'open' end",
    ),
    (
        ("superseded_by",),
        "case when superseded_by is not null and superseded_by != '' "
        "then 'superseded' else 'active' end",
    ),
)


def _git_common_root(root: str) -> str | None:
    """The primary checkout of a linked worktree, from its `.git` pointer file."""
    try:
        with open(os.path.join(root, ".git"), encoding="utf-8") as fh:
            line = fh.readline().strip()
        if not line.startswith("gitdir:"):
            return None
        gitdir = line.split(":", 1)[1].strip()
        if not os.path.isabs(gitdir):
            gitdir = os.path.normpath(os.path.join(root, gitdir))
        common = gitdir
        try:
            with open(os.path.join(gitdir, "commondir"), encoding="utf-8") as fh:
                rel = fh.readline().strip()
            common = os.path.normpath(os.path.join(gitdir, rel))
        except OSError:
            pass
        return os.path.dirname(common)
    except OSError:
        return None


def newest_index(ddflow_dir: str) -> str | None:
    """The newest index file in ``ddflow_dir`` by mtime, or None."""
    best, best_t = None, -1.0
    try:
        with os.scandir(ddflow_dir) as it:
            for e in it:
                if not INDEX_NAME.match(e.name):
                    continue
                try:
                    t = e.stat().st_mtime
                except OSError:
                    continue
                if t > best_t:
                    best, best_t = e.path, t
    except OSError:
        return None
    return best


def find_index(start: str | None = None) -> str | None:
    """The index to read from ``start``: this checkout's, else the primary's.

    A worktree has its own `.ddflow/` (the committed events) but its index lives with the
    primary checkout, so the walk up from ``start`` is followed by the `.git` pointer.
    """
    here = os.path.abspath(start or os.getcwd())
    seen: list[str] = []
    cur = here
    while True:
        if os.path.isdir(os.path.join(cur, ".ddflow")):
            seen.append(cur)
            alt = _git_common_root(cur)
            if alt:
                seen.append(alt)
            break
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    for root in seen:
        found = newest_index(os.path.join(root, ".ddflow"))
        if found:
            return found
    return None


#: How long a locked index is waited for, in ms: a TAB must not hang behind a writer.
#: Settable as DDFLOW_COMPLETION_LOCK_MS (the config layer is too heavy to import here).
LOCK_MS_ENV, LOCK_MS_DEFAULT = "DDFLOW_COMPLETION_LOCK_MS", 30


def _lock_ms() -> int:
    try:
        return max(0, int(os.environ.get(LOCK_MS_ENV, LOCK_MS_DEFAULT)))
    except ValueError:
        return LOCK_MS_DEFAULT


def _connect(path: str) -> sqlite3.Connection:
    ms = _lock_ms()
    uri = "file:" + path.replace("%", "%25").replace("?", "%3f").replace("#", "%23")
    con = sqlite3.connect(uri + "?mode=ro", uri=True, timeout=ms / 1000)
    con.execute(f"pragma busy_timeout={ms}")
    return con


def _like(prefix: str) -> str:
    esc = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return esc + "%"


def _tables(con: sqlite3.Connection) -> dict[str, tuple[str, ...]]:
    """Id-bearing record tables of the index: name -> its columns."""
    rows = con.execute("select name, sql from sqlite_master where type='table'").fetchall()
    virtual = [n for n, s in rows if s and s.lstrip().upper().startswith("CREATE VIRTUAL")]
    out: dict[str, tuple[str, ...]] = {}
    for name, _sql in rows:
        if name.startswith("sqlite_") or any(name.startswith(v + "_") for v in virtual):
            continue
        if name in virtual:
            continue
        cols = tuple(r[1] for r in con.execute(f'pragma table_info("{name}")'))
        if "id" in cols:
            out[name] = cols
    return out


def _state_expr(cols: tuple[str, ...]) -> str | None:
    for c in STATE_COLUMNS:
        if c in cols:
            return f'"{c}"'
    for need, expr in _DERIVED:
        if all(c in cols for c in need):
            return expr
    return None


def _kind_matches(want: str, name: str) -> bool:
    """Whether the kind word ``want`` names table ``name``: either may be the plural."""
    return name in _forms(want) or want in _forms(name) or want in _singulars(name)


def _forms(word: str) -> set[str]:
    forms = {word, word + "s", word + "es"}
    if word.endswith("y"):
        forms.add(word[:-1] + "ies")
    return forms


def _singulars(name: str) -> set[str]:
    """Singulars a plural table name could stand for (memories -> memory, classes -> class)."""
    out = set()
    if name.endswith("ies"):
        out.add(name[:-3] + "y")
    if name.endswith("es"):
        out.add(name[:-2])
    if name.endswith("s"):
        out.add(name[:-1])
    return out


def _query_ids(con, prefix, kind, state, limit) -> list[str]:
    ids: list[str] = []
    like = _like(prefix)
    for name, cols in sorted(_tables(con).items()):
        # A table whose rows carry a `kind` column (items, records) is reached by those
        # values only: its own name is not a kind, so it never answers for all of them.
        where, args = ["id like ? escape '\\'"], [like]
        if "kind" in cols:
            if kind is not None:
                where.append("kind = ?")
                args.append(kind)
        elif kind is not None and not _kind_matches(kind, name):
            continue
        if state is not None:
            expr = _state_expr(cols)
            if expr is None:
                continue
            where.append(f"{expr} = ?")
            args.append(state)
        sql = f'select id from "{name}" where {" and ".join(where)} order by id limit ?'
        ids.extend(str(r[0]) for r in con.execute(sql, [*args, limit]) if r[0] is not None)
    return sorted(set(ids))[:limit]


def _guard(fn, index, *args):
    """Run ``fn(con, *args)`` against the index; [] on any failure at all."""
    try:
        path = index if index is not None else find_index()
        if not path:
            return []
        con = _connect(path)
        try:
            return fn(con, *args)
        finally:
            con.close()
    except Exception:
        return []


def complete_ids(prefix="", kind=None, state=None, *, index=None, limit=DEFAULT_LIMIT):
    """Ids starting with ``prefix``, optionally of one kind and state."""
    return _guard(_query_ids, index, str(prefix), kind or None, state or None, limit)


def kinds(*, index=None) -> list[str]:
    """Completable kind words: id tables, plus the kind values of items and records."""

    def run(con):
        names: set[str] = set()
        for name, cols in _tables(con).items():
            if "kind" not in cols:
                names.add(name)
            else:
                names.update(
                    str(r[0]) for r in con.execute(f'select distinct kind from "{name}"') if r[0]
                )
        return sorted(names)

    return _guard(run, index)


def states(*, index=None) -> list[str]:
    """Every state value any record table holds."""

    def run(con):
        out: set[str] = set()
        for name, cols in _tables(con).items():
            expr = _state_expr(cols)
            if expr:
                out.update(
                    str(r[0]) for r in con.execute(f'select distinct {expr} from "{name}"') if r[0]
                )
        return sorted(out)

    return _guard(run, index)


def phases(prefix="", *, index=None, limit=DEFAULT_LIMIT):
    """Phase ids (items of kind `phase`)."""
    return complete_ids(prefix, "phase", index=index, limit=limit)
