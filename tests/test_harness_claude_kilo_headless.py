"""XA-headless-facts-claude-kilo: the [detect], [headless], [autonomy] and [acp] facts of the
claude and kilo descriptors, each with its evidence and verification state.

`claude` and `kilo` are the two agents installed on the machine that recorded these facts, so
their read-only probes (`--version`, `--help`) are the evidence. A fact that needs a model run
stays `unverified` and names the exact command a person would run (`[verified].unverified`).
No agent is started here: the tests read the descriptors only.
"""

from __future__ import annotations

import dataclasses
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ddflow.services import driver_conformance as DC
from ddflow.services import facts as F
from ddflow.services import harnessfacts, harnessreg
from ddflow.services.harnessv2 import Autonomy
from ddflow.services.lanes import opencode as OC

ROOT = Path(__file__).resolve().parents[1]
CLAUDE = harnessreg.get("claude")
KILO = harnessreg.get("kilo")
OPENCODE = harnessreg.get("opencode")

HEADLESS_KEYS = (
    "headless.argv",
    "headless.model_flag",
    "headless.output_flag",
    "headless.output_format",
    "headless.resume_flag",
)
READ = {"verified", "documented"}


def test_claude_headless_flags_are_the_ones_its_help_lists():
    h = CLAUDE.headless
    assert h is not None
    assert h.argv == ("claude", "-p")
    assert h.model_flag == "--model" and h.effort_flag == "--effort"
    assert h.mcp_flag == "--mcp-config"
    assert (h.output_flag, h.output_format) == ("--output-format", "stream-json")
    assert h.resume_flag == "--resume"


def test_claude_autonomy_profiles_cover_permission_mode_and_allowed_tools():
    a = CLAUDE.autonomy
    assert a is not None and not a.none
    assert a.profiles["accept-edits"] == "--permission-mode acceptEdits"
    assert a.profiles["bypass"] == "--dangerously-skip-permissions"
    assert "--allowedTools" in a.profiles[CLAUDE.headless.autonomy]
    assert "--permission-mode dontAsk" in a.profiles[CLAUDE.headless.autonomy]


def test_claude_is_detected_by_its_binary_its_env_marker_and_its_client_name():
    d = CLAUDE.detect
    assert d is not None
    assert d.process_names == ("claude",)
    assert d.env == ("CLAUDECODE",)
    assert d.client_names == ("claude-code",)


def test_claude_has_no_native_acp_server():
    assert CLAUDE.reserved["acp"].none is True
    assert CLAUDE.facts["acp"]["s"] == "none"


def test_kilo_headless_is_the_run_command_with_json_events():
    h = KILO.headless
    assert h is not None
    assert h.argv == ("kilo", "run")
    assert h.model_flag == "--model" and h.effort_flag == "--variant"
    assert (h.output_flag, h.output_format) == ("--format", "json")
    assert h.resume_flag == "--session"
    assert KILO.autonomy.profiles[h.autonomy] == "--auto"


def test_kilo_is_detected_by_binary_and_client_name_and_serves_acp():
    assert KILO.detect.process_names == ("kilo",)
    assert KILO.detect.client_names == ("kilo",)
    assert KILO.reserved["acp"].values["command"] == "kilo acp"
    assert KILO.reserved["acp"].values["cwd_flag"] == "--cwd"


def test_opencode_shares_the_kilo_run_command_shape():
    h = OPENCODE.headless
    assert h is not None and h.argv == ("opencode", "run")
    assert (h.output_flag, h.output_format, h.model_flag) == ("--format", "json", "--model")
    assert OPENCODE.autonomy.profiles[h.autonomy] == "--auto"


@pytest.mark.parametrize("h", [CLAUDE, KILO, OPENCODE], ids=lambda h: h.id)
def test_every_headless_fact_carries_status_source_date(h):
    rows = {r["fact"]: r for r in harnessfacts.rows(h)}
    for key in HEADLESS_KEYS:
        if key not in rows:  # opencode: the resume flag was never read
            assert h.id == "opencode" and key == "headless.resume_flag"
            continue
        r = rows[key]
        assert r["status"] in READ, (h.id, key, r["status"])
        assert r["source"] and r["date"], (h.id, key)
        node = h.facts[key]
        assert F.problems(f"{h.id}.{key}", node) == []


