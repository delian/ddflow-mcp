"""Architecture guards as ratchets (D-unify 4: strangler with ratchets).

P-unify replaces many ad-hoc ways of doing one thing with one shared interface each. A
rule that only says "use the shared interface" decays the way the layer rule did before
tests/test_layering.py. So every pattern being retired is COUNTED here, the count is
compared with a committed baseline, and the baseline can only go down:

- a count ABOVE its baseline fails: new code used the old pattern. Use the shared
  interface instead (the failure names every site, so the new one is in the list);
- a count BELOW its baseline fails too, and says the number to write: the change that
  removed the sites lowers the baseline in the same commit, so nothing can quietly
  grow back into the room it left.

The import rules (layers, surfaces through api, one home each for subprocess, tempfile,
fcntl, hashlib and the optional extras) live in `.importlinter` and are checked by
import-linter here; their allowlists are ratchets of the same kind.

Every baseline is a file of its own under tests/guard_baselines/. A counter that finds
its sites module by module (every pattern counter, and complexity) keeps a directory,
`<counter>/<dotted module>.toml`, holding that module's number: a task that removes the
sites of `ddflow.services.enforce` lowers -- and declares in its globs -- only
`<counter>/ddflow.services.enforce.toml`, so lanes working in different modules never
touch the same file. A module with no sites has no file (a file reaching zero is deleted).
A counter whose last module reaches zero has no directory at all (git tracks no empty
directory), so absence means no baselines.
`<counter>.toml` holds the number of the other counters (`unreferenced_functions.toml`
also the functions kept unreferenced on purpose), and `importlinter-<contract>.toml` one
contract's `ignore_imports` allowlist, which this module joins back into the contract
before import-linter runs (for surfaces-through-api, subprocess-home and fsio-home a
directory `importlinter-<contract>/<dotted source module>.toml`, one file per module,
deleted when the module reaches zero; a "home" entry stays in its module's file). This module changes only when a guard is added or what it
measures changes.

Counted on the source with `ast`, never by running anything, so the guard is the same on
every platform and takes seconds. Complexity is radon's grade; duplicate code is pylint's
similarity checker with the settings pinned below. Both are dev dependencies: a missing
one SKIPS its test with the reason (it is never counted as zero).
"""

from __future__ import annotations

import pytest
from architecture_guards_support import (
    COUNTERS,
    PER_MODULE,
    ROOT,
    _baseline,
    _baseline_path,
    _hint,
    _measure,
    _module_baselines,
    _ratchet_problems,
)

#: What this checks, read by `ddflow tests --item` (B2a1eaa259e): a change under one of
#: these paths selects this test, even where nothing it imports changed.
GOVERNS = ("ddflow/**", ".importlinter", "tests/guard_baselines/**")


@pytest.mark.parametrize("kind", sorted(COUNTERS))
def test_ratchet(kind: str) -> None:
    sites = _measure(kind)
    if kind in PER_MODULE:
        above, below = _ratchet_problems(kind, sites, _module_baselines(kind))
        assert not above, "\n".join(above)
        assert not below, "\n".join(below)
        return
    baseline = _baseline(kind)
    count = len(sites)
    assert count <= baseline, (
        f"{kind}: {count} sites, the baseline is {baseline}. New code used a pattern "
        f"{_hint(kind)} Every current site:\n  " + "\n  ".join(sites)
    )
    assert count >= baseline, (
        f"{kind}: {count} sites, below the baseline of {baseline}. Good -- now lower it: "
        f"set baseline = {count} in {_baseline_path(kind).relative_to(ROOT)} in this "
        "same change, so the sites cannot grow back."
    )
