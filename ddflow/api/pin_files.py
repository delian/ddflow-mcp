"""`pins regenerate`: the application-layer entry (DX-pins-regenerate)."""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..core import outcome as O
from ..infra import git as G
from ..services import leasekeep as LK
from ..services import pins as PN
from ..services import pins_merge as PM
from ._base import _load


def pins_regenerate(
    repo: Path,
    *,
    only: str = "",
    check: bool = False,
    install: bool = False,
    after_merge: bool = False,
    verb: str = "regenerate",
    target: str = "",
    start: Path | None = None,
    agent: str = "",
    on_progress=None,
) -> O.Outcome:
    """Re-record the stale pin files; with ``check`` list them (exit 1 when any)."""
    if verb == "merge-driver":
        code = PM.merge_driver(target)
        return O.ok("pins", driver=True) if code == 0 else O.failed("pins", "no file to keep")
    # The working tree the caller stands in, not the primary checkout `repo` resolves to: a
    # pin file is re-recorded where the code under test is.
    primary = repo  # the event log lives here; `repo` becomes the caller's own tree below
    top = G.run(start, "rev-parse", "--show-toplevel") if start else None
    if top is not None and top.ok and top.out.strip():
        repo = Path(top.out.strip())
    cfg = Config.load(repo)
    if install:
        actions = PM.install(repo, cfg)
        if not actions:
            return O.nothing("pins", "nothing to install: no pin recipes, or already installed")
        return O.ok("pins", installed=actions)
    if after_merge:
        note = PM.after_merge(repo, cfg)
        return O.ok("pins", note=note)
    tick = _lease_tick(primary, agent)
    rep = PN.regenerate(repo, cfg, only=only, check=check, tick=tick, on_progress=on_progress)
    if rep.problem:
        return O.failed("pins", rep.problem)
    return _regenerate_outcome(rep, check)


def _regenerate_outcome(rep, check: bool) -> O.Outcome:
    """The outcome of a run: failed when a recipe failed (or, with ``check``, is stale); nothing
    (exit 2, could not run) when a recipe could not finish; ok otherwise."""
    rows = [
        {
            "name": p.name,
            "files": list(p.files),
            "stale": p.stale,
            "regenerated": p.regenerated,
            "error": p.error,
            **({"unavailable": p.unavailable} if p.unavailable else {}),
        }
        for p in rep.pins
    ]
    extra = {"resolved": rep.resolved} if rep.resolved else {}
    if rep.failed:
        return O.failed(
            "pins", "; ".join(f"{p.name}: {p.error}" for p in rep.failed), pins=rows, **extra
        )
    if check and rep.stale:
        return O.failed(
            "pins",
            "stale pins: "
            + ", ".join(p.name for p in rep.stale)
            + ". `ddflow pins regenerate` re-records them",
            pins=rows,
        )
    if rep.unavailable:
        why = "; ".join(f"{p.name}: {p.unavailable}" for p in rep.unavailable)
        return O.nothing("pins", f"could not finish: {why}", pins=rows, **extra)
    return O.ok("pins", pins=rows, **extra)


def _lease_tick(repo: Path, agent: str):
    """(a tick renewing every live lease the caller holds, its interval) while the recipes
    run, or None: regenerating pins runs the whole snapshot suite, minutes long."""
    try:
        log, cfg, st = _load(repo, agent)
    except Exception:
        return None
    tick, every = LK.for_holder(log, cfg, st)
    return (tick, every) if tick is not None else None
