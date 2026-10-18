"""Conformance over every descriptor: the facts parse, the payload fixtures normalize, an
install is idempotent and a round trip leaves the operator's file as it was.

Parameterised, so a new ``ddflow/harnesses/<id>.toml`` is checked with no new test. Live
probes run only when the agent's binary is on PATH and are skipped (a recorded gap, never a
pass) otherwise.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from harness_conformance import cases as C

from ddflow.api import harness as H
from ddflow.services import harnessreg as HR
from ddflow.services import hookio
from ddflow.services import hookwriters as HW


@pytest.mark.parametrize("hid", C.ALL)
def test_paths_are_project_relative_or_home(hid):
    h = HR.get(hid)
    for rel in filter(None, (h.mcp.path, h.hooks.file if h.hooks else "")):
        assert not Path(rel).is_absolute(), (hid, rel)
        assert ".." not in Path(rel).parts, (hid, rel)


@pytest.mark.parametrize("hid", C.ALL)
def test_every_mapped_event_is_canonical_and_the_ladder_is_total(hid):
    h = HR.get(hid)
    assert set(h.hooks.events if h.hooks else {}) <= set(HR.CANONICAL_EVENTS)
    levels = H.levels(h)
    assert set(levels) == set(HR.CANONICAL_EVENTS)
    assert set(levels.values()) <= {*H.LADDER, H.NV}


@pytest.mark.parametrize("hid", C.COMMAND_HOOKED)
def test_the_normalizer_and_emitter_named_resolve(hid):
    plan = hookio.plan_for(hid)
    assert plan is not None and plan.normalizer in hookio.NORMALIZERS


@pytest.mark.parametrize(("hid", "event", "fx"), C.all_fixtures())
def test_a_recorded_payload_normalizes_to_what_it_says(hid, event, fx):
    plan = hookio.plan_for(hid)
    got = hookio.normalize(plan.normalizer, event, hid, json.dumps(fx["stdin"]))
    assert not got.malformed
    assert got.session_id == fx["expect"]["session_id"]
    if "prompt" in fx["expect"]:
        assert got.prompt == fx["expect"]["prompt"]


def test_every_fixture_says_whether_it_was_recorded():
    for hid, event, fx in C.all_fixtures():
        assert isinstance(fx["_meta"]["recorded"], bool), (hid, event)


@pytest.mark.parametrize("hid", C.COMMAND_HOOKED)
def test_a_hooked_agent_without_fixtures_is_a_visible_gap(hid):
    if not C.fixtures(hid):
        pytest.skip(f"{hid}: no payload fixture yet (B-ac-fixtures records them live)")


@pytest.mark.parametrize("hid", C.WRITABLE)
def test_installing_twice_changes_nothing_and_uninstall_restores_the_file(repo, hid):
    h = HR.get(hid)
    path = repo / h.hooks.file
    path.parent.mkdir(parents=True, exist_ok=True)
    seed = {"version": 1, "keep": {"me": 1}, "hooks": {"Elsewhere": [{"command": "mine"}]}}
    path.write_text(json.dumps(seed, indent=2) + "\n")
    HW.apply(repo, h, "install")
    once = path.read_text()
    HW.apply(repo, h, "install")
    assert path.read_text() == once, hid
    assert all(on is True for _s, on, _w in HW.state(repo, h)), hid
    HW.apply(repo, h, "uninstall")
    assert json.loads(path.read_text()) == seed, hid


@pytest.mark.parametrize("hid", C.ALL)
def test_adopting_twice_changes_nothing(repo, hid):
    run_cli(repo, "init")
    assert run_cli(repo, "adopt", "--agents", hid)[0] == 0

    def tree() -> dict[str, bytes]:
        return {
            str(p.relative_to(repo)): p.read_bytes()
            for p in sorted(repo.rglob("*"))
            if p.is_file() and ".git" not in p.parts and ".ddflow" not in p.parts
        }

    first = tree()
    assert run_cli(repo, "adopt", "--agents", hid)[0] == 0
    assert tree() == first, hid


@pytest.mark.parametrize("hid", C.ALL)
def test_live_probe_runs_only_when_the_agent_is_installed(hid):
    binary = shutil.which(hid)
    if binary is None:
        pytest.skip(f"{hid} is not on PATH: live probe unavailable")
    p = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
