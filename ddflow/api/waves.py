"""`ddflow waves`: print the dependency waves, stamp them, check them, and keep them stamped.

The derivation is `services/waves.py`; the tag writes are `api/batch.apply` (the engine of
`ddflow update --add-tags/--remove-tags`), so a restamp is ONE locked transaction, only for
the items whose wave changed, and idempotent (D-wave-semantics).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core import outcome as O
from ..services import waves as W
from . import batch
from ._base import _load

KIND = "waves.report"


def _entries(cfg) -> Callable[[Any], list[dict]]:
    """The batch entries for the state the batch is planned from (the locked one)."""
    return lambda st: W.plan(st, cfg).edits


def _by_wave(pl: W.Plan, st) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for iid, w in pl.waves.items():
        if w is not None:
            out.setdefault(w, []).append(iid)
    for ids in out.values():
        ids.sort(key=lambda i: (st.items[i].priority, i))
    return dict(sorted(out.items()))


def report(repo: Path, args: dict, agent: str = "") -> O.Outcome:
    """The waves, plus `--stamp` (write them) or `--check` (exit 1 when stale)."""
    _log, cfg, st = _load(repo, agent)
    pl = W.plan(st, cfg)
    stamp, check = bool(args.get("stamp")), bool(args.get("check"))
    if stamp and check:
        return O.failed(KIND, "give --stamp or --check, not both")
    data: dict[str, Any] = {
        "waves": {str(n): ids for n, ids in _by_wave(pl, st).items()},
        "titles": {i: st.items[i].title[:100] for ids in _by_wave(pl, st).values() for i in ids},
        "cyclic": pl.cyclic,
        "stale": len(pl.edits),
        "stamped": 0,
        "limit": int(args.get("limit") or 0),
    }
    if check and pl.edits:
        return O.Outcome(
            kind=KIND,
            data=data,
            exit=O.FAIL,
            reason=f"{len(pl.edits)} item(s) carry a stale or missing wave:N stamp; "
            "`ddflow waves --stamp` rewrites them",
        )
    if stamp and pl.edits:
        out = batch.apply(repo, _entries(cfg), agent=agent, kind=KIND)
        if out.exit != O.OK:
            return out
        data["stamped"] = len(out.data["edits"])
        data["stale"] = 0
    return O.ok(KIND, **data)


def text(out, a) -> str:
    d = out.data
    lines = []
    for n, ids in d["waves"].items():
        shown = ids[: d["limit"]] if d["limit"] else ids
        lines.append(f"wave {n}: {len(ids)} item(s)")
        lines += [f"  {i}  {d['titles'][i]}" for i in shown]
        if len(shown) < len(ids):
            lines.append(f"  ... {len(ids) - len(shown)} more (--limit 0 lists all)")
    if d["cyclic"]:
        lines.append(f"no wave (on or behind a dependency cycle): {', '.join(d['cyclic'])}")
    lines.append(
        f"stamped {d['stamped']} item(s)" if d["stamped"] else f"{d['stale']} stale stamp(s)"
    )
    return "\n".join(lines)


# -- stamping by default (the around-a-command guard is services/waves_auto.py) ----------
def restamp(repo: Path, agent: str = "") -> int:
    """Stamp the stale items now; the number of tag events written."""
    _log, cfg, st = _load(repo, agent)
    if not W.plan(st, cfg).edits:
        return 0
    out = batch.apply(repo, _entries(cfg), agent=agent, kind=KIND)
    return len(out.data["edits"]) if out.exit == O.OK and out.data else 0
