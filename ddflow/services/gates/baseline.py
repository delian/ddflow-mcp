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
from pathlib import Path
from typing import Any

from ...adapters.clock import DEFAULT as CLOCK
from ...core.digest import content_digest as digest
from ...infra import fsio
from ...infra.tomlcfg import atomic_write
from .. import testselect as TS

CACHE_DIR = Path(".ddflow") / "local" / "baseline"
KNOB = "baseline_red_auto"
#: Index of a cache entry's failure text (``[failed, measured_at, text]``; older entries omit it).
SIG_AT = 2


def _path(repo: Path, sha: str, command: str = "") -> Path:
    """One file per base sha AND gate command: another command measures other tests."""
    return Path(repo) / CACHE_DIR / f"{sha[:40]}.{digest(command)[:12]}.json"


def cache_load(
    repo: Path, sha: str, ttl_s: int, now: float | None = None, command: str = ""
) -> dict[str, bool]:
    """``{test id: failed on this base}`` measured so far and still younger than ``ttl_s``
    (each id has its own age); {} when none."""
    if ttl_s <= 0:
        return {}
    clock_now = CLOCK.wall() if now is None else now
    try:
        seen = json.loads(_path(repo, sha, command).read_text(encoding="utf-8"))["ids"]
        return {str(k): bool(v[0]) for k, v in seen.items() if _fresh(v, clock_now, ttl_s)}
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return {}


def cache_signatures(
    repo: Path, sha: str, ttl_s: int, now: float | None = None, command: str = ""
) -> dict[str, str]:
    """``{test id: failure text on this base}`` for ids measured after text was kept (an id
    without text -- an old entry -- is absent); {} when none. The text is what the branch's
    shown later compares against to spot a new offender (B-baseline-red-hides-new-offender-
    in-red-ratchet)."""
    if ttl_s <= 0:
        return {}
    clock_now = CLOCK.wall() if now is None else now
    try:
        seen = json.loads(_path(repo, sha, command).read_text(encoding="utf-8"))["ids"]
        out: dict[str, str] = {}
        for k, v in seen.items():
            if _fresh(v, clock_now, ttl_s) and len(v) > SIG_AT and v[SIG_AT]:
                out[str(k)] = str(v[SIG_AT])
        return out
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return {}


def _fresh(entry, now: float, ttl_s: int) -> bool:
    """Is a held ``[failed, measured_at]`` entry well formed and still within the ttl?"""
    try:
        return now - float(entry[1]) <= ttl_s
    except (TypeError, ValueError, IndexError, KeyError):
        return False


def cache_store(
    repo: Path,
    sha: str,
    results: dict[str, bool],
    ttl_s: int,
    command: str = "",
    sigs: dict[str, str] | None = None,
) -> None:
    """Merge ``results`` into the cache of ``sha`` and ``command``, each id stamped with the
    time it was measured (best effort: a failed write costs a rerun). ``sigs`` (id -> failure
    text) is stored beside the bool, so a later change keeps comparing against the text."""
    if ttl_s <= 0 or not results:
        return
    try:
        path = _path(repo, sha, command)
        try:
            held = json.loads(path.read_text(encoding="utf-8"))["ids"]
        except (OSError, ValueError, KeyError, TypeError):
            held = {}
        if not isinstance(held, dict):
            held = {}
        now = CLOCK.wall()
        merged = {
            **{k: v for k, v in held.items() if _fresh(v, now, ttl_s)},
            **{i: [f, now, (sigs or {}).get(i, "")] for i, f in results.items()},
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        fsio.ensure_ignored_dir(path.parent)
        atomic_write(path, json.dumps({"sha": sha, "ids": merged}))
    except (OSError, ValueError, IndexError, TypeError, KeyError, AttributeError):
        return


FULL_DIR = Path(".ddflow") / "local" / "ci-failing"
#: What the outcome evidence keeps of a long failing list; the whole list is in a file.
SAMPLE = 50


def write_full(repo: Path, sha: str, ids: list[str]) -> str:
    """Keep EVERY failing test id of a run on ``sha`` in a local file (the gate evidence holds
    only a capped sample): returns its path relative to ``repo``, "" when it could not be
    written. A large red suite spills here instead of being cut."""
    if not sha or not ids:
        return ""
    rel = FULL_DIR / f"{sha[:40]}.json"
    try:
        path = Path(repo) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        fsio.ensure_ignored_dir(path.parent)
        atomic_write(path, json.dumps({"sha": sha, "ids": list(dict.fromkeys(ids))}))
    except OSError:
        return ""
    return str(rel)


def read_full(repo: Path, sha: str) -> list[str]:
    """The whole failing list `write_full` kept for ``sha`` ([] when there is none)."""
    try:
        data = json.loads((Path(repo) / FULL_DIR / f"{sha[:40]}.json").read_text(encoding="utf-8"))
        ids = data["ids"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    return [str(i) for i in ids] if sha and data.get("sha") == sha and isinstance(ids, list) else []


def only_tests_failed(checks) -> bool:
    """Did the ci run fail ONLY in its tests? A failed lint, build or secrets check is no test
    failure, so a red that includes one is never baseline-red. A check counts as the tests when
    its name says so (the hook "unit tests + end-to-end scenarios") or it is the run's own exit."""
    return all(c.ok or c.id == "command" or "test" in c.id.lower() for c in checks)


def test_files_of(ids: list[str]) -> list[str]:
    """The test files of ``ids`` ("tests/test_a.py::T::t" -> "tests/test_a.py")."""
    return sorted({i.split("::", 1)[0] for i in ids})


def auto_decision(
    cfg, tree: Path, base: str, ids: list[str], found: dict[str, Any], limits: dict[str, int]
) -> dict[str, Any]:
    """``{"accepted": bool, "why": str, ...}``: may the baseline-red partial of ``ids`` be
    accepted without a person? Needs ``found["red"]`` (measured), the knob on, and no failing
    test in a file the item changed or whose import reaches a changed file. A test selected
    only because it DECLARES it governs the changed path (a whole-tree guard such as
    `tests/test_architecture_guards.py` with ``GOVERNS = ("ddflow/**", ...)``) is reached
    trivially -- it governs the whole tree, so its presence in the selection is not evidence
    this change caused its failure -- and blocks only when the guard file itself changed
    (B-baseline-reach-blocks-red-main-fix)."""
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
    changed = set(sel.changed)
    governs = {t.path for t in (*sel.tests, *sel.cut) if t.reason.startswith("governs ")}
    # A test file blocks when the item changed it, or when the selection reaches it because it
    # imports the changed code (a governed whole-tree guard is exempt -- see the docstring).
    reached = {t.path for t in (*sel.tests, *sel.cut)}
    inside = [f for f in test_files_of(ids) if f in changed or (f in reached and f not in governs)]
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
