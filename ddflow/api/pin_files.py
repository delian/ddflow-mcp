"""`pins regenerate`: the application-layer entry (DX-pins-regenerate)."""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..core import outcome as O
from ..infra import git as G
from ..services import pins as PN
from ..services import pins_merge as PM


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
) -> O.Outcome:
    """Re-record the stale pin files; with ``check`` list them (exit 1 when any)."""
    if verb == "merge-driver":
        code = PM.merge_driver(target)
        return O.ok("pins", driver=True) if code == 0 else O.failed("pins", "no file to keep")
    # The working tree the caller stands in, not the primary checkout `repo` resolves to: a
    # pin file is re-recorded where the code under test is.
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
    rep = PN.regenerate(repo, cfg, only=only, check=check)
    if rep.problem:
        return O.failed("pins", rep.problem)
    rows = [
        {
            "name": p.name,
            "files": list(p.files),
            "stale": p.stale,
            "regenerated": p.regenerated,
            "error": p.error,
        }
        for p in rep.pins
    ]
    if rep.failed:
        return O.failed("pins", "; ".join(f"{p.name}: {p.error}" for p in rep.failed), pins=rows)
    if check and rep.stale:
        return O.failed(
            "pins",
            "stale pins: "
            + ", ".join(p.name for p in rep.stale)
            + ". `ddflow pins regenerate` re-records them",
            pins=rows,
        )
    return O.ok("pins", pins=rows)
