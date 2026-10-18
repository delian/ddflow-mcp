"""`ddflow harness list | show | doctor` and the MCP actions of `ddflow_hooks` that mirror them."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import harness as H
from ddflow.api import harness_view
from ddflow.services import harnessreg as HR


def test_list_names_every_descriptor(repo):
    code, out, _ = run_cli(repo, "harness", "list", "--json")
    assert code == 0
    ids = [r["id"] for r in json.loads(out)["harnesses"]]
    assert ids == HR.ids()


def test_show_gives_a_level_per_canonical_event_and_per_feature(repo):
    out = harness_view(repo, what="show", id="gemini")
    assert list(out.data["levels"]) == list(HR.CANONICAL_EVENTS)
    assert set(out.data["features"]) == {"commands", "skills", "subagents"}
    assert out.data["levels"]["prompt"] == H.NATIVE


def test_the_ladder_falls_back_for_an_agent_without_hooks(repo):
    aider = HR.get("aider")
    # no hooks, no plugin, no MCP: only the instruction file reaches it
    assert {H.event_level(aider, e) for e in HR.CANONICAL_EVENTS} == {H.TEXT}
    assert harness_view(repo, what="show", id="aider").data["features"]["skills"] == "no"


def test_an_unverified_section_is_nv_not_an_absence():
    bare = [h for h in HR.load() if h.skills is None]
    if bare:
        assert H._feature(bare[0].skills) == H.NV


def test_show_an_unknown_id_fails_and_names_the_known_ones(repo):
    code, _, err = run_cli(repo, "harness", "show", "nope")
    assert code == 1 and "claude" in err


def test_doctor_finds_a_missing_mcp_entry_with_its_remedy(repo):
    out = harness_view(repo, what="doctor", id="claude")
    assert out.exit == 1
    mcp = [f for f in out.data["findings"] if f["area"] == "mcp"]
    assert mcp and mcp[0]["remedy"] == "ddflow adopt --agents claude"


def test_doctor_is_clean_for_an_agent_with_nothing_to_install(repo):
    assert harness_view(repo, what="doctor", id="aider").exit == 0


def test_the_mcp_action_matches_the_cli(repo):
    from ddflow.surfaces.declared.hooks import BY_TOOL

    cmd = BY_TOOL["ddflow_hooks"]
    out = cmd.call(repo, {"action": "harness_show", "harness": "claude"}, "t")
    assert out.data["id"] == "claude"
    assert cmd.payload({"action": "harness_show"})[0] == "harnesses"
    assert cmd.payload({"action": "status"})[0] == "installed"
