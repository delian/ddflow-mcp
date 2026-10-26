"""The bug list's knowledge-level facets: severity, the fix-task edge, severity order."""

from __future__ import annotations

from pathlib import Path

from ...core import outcome as O
from .._base import _load
from ..viewers import view_list

BUG_SEVERITIES = ("low", "medium", "high", "critical")
#: Where each declared severity ranks (0 first); anything else -- "" for a bug filed
#: without one -- ranks after every declared severity (`severity_rank`).
SEVERITY_RANK = {sev: i for i, sev in enumerate(reversed(BUG_SEVERITIES))}


def severity_rank(severity: str) -> int:
    """0 first: `critical`. A severity that is not one of `BUG_SEVERITIES` -- an empty
    one included -- ranks after every declared severity."""
    return SEVERITY_RANK.get((severity or "").strip().lower(), len(SEVERITY_RANK))


def _whole_bug_set(repo: Path, filters: dict) -> tuple[list[dict], dict]:
    """Every matched bug row, past the engine's cap: `(rows, dict)` -- rows is the whole
    set, dict is the first page's base data (`total` from there is the whole match
    count). The read surfaces echo `agent` under the flag's own name `owner`
    (`api/viewers._filters`); the engine kwarg is `agent`, so the MCP path's `owner` in
    `filters` must map back, or the re-fetch happens with a kwarg `view_list` does not
    take."""
    filters = {
        ("agent" if k == "owner" and "agent" not in filters else k): v
        for k, v in dict(filters).items()
    }
    fresh = view_list(repo, "bug", **filters, limit=0, offset=0)
    full = list(fresh.data["rows"])
    # The engine normalizes `limit=0` to its cap before the rows come back (a
    # successful page's limit is never 0), so one call tops out at the cap while `total`
    # keeps the whole match count: page the rest at the stride the engine advertised, so
    # an offset at or past the cap is not an unreachable empty page `next` refuses to
    # move off.
    stride = fresh.data["limit"]
    while len(full) < fresh.data["total"]:
        more = view_list(repo, "bug", **filters, limit=stride, offset=len(full))
        chunk = list(more.data["rows"])
        if not chunk:
            break  # said the cap, gave less: what it gave stands
        full.extend(chunk)
    return full, dict(fresh.data)


def finish_bug_list(repo: Path, out: O.Outcome, sort: str = "") -> O.Outcome:
    """The bug facets the shared list engine defers to the domain (`services/viewers.py`
    renders the row's id/state/title/owner/updated and the newest-first page): severity,
    the fix-task edge and that task's dependency edges. One function, so the CLI
    (`viewers_lists.cmd_list`) and the MCP `ddflow_list` shaping (`surfaces/mcp.py`) can
    never drift on which fields a bug row carries.

    ``sort`` is "" (the engine's own order, newest change first) or "severity": stable
    severity order -- `severity_rank`, id ascending as the tiebreak -- with the page
    re-cut from the whole matched set, fetched at the engine's cap in cap-sized pages,
    so the order decides the page, not the page the order. Any other word raises
    `ValueError`: an order the surface does not declare is refused, never silently
    dropped."""

    data = out.data
    sort = (sort or "").strip().lower()
    if sort and data.get("record_kind") != "bug":
        # The same refusal the CLI parser gives every other kind (--sort is a bug-only
        # selector there): MCP declares one schema for the whole ddflow_list tool, so the
        # runtime check is the one place both surfaces can share.
        raise ValueError(
            f"sort takes 'severity' for kind=bug only, not {data.get('record_kind')!r}"
        )
    if data.get("record_kind") != "bug":
        return out
    if sort not in ("", "severity"):
        raise ValueError(f"sort takes 'severity' (default: newest change first), got {sort!r}")
    _log, _cfg, st = _load(repo)
    if sort == "severity" and out.exit == 0:
        # The page the engine already cut was newest-first: re-cut it from the whole
        # matched set, in the order asked. A refused or "nothing" call keeps its
        # refusal; the re-cut is asked again with arguments the earlier call already
        # validated, so it is refused again only as the earlier call was.
        full, basis = _whole_bug_set(repo, dict(data["filters"]))
        for r in full:
            _row_edges(r, st)
        full.sort(key=lambda r: r["id"])
        full.sort(key=lambda r: severity_rank(r.get("severity") or ""))
        off, lim = data["offset"], data["limit"]
        # The `not lim` arm keeps the whole-set meaning of 0 honest if a 0 ever did
        # arrive on a caller page: the page is everything, not `rows[off:off]`, and
        # the caller's page size stays the advertised limit.
        page = full[off:] if not lim else full[off : off + lim]
        base = dict(basis)
        # The whole set was asked at offset 0; the page the caller asked for
        # starts where the caller told the engine to start, and its advertised limit is
        # the caller's page size, not the whole-set fetch's.
        base["offset"] = off
        base["limit"] = lim
        base["rows"] = page
        base["shown"] = len(base["rows"])
        base["truncated"] = base["total"] > off + base["shown"]
        base["next"] = off + base["shown"] if base["truncated"] else None
        return O.ok("bug", **base)
    if out.exit == 0:
        for r in data.get("rows") or []:
            _row_edges(r, st)
    return out


def _row_edges(row: dict, st) -> None:
    """Attach the knowledge-level bug facets to an engine row in place: severity, the
    fix-task edge and that task's dependency edges. A row the fold knows no bug for
    keeps the fields empty (the two surfaces must see the same keys)."""
    b = st.bugs.get(row.get("id") or "")
    row["severity"] = b.severity if b is not None else ""
    row["fix_task"] = b.fix_task if b is not None else ""
    fix = st.items.get(row["fix_task"]) if row["fix_task"] else None
    row["needs"] = list(fix.needs) if fix is not None else []
