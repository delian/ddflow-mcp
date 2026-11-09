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
from ...core import globs
from ...core.digest import content_digest as digest
from ...infra import fsio
from ...infra.tomlcfg import atomic_write
from .. import testselect as TS

CACHE_DIR = Path(".ddflow") / "local" / "baseline"
KNOB = "baseline_red_auto"
#: Index of a cache entry's failure text (``[failed, measured_at, text]``; older entries omit it).
SIG_AT = 2
#: Marker element stamped on entries whose failure text is kept WHOLE (not cut to
#: ``flakes.LINE_CHARS``): index 3 of a cache entry -- older entries omit it. A whole text of
#: exactly LINE_CHARS length is told apart from a legacy entry cut to that length only by this
#: marker (B60e9e09308: re-measure the legacy, never a genuine whole 240-char reason).
SIG_WHOLE_AT = 3
SIG_WHOLE = "whole"


def _path(repo: Path, sha: str, command: str = "") -> Path:
    """One file per base sha AND gate command: another command measures other tests."""
    return Path(repo) / CACHE_DIR / f"{sha[:40]}.{digest(command)[:12]}.json"


def _entries(
    repo: Path, sha: str, ttl_s: int, now: float | None = None, command: str = ""
) -> dict[str, list]:
    """One read of ``sha``/``command``'s cache: ``{test id: [failed, measured_at, text?, whole?]}``
    for entries still within ``ttl_s`` (each id its own age). A single parse so every view built
    from it agrees -- separate reads can interleave with a concurrent ``cache_store`` (B60e9e09308)."""
    if ttl_s <= 0:
        return {}
    clock_now = CLOCK.wall() if now is None else now
    try:
        seen = json.loads(_path(repo, sha, command).read_text(encoding="utf-8"))["ids"]
        return {str(k): v for k, v in seen.items() if _fresh(v, clock_now, ttl_s)}
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return {}


def cache_load(
    repo: Path, sha: str, ttl_s: int, now: float | None = None, command: str = ""
) -> dict[str, bool]:
    """``{test id: failed on this base}`` measured so far and still younger than ``ttl_s``
    (each id has its own age); {} when none."""
    return {k: bool(v[0]) for k, v in _entries(repo, sha, ttl_s, now, command).items()}


def cache_signatures(
    repo: Path, sha: str, ttl_s: int, now: float | None = None, command: str = ""
) -> dict[str, str]:
    """``{test id: failure text on this base}`` for ids measured after text was kept (an id
    without text -- an old entry -- is absent); {} when none. The text is what the branch's
    shown later compares against to spot a new offender (B-baseline-red-hides-new-offender-
    in-red-ratchet)."""
    return {
        k: str(v[SIG_AT])
        for k, v in _entries(repo, sha, ttl_s, now, command).items()
        if len(v) > SIG_AT and v[SIG_AT]
    }


def cache_whole(
    repo: Path, sha: str, ttl_s: int, now: float | None = None, command: str = ""
) -> dict[str, bool]:
    """``{test id: True}`` for ids whose cached failure text is marked WHOLE (``SIG_WHOLE``), so
    ``on_base`` trusts a whole text even when its length is exactly ``flakes.LINE_CHARS``; an
    entry WITHOUT the marker is the legacy cut-to-that-length shape and is re-measured instead
    (B60e9e09308). An id with no text is absent here, as in ``cache_signatures``."""
    return {
        k: True
        for k, v in _entries(repo, sha, ttl_s, now, command).items()
        if len(v) > SIG_WHOLE_AT and v[SIG_WHOLE_AT]
    }


def cache_whole(
    repo: Path, sha: str, ttl_s: int, now: float | None = None, command: str = ""
) -> dict[str, bool]:
    """``{test id: True}`` for ids whose cached failure text is marked WHOLE (``SIG_WHOLE``), so
    ``on_base`` trusts a whole text even when its length is exactly ``flakes.LINE_CHARS``; an
    entry WITHOUT the marker is the legacy cut-to-that-length shape and is re-measured instead
    (B60e9e09308). An id with no text is absent here, as in ``cache_signatures``."""
    return {
        k: True
        for k, v in _entries(repo, sha, ttl_s, now, command).items()
        if len(v) > SIG_WHOLE_AT and v[SIG_WHOLE_AT]
    }


