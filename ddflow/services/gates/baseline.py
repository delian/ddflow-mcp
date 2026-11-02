"""The base branch's own failures, remembered per base sha, and the automatic acceptance of a
partial that rests on them (D-baseline-red-auto-accept).

`baseline_red.on_base` is the one comparer: it runs the failing ids on the base. This module
adds what makes it cheap and decides what follows from it:

* a CACHE of what was measured on one base commit (`.ddflow/local/baseline/<sha>.json`, local,
  shared by every item of this checkout; a new base has a new sha, so it expires by itself, and
  `[gates].baseline_cache_ttl_s` bounds it in time as well). An id already measured costs a
  lookup; an id never measured is run once on a scratch checkout of the base;
* `auto`: the partial is accepted without a person only when every failing id failed on the
  base AND no failing test is in a file the item changed or reaches by import. A selection
  that cannot be made (a hub changed, git cannot say) never accepts.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ...infra import fsio
from ...infra.tomlcfg import atomic_write
from .. import testselect as TS

CACHE_DIR = Path(".ddflow") / "local" / "baseline"
KNOB = "baseline_red_auto"


def _path(repo: Path, sha: str) -> Path:
    return Path(repo) / CACHE_DIR / f"{sha[:40]}.json"


def load(repo: Path, sha: str, ttl_s: int, now: float | None = None) -> dict[str, bool]:
    """``{test id: failed on this base}`` measured so far; {} when none or older than ``ttl_s``."""
    if ttl_s <= 0:
        return {}
    try:
        data = json.loads(_path(repo, sha).read_text(encoding="utf-8"))
        seen = data["ids"]
        fresh = (time.time() if now is None else now) - float(data["at"]) <= ttl_s
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    return {str(k): bool(v) for k, v in seen.items()} if fresh and isinstance(seen, dict) else {}


def store(repo: Path, sha: str, results: dict[str, bool], ttl_s: int) -> None:
    """Merge ``results`` into the cache of ``sha`` (best effort: a failed write costs a rerun)."""
    if ttl_s <= 0 or not results:
        return
    try:
        merged = {**load(repo, sha, ttl_s), **results}
        path = _path(repo, sha)
        path.parent.mkdir(parents=True, exist_ok=True)
        fsio.ensure_ignored_dir(path.parent)
        atomic_write(path, json.dumps({"sha": sha, "at": time.time(), "ids": merged}))
    except OSError:
        return


def failing_files(ids: list[str]) -> list[str]:
    """The test files of ``ids`` ("tests/test_a.py::T::t" -> "tests/test_a.py")."""
    return sorted({i.split("::", 1)[0] for i in ids})


def auto(
    cfg, tree: Path, base: str, ids: list[str], found: dict[str, Any], limits: dict[str, int]
) -> dict[str, Any]:
    """``{"accepted": bool, "why": str, ...}``: may the baseline-red partial of ``ids`` be
    accepted without a person? Needs ``found["red"]`` (measured), the knob on, and no failing
    test in a file the item changed or whose import reaches a changed file."""
    out: dict[str, Any] = {"accepted": False, "ids": list(ids)}
    if getattr(cfg.gates, KNOB) != "on":
        return {**out, "why": f"[gates].{KNOB} is off: a person accepts it"}
    if found.get("red") is not True:
        return {**out, "why": "not measured baseline-red"}
    sel = TS.select(tree, base, **limits)
    if sel is None:
        return {**out, "why": "git could not say what the item changed, so none is excluded"}
    if sel.full:
        return {**out, "why": f"the import reach cannot be told ({sel.full})"}
    reached = {t.path for t in (*sel.tests, *sel.cut)} | set(sel.changed)
    inside = [f for f in failing_files(ids) if f in reached]
    if inside:
        return {
            **out,
            "why": f"failing test file(s) the item changed or reaches: {', '.join(inside)}",
        }
    return {
        **out,
        "accepted": True,
        "why": "every failing test fails on the base and none is in the item's reach",
    }
