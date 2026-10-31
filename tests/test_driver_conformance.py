"""Driver conformance, offline, over every descriptor: the engines (E1 hooks, E4 argv, E5
stream), the Lane contract against a fake agent, and the facts overlay.

Extends ``harness_conformance`` (the cases module and ``test_harness_conformance.py`` stay the
one conformance suite); a new descriptor is picked up with no edit. A declared check with no
fixture is SKIPPED as unverified, never passed, and the ratchet fails when a gap is not in
``harness_conformance/gaps.json``. No network, no installed agent, no keys.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness_conformance import cases as C
from harness_conformance import declared as D
from harness_conformance import engines as E
from harness_conformance import report as R

from ddflow.services import facts, harnessfacts
from ddflow.services import harnessreg as HR

ENGINE_CHECKS = [c for c in D.all_checks() if c[1] != D.HOOK]


def _id(c):
    return "-".join(x for x in c if x)


@pytest.mark.parametrize("check", ENGINE_CHECKS, ids=_id)
def test_a_declared_engine_check_passes_against_its_fixture(check):
    hid, kind, _ = check
    fx = D.load(hid, kind)
    if fx is None:
        pytest.skip(f"{hid}: {kind} unverified (no fixture under {D.path_for(hid, kind)})")
    if kind == D.ARGV:
        E.check_argv(hid, fx)
    else:
        E.check_stream(hid, fx, D.path_for(hid, kind))


@pytest.mark.parametrize("check", D.all_checks(), ids=_id)
def test_a_fixture_is_labelled_honestly(check):
    hid, kind, name = check
    fx = D.load(hid, kind, name)
    if fx is None:
        pytest.skip(f"{hid}: {kind} {name} unverified")
    assert isinstance(fx.get("_meta"), dict), (
        f"{hid}: a fixture carries _meta (recorded, synthetic, source, version)"
    )
    meta = fx["_meta"]
    assert isinstance(meta["recorded"], bool)
    assert meta.get("synthetic", False) is (not meta["recorded"]), "recorded=false means synthetic"
    assert meta["source"] and meta["version"]
    if meta["recorded"]:
        assert meta["version"] != "unversioned", "a recording says which version it is of"


def test_every_fixture_on_disk_belongs_to_a_declared_check():
    """A fixture for something the descriptor does not declare is dead data or a typo."""
    declared = {D.path_for(h, k, n) for h, k, n in D.all_checks()}
    on_disk = set(D.FIXTURES.glob("*/*.json")) | set(D.FIXTURES.glob("*/engines/*.json"))
    stray = sorted(str(p.relative_to(D.FIXTURES)) for p in on_disk - declared)
    # hook fixtures of non-command-hooked agents are not expected either
    assert not stray, f"fixtures no descriptor declares: {stray}"


@pytest.mark.parametrize("hid", C.ALL)
def test_the_facts_overlay_is_total_and_well_formed(hid):
    h = HR.get(hid)
    for r in harnessfacts.rows(h, absent=True):
        assert r["status"] in facts.STATUSES, (hid, r["fact"])
    for key, node in h.facts.items():
        assert not facts.problems(f"{hid}.{key}", node), (hid, key)


def test_the_gap_set_only_shrinks():
    """A descriptor lacking a fixture for a declared check fails here until it has one (or the
    baseline is knowingly regenerated); a gap that was closed must leave the baseline."""
    now, known = set(R.gaps()), set(R.baseline())
    assert not now - known, (
        f"new fixture gaps (record them or add the fixture): {sorted(now - known)}"
    )
    assert not known - now, f"closed gaps still in gaps.json (regenerate): {sorted(known - now)}"


def test_the_report_names_every_agent_and_the_zero_python_rate(capsys):
    text = R.render()
    assert "zero-Python agents:" in text
    z = R.zero_python()
    assert z["agents"] == len(C.ALL) and 0 <= z["zero_python"] <= z["agents"]
    assert set(z["bespoke"]) <= set(C.ALL)
    for hid, _k, _n in D.all_checks():
        assert f"| {hid} |" in text, hid  # a row of its own, not a stray mention