def cache_snapshot(
    repo: Path, sha: str, ttl_s: int, now: float | None = None, command: str = ""
) -> tuple[dict[str, bool], dict[str, str], dict[str, bool]]:
    """``(known, sigs, whole)`` from ONE cache read, so the three never disagree when a concurrent
    ``cache_store`` rewrites the file between separate calls (B60e9e09308). ``known``: id ->
    failed on the base; ``sigs``: id -> whole failure text; ``whole``: id -> True for a text kept
    WHOLE."""
    entries = _entries(repo, sha, ttl_s, now, command)
    known = {k: bool(v[0]) for k, v in entries.items()}
    sigs = {k: str(v[SIG_AT]) for k, v in entries.items() if len(v) > SIG_AT and v[SIG_AT]}
    whole = {k: True for k, v in entries.items() if len(v) > SIG_WHOLE_AT and v[SIG_WHOLE_AT]}
    return known, sigs, whole


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
        merged = {k: v for k, v in held.items() if _fresh(v, now, ttl_s)}
        for i, f in results.items():
            sig = (sigs or {}).get(i, "")
            # The WHOLE marker is stamped on the text FULLY (only an entry WITHOUT it can still
            # be the legacy cut-to-LINE_CHARS shape): it is what tells "kept whole" apart from
            # "cut" when the whole text happens to be exactly LINE_CHARS long (B60e9e09308).
            merged[i] = [f, now, sig, SIG_WHOLE if sig else ""]
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


def _always_guard_files(cfg, files: list[str]) -> set[str]:
    """The failing files that are always-run guards (`[ci].always_tests`): they run for every
    item whatever the change reaches, so their presence in the selection is never evidence this
    change caused the failure (B-baseline-red-reach-counts-whole-tree-guards). Matching their
    DECLARED globs, not importing `ci_scope` (a module-level import would cycle through
    `gates`; a local one is a deferred import the architecture ratchet forbids)."""
    ci_globs = tuple(getattr(getattr(cfg, "ci", None), "always_tests", None) or ())
    return {f for f in files if any(globs.match(f, g) for g in ci_globs)}


def auto_decision(
    cfg, tree: Path, base: str, ids: list[str], found: dict[str, Any], limits: dict[str, int]
) -> dict[str, Any]:
    """``{"accepted": bool, "why": str, ...}``: may the baseline-red partial of ``ids`` be
    accepted without a person? Needs ``found["red"]`` (measured), the knob on, and no failing
    test in a file the item changed or whose import reaches a changed file. A test selected
    only because it DECLARES it governs the changed path (a whole-tree guard such as
    `tests/test_architecture_guards.py` with ``GOVERNS = ("ddflow/**", ...)``) is reached
    trivially -- it governs the whole tree, so its presence in the selection is not itself
    evidence this change caused its failure. Same for an always-run guard (`[ci].always_tests`):
    it runs for every item, so reach there is trivial too. Such a guard
    blocks only when the guard file
    itself changed; the guard's own violation sites are compared against the base separately
    in `baseline_red` (a new offender is already not ``red``), so the reach check never has to
    look inside its output (B-baseline-reach-blocks-red-main-fix, B-baseline-red-reach-counts-
    whole-tree-guards)."""
    out: dict[str, Any] = {"accepted": False, "ids": list(ids)}
    if getattr(cfg.gates, KNOB) != "on":
        return {**out, "why": f"[gates].{KNOB} is off: a person accepts it"}
    if found.get("red") is not True:
        return {**out, "why": "not measured baseline-red"}
    sel = TS.select(tree, base, **limits)
    if sel is None:
        return {**out, "why": "git could not say what the item changed, so none is excluded"}
    if sel.full:
        # A hub-module (or test-infrastructure) change cut the reach walk, so which test files
        # it would reach by import or governing cannot be told. What IS still known is the
        # changed-file list (`sel.changed`) and the measurement itself: `red` (checked above)
        # already means every failing test was measured on the base, so `unmeasured` is empty
        # and only two guards remain real -- the comparison was whole-vs-whole (`uncompared`
        # empty: a missing branch or base text is an absence of evidence, not a match) and no
        # failing test FILE is itself changed. Reach is then only needed to attribute a failure
        # to a changed file (B0f41a2f1d6).
        changed = set(sel.changed)
        uncompared = found.get("uncompared")
        # `uncompared` is always set on the one path that yields `red` (`_tally`), so an ABSENT
        # list must decline rather than read as "all compared" (B0f41a2f1d6).
        if (
            uncompared is not None
            and not uncompared
            and not [f for f in test_files_of(ids) if f in changed]
        ):
            return {
                **out,
                "accepted": True,
                "why": "every failing test was measured on the base "
                f"(a hub cut the reach walk; nothing is left to attribute): {sel.full}",
            }
        return {**out, "why": f"the import reach cannot be told ({sel.full})"}
    changed = set(sel.changed)
    governs = {t.path for t in (*sel.tests, *sel.cut) if t.reason.startswith("governs ")}
    reached = {t.path for t in (*sel.tests, *sel.cut)}
    files = test_files_of(ids)
    excluded = governs | _always_guard_files(cfg, files)
    # The non-full path has the reach guard the `full` branch lacks, so it does NOT also consult
    # `uncompared`: a failing test whose text was missing (an indeterminate "new offender") is
    # still declined here when the change reaches it, and an unreached one cannot be caused by
    # the change. The `full` branch above substitutes `uncompared` for that missing reach guard.
    inside = [f for f in files if f in changed or (f in reached and f not in excluded)]
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
