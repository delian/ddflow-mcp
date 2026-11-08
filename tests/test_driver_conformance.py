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

from ddflow.services import driver_conformance as DC
from ddflow.services import harnessreg as HR
from ddflow.services import hookio

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
    # The per-descriptor checks (facts overlay, hook plan) live in the package `run`;
    # this test calls it rather than keeping a second copy of the loop.
    result = DC.run(h)
    assert result["checks"]["facts"]["status"] == "ok", (hid, result)


def test_run_reports_failed_when_a_command_hooked_agent_has_no_plan(monkeypatch):
    from types import SimpleNamespace

    from ddflow.services import harnessfacts

    h = SimpleNamespace(
        id="claude",
        facts={},
        hooks=SimpleNamespace(
            style="claude",  # a COMMAND_HOOK_STYLE
            normalizer="bogus",  # no such NORMALIZERS key
            emitter="claude",
            inject=(),
            events={},
        ),
    )
    monkeypatch.setattr(harnessfacts, "rows", lambda _h, **k: [])
    result = DC.run(h)
    assert result["status"] == "failed", result
    assert result["checks"]["hooks"]["status"] == "failed", result


def test_run_resolves_the_hook_plan_from_the_given_descriptor(monkeypatch):
    """run() builds the hook plan from the descriptor handed to it, not by re-fetching the
    id: `plan_for(id)` reads only the shipped registry, so a local descriptor not shipped
    there must still resolve to a passing hooks check."""
    from types import SimpleNamespace

    from ddflow.services import harnessfacts

    h = SimpleNamespace(
        id="not-a-shipped-harness",
        facts={},
        hooks=SimpleNamespace(
            style="claude",
            normalizer="claude",
            emitter="claude",
            inject=(),
            events={},
        ),
    )
    monkeypatch.setattr(harnessfacts, "rows", lambda _h, **k: [])
    result = DC.run(h)
    assert result["checks"]["hooks"]["status"] == "ok", result


def test_run_reports_failed_when_a_fact_has_an_unknown_status(monkeypatch):
    from ddflow.services import harnessfacts

    h = HR.get("claude")
    monkeypatch.setattr(harnessfacts, "rows", lambda _h, **k: [{"fact": "who", "status": "bogus"}])
    result = DC.run(h)
    assert result["status"] == "failed", result
    assert result["checks"]["facts"]["status"] == "failed", result


def test_run_reports_failed_when_a_fact_node_is_malformed(monkeypatch):
    """A malformed fact NODE trips the `facts.problems` path of run(), the second (and
    only other) way a fact problem is caught: no well-formed shipped descriptor ever
    makes a real Harness produce one, so this pins that loop with a bare stand-in."""
    from types import SimpleNamespace

    from ddflow.services import harnessfacts

    h = SimpleNamespace(
        id="claude",
        facts={"headless.argv": {"s": "bogus", "src": "u", "d": "2026-01-01"}},
        hooks=None,
    )
    monkeypatch.setattr(harnessfacts, "rows", lambda _h, **k: [])
    result = DC.run(h)
    assert result["status"] == "failed", result
    assert result["checks"]["facts"]["status"] == "failed", result


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