@pytest.mark.parametrize("h", [CLAUDE, KILO], ids=lambda h: h.id)
def test_a_verified_fact_names_the_probe_and_the_tool_version(h):
    verified = {k: n for k, n in h.facts.items() if n["s"] == "verified"}
    assert verified, h.id
    for key, node in verified.items():
        assert node.get("ver"), (h.id, key, "a verified fact names the version it was read on")
        assert node.get("probe"), (h.id, key, "and the read-only command that re-reads it")
        words = node["probe"].split()
        assert "--version" in words or "--help" in words or words[0] == "env", (
            h.id, key, "a verified fact's probe is read-only: --version, --help or the env listing",
        )  # fmt: skip


def test_the_installed_versions_are_the_ones_the_facts_were_read_on():
    assert CLAUDE.facts["headless.argv"]["ver"] == "2.1.323"
    assert KILO.facts["headless.argv"]["ver"] == "7.8.27"


@pytest.mark.parametrize("h", [CLAUDE, KILO], ids=lambda h: h.id)
def test_what_needs_a_model_run_is_listed_with_its_command(h):
    entries = h.verified.unverified
    codes = [e for e in entries if "exit codes" in e]
    assert len(codes) == 1 and "probe: " in codes[0], "exit codes name their own command"
    assert any("max_turns_flag" in e for e in entries)
    for e in entries:
        if "one model call" in e:  # the cost is stated beside each command that spends
            assert "probe: " in e, e


def test_kilo_lane_argv_is_built_from_the_descriptor():
    b = OC.Binary("kilo", "/x/kilo", "7.8.27", True)
    assert OC.argv_for(b) == (
        "/x/kilo", "run", OC.MESSAGE, "--format", "json", "--model", "{model}",
        "--dir", "{dir}", "--auto", "--file", "{prompt_file}",
    )  # fmt: skip
    # a different descriptor value moves the lane's argv: it is not a copy in code
    other = harnessreg.parse(
        (ROOT / "ddflow/harnesses/kilo.toml").read_text("utf-8").replace('"--auto"', '"--yolo"'),
        "kilo",
    )
    assert "--yolo" in OC.argv_for(b, descriptor=other)
    assert "--auto" not in OC.argv_for(b, descriptor=other)


@pytest.mark.parametrize("hid", ["claude", "kilo"])
def test_harness_test_grades_headless_not_ok_over_unknown(hid):
    out = DC.run(harnessreg.get(hid))
    chk = out["checks"]["headless"]
    assert chk["status"] == "ok"
    assert re.match(r"^\d+ read \(verified or documented\), \d+ unverified", chk["detail"])
    assert "unknown" not in chk["detail"]


def test_a_headless_section_nobody_read_is_unverified_not_ok():
    raw = (ROOT / "ddflow/harnesses/kilo.toml").read_text("utf-8")
    bare = harnessreg.parse(raw.split("[facts.")[0] + raw[raw.index("[verified]") :], "kilo")
    out = DC.run(bare)
    assert out["checks"]["headless"]["status"] == "unverified"
    assert out["status"] == "unverified"


def test_the_agent_smoke_plan_lists_claude_and_kilo():
    got = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts/ci/agent-smoke"), "plan", "--json"],
        capture_output=True, text=True, check=True, cwd=ROOT,
    )  # fmt: skip
    plan = {r["agent"]: r["command"] for r in json.loads(got.stdout)}
    assert plan["claude"].startswith("claude -p ") and "--permission-mode dontAsk" in plan["claude"]
    assert plan["kilo"].startswith("kilo run ") and "--auto" in plan["kilo"]


def test_the_three_descriptors_read_at_least_the_facts_this_task_recorded():
    rows = []
    for h in harnessreg.load():
        rows += harnessfacts.rows(h)
    mine = [r for r in rows if r["agent"] in ("claude", "kilo", "opencode") and r["status"] in READ]
    assert len(mine) >= 28


def test_acp_is_the_only_reserved_section_claude_and_kilo_fill():
    assert set(CLAUDE.reserved) == {"acp"} and set(KILO.reserved) == {"acp"}


def test_a_kilo_lane_whose_autonomy_profile_is_missing_refuses_to_start():
    b = OC.Binary("kilo", "/x/kilo", "7.8.27", True)
    other = dataclasses.replace(KILO, autonomy=Autonomy(profiles={"other": "--x"}))
    with pytest.raises(ValueError, match="autonomy"):
        OC.argv_for(b, descriptor=other)
