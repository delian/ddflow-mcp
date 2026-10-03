"""List viewers over the folded state (read-only): one Outcome per request."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import outcome as O
from ..services import search as S
from ..services import session_view as SV
from ..services import viewers as V
from ._base import _load


def view_list(
    repo: Path,
    kind: str,
    *,
    state: str = "",
    phase: str = "",
    tag: str = "",
    agent: str = "",
    since: str = "",
    limit: int = V.DEFAULT_LIMIT,
) -> O.Outcome:
    """Filtered, bounded rows of `kind` (task|phase|bug|research|session)."""
    _log, cfg, st = _load(repo)
    try:
        view = V.list_view(
            st, cfg, kind, state=state, phase=phase, tag=tag, agent=agent, since=since, limit=limit
        )
    except V.ViewError as exc:
        return O.refused("view.list", str(exc), record_kind=kind)
    data: dict[str, Any] = {
        "record_kind": view.kind,
        "rows": view.rows,
        "total": view.total,
        "shown": len(view.rows),
        "limit": view.limit,
        "truncated": view.truncated,
        "filters": view.filters,
    }
    if not view.rows:
        which = f" matching {view.filters}" if view.filters else ""
        return O.nothing("view.list", f"No {kind} records{which}.", **data)
    return O.ok("view.list", **data)


def phase_progress(st: Any, phase_id: str) -> tuple[int, int]:
    """(done, total) of the live tasks below a phase."""
    below = [
        st.items[i]
        for i in st.descendants(phase_id)
        if i in st.items and st.items[i].kind == "task" and not st.items[i].removed
    ]
    return sum(1 for t in below if t.state == "done"), len(below)


#: What `view_read` answers: the list kinds, plus a text search.
READ_KINDS = (*V.KINDS[:4], "session", "search")


def view_read(  # noqa: PLR0913 -- one tool carries every viewer filter
    repo: Path,
    kind: str,
    *,
    id: str = "",
    query: str = "",
    state: str = "",
    phase: str = "",
    tag: str = "",
    owner: str = "",
    since: str = "",
    limit: int = V.DEFAULT_LIMIT,
    all: bool = False,
    mode: str = "ranked",
    sources: str = "",
) -> O.Outcome:
    """The one read over every viewer (task|phase|bug|research|session|search): the same
    rows and shape as the CLI's `list` / `session list|show` / `search` with `--json`.
    `id` (session only) is one session in full; `query` is what `search` looks for."""
    if kind not in READ_KINDS:
        return O.refused(
            "view.read", f"unknown kind {kind!r}: one of {', '.join(READ_KINDS)}", record_kind=kind
        )
    if kind == "session":
        return _read_session(repo, id, state, owner, since, limit)
    if kind == "search":
        return _read_search(repo, query, mode, sources, state, phase, owner, since, limit)
    if id:
        return O.refused("view.read", f"id applies to kind=session, not {kind}", record_kind=kind)
    if kind == "bug" and not state and not all:
        state = "open"
    out = view_list(
        repo, kind, state=state, phase=phase, tag=tag, agent=owner, since=since, limit=limit
    )
    if kind == "phase" and "rows" in out.data:
        _log, _cfg, st = _load(repo)
        for r in out.data["rows"]:
            r["done"], r["total"] = phase_progress(st, r["id"])
    return out


def _filters(given: dict[str, str]) -> dict[str, str]:
    """Echoed under the flag's own name: replaying `agent` would be identity."""
    return {("owner" if k == "agent" else k): v for k, v in given.items()}


def _read_session(
    repo: Path, sid: str, state: str, owner: str, since: str, limit: int
) -> O.Outcome:
    log, cfg, _st = _load(repo)
    try:
        if sid:
            return O.ok(
                "view.session", record_kind="session", **SV.show_session(log.read_all(), cfg, sid)
            )
        view = SV.list_sessions(
            log.read_all(), cfg, state=state, agent=owner, since=since, limit=limit
        )
    except SV.SessionViewError as exc:
        return O.refused("view.session", str(exc), record_kind="session")
    data: dict[str, Any] = {
        "record_kind": "session",
        "rows": view.rows,
        "total": view.total,
        "shown": len(view.rows),
        "limit": view.limit,
        "truncated": view.truncated,
        "filters": _filters(view.filters),
    }
    if not view.rows:
        return O.nothing(
            "view.session",
            "No sessions" + (f" matching {data['filters']}." if view.filters else "."),
            **data,
        )
    return O.ok("view.session", **data)


def _read_search(
    repo: Path,
    query: str,
    mode: str,
    sources: str,
    state: str,
    phase: str,
    owner: str,
    since: str,
    limit: int,
) -> O.Outcome:
    log, cfg, st = _load(repo)
    try:
        res = S.search(
            st,
            log.read_all(),
            cfg,
            query,
            S.Filters(sources, state, phase, owner, since),
            mode=mode,
            limit=limit,
        )
    except S.SearchError as exc:
        return O.refused("view.search", str(exc), record_kind="search")
    data: dict[str, Any] = {
        "record_kind": "search",
        "query": res.query,
        "mode": res.mode,
        "rows": res.rows,
        "total": res.total,
        "shown": len(res.rows),
        "limit": res.limit,
        "truncated": res.truncated,
        "searched": res.searched,
        "filters": _filters(res.filters),
        "note": res.note,
    }
    if not res.rows:
        return O.nothing(
            "view.search",
            f"No matches for {res.query!r} ({res.mode}); searched {res.searched} records.",
            **data,
        )
    return O.ok("view.search", **data)
