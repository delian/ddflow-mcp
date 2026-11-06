"""The rules `ddflow harness record` keeps (B-ac-fixtures): the scrub, when a recording has gone
stale, and what a tier-2 checklist must say. `test_harness_fixtures.py` drives the verb itself.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness_conformance import report as R

from ddflow.core.redact import Redactor
from ddflow.services import harness_record as HRec
from ddflow.services import harness_tap as Tap
from ddflow.services import harnessreg as HR

ROOT = Path(__file__).resolve().parents[1]
CHECKLISTS = ROOT / "docs" / "ddflow" / "agents" / "checklist"
#: D-agent-compat tier 2: no headless run, so a person walks a checklist.
TIER_2 = ("cursor", "cline", "devin", "continue", "antigravity", "aider")
TODAY = date(2026, 11, 5)
SESSION = "7d6c0a52-1111-4222-8333-0123456789ab"
PROMPT_ID = "b12ae6be-5f0c-4687-9a33-6681ac070e94"


# --- the scrub -------------------------------------------------------------------------


def _scrubber(root="/tmp/x.y/z", home="/home/me"):
    r = Redactor("log", secret_patterns=[r"(?i)token[=:]\s*\S+"], hostname="box9", home=home)
    return HRec.Scrubber(root=root, home=home, redactor=r)


def test_the_scrub_replaces_paths_ids_and_secrets_in_keys_and_values():
    s = _scrubber()
    s.session("abc-session")
    got = s.value(
        {
            "/tmp/x.y/z/f": "/home/me/.a/-tmp-x-y-z/abc-session.jsonl",
            "other": [PROMPT_ID, PROMPT_ID, SESSION, "token=hunter2hunter2", "on box9"],
        }
    )
    assert got["/w/f"] == "~/.a/-w/s-1.jsonl"
    first, again, second, secret, host = got["other"]
    assert first == again != second
    assert re.fullmatch(r"0{8}-0{4}-0{4}-0{4}-0{11}1", first)
    assert "hunter2" not in secret
    assert "box9" not in host


def test_the_scrub_replaces_a_home_in_a_directory_name():
    assert _scrubber().value("x/-home-me-.claude/y") == "x/-home-.claude/y"


def test_a_session_id_inside_the_project_path_does_not_break_the_path():
    s = _scrubber(root="/work/a1b2c3d4/proj")
    s.session("a1b2c3d4")
    assert s.value("/work/a1b2c3d4/proj/f and a1b2c3d4") == "/w/f and s-1"


def test_a_session_id_inside_a_longer_one_leaves_no_tail():
    s = _scrubber()
    s.session("abcd1234")
    s.session("abcd1234XYZ")
    assert s.value("id=abcd1234XYZ abcd1234") == "id=s-2 s-1"


def test_the_scrub_cuts_a_long_string_and_says_so():
    got = _scrubber().value("x" * (HRec.MAX_STRING + 50))
    assert len(got) < HRec.MAX_STRING + 40
    assert got.endswith("[cut 50 chars]")


def test_a_replacement_inside_a_longer_word_still_scrubs():
    assert _scrubber(root="/tmp/x").value("/tmp/x/y and /tmp/x") == "/w/y and /w"


def test_leaks_name_the_kind_and_never_the_value():
    s = _scrubber()
    s.session("abc-session")
    assert s.leaks({"a": "/home/me/x"}) == ["home"]
    assert s.leaks({"/tmp/x.y/z": 1}) == ["project path"]
    assert s.leaks({"a": "abc-session"}) == ["session id"]
    assert s.leaks({"a": "-home-me-.claude"}) == ["home"]
    assert s.leaks({"a": "~/x"}) == []


def test_a_home_of_slash_and_a_short_session_id_mangle_nothing():
    s = _scrubber(home="/")
    s.session("1")
    assert s.value("a/b 1") == "a/b 1"
    assert s.leaks({"a": "a/b 1"}) == []


def test_a_hostname_inside_a_longer_word_is_no_leak():
    assert _scrubber().leaks({"a": "box99"}) == []
    assert _scrubber().leaks({"a": "on box9 now"}) == ["hostname"]


def test_junk_on_the_wire_does_not_stop_a_recording(tmp_path):
    cap = tmp_path / HRec.CAPTURE_DIR
    cap.mkdir()
    rows = [
        {"d": "in", "line": "not json"},
        {"d": "in", "line": "[1]"},
        {"d": "in", "line": json.dumps({"id": [1], "method": "m"})},
        {"d": "in", "line": json.dumps({"id": 1, "method": "m", "params": [1]})},
        {"d": "out", "line": json.dumps({"id": 1, "result": {}})},
    ]
    text = "".join(json.dumps(r) + "\n" for r in rows) + '{"d": "in", "li'
    (cap / Tap.MCP_LOG).write_text(text)
    assert [p["method"] for p in HRec._pairs(cap)] == ["m"]


# --- staleness -------------------------------------------------------------------------


def _meta(**kw):
    return {"recorded": True, "version": "2.1.318 (Claude Code)", "date": "2026-11-05", **kw}


def test_a_fresh_recording_of_the_installed_major_has_no_flag():
    flags = HRec.fixture_flags(_meta(), today=TODAY, months=6, installed="2.9.0")
    assert flags == []


def test_an_old_recording_is_flagged_by_its_age():
    [flag] = HRec.fixture_flags(_meta(date="2026-04-04"), today=TODAY, months=6)
    assert "older than 6 months" in flag and "2026-04-04" in flag


def test_the_age_boundary_is_whole_months():
    assert HRec.fixture_flags(_meta(date="2026-05-05"), today=TODAY, months=6) == []
    assert HRec.fixture_flags(_meta(date="2026-05-04"), today=TODAY, months=6)


def test_another_major_version_is_flagged():
    [flag] = HRec.fixture_flags(_meta(), today=TODAY, months=6, installed="3.0.1")
    assert "major 2" in flag and "major 3" in flag


def test_a_recording_with_no_date_is_flagged_not_taken_as_fresh():
    [flag] = HRec.fixture_flags({"recorded": True, "version": "1"}, today=TODAY, months=6)
    assert "no date" in flag


def test_a_synthetic_fixture_is_not_aged():
    meta = {"recorded": False, "version": "unversioned"}
    assert HRec.fixture_flags(meta, today=TODAY, months=6, installed="2.0") == []


@pytest.mark.parametrize("text", ["{", '{"_meta": null}', '{"_meta": "bogus"}', "[]"])
def test_an_unreadable_fixture_is_a_row_not_a_crash(tmp_path, text):
    (tmp_path / "prompt.json").write_text(text)
    [row] = HRec.rows(tmp_path, today=TODAY, months=6)
    assert row["status"] == "unreadable"


def test_every_committed_fixture_of_an_agent_is_a_row(tmp_path):
    (tmp_path / "unmapped").mkdir()
    (tmp_path / "unmapped" / "post_compact.json").write_text(
        json.dumps({"_meta": _meta(date="2020-01-01")})
    )
    [row] = HRec.rows(tmp_path, today=TODAY, months=6)
    assert row["event"] == "unmapped/post_compact" and row["flags"]


def test_the_gap_report_carries_the_flags():
    rows = R.rows()
    assert all("flags" in r for r in rows)
    assert " flags |" in R.render().splitlines()[0]


# --- the checklists ---------------------------------------------------------------------


@pytest.mark.parametrize("agent", TIER_2)
def test_every_tier_2_agent_has_a_checklist_with_the_four_steps(agent):
    text = (CHECKLISTS / f"{agent}.md").read_text()
    for heading in ("## Adopt", "## Approve the MCP server", "## Prompt capture", "## Round trip"):
        assert heading in text, (agent, heading)
    assert re.search(r"^Agent version: \S+", text, re.M), agent
    assert re.search(r"^Date walked: \S+", text, re.M), agent


@pytest.mark.parametrize("agent", TIER_2)
def test_a_checklist_states_the_descriptor_facts_it_rests_on(agent):
    h = HR.get(agent)
    text = (CHECKLISTS / f"{agent}.md").read_text()
    if h is None:
        assert "no descriptor" in text, agent
    else:
        assert h.mcp.path in text, agent


def test_a_checklist_never_walked_says_so(tmp_path):
    p = tmp_path / "x.md"
    p.write_text("Agent version: unverified\nDate walked: never\n")
    assert HRec.checklist_status(p, today=TODAY, months=6) == "checklist never walked"


def test_a_walked_checklist_says_its_date_and_goes_stale(tmp_path):
    p = tmp_path / "x.md"
    p.write_text("Agent version: 1.2\nDate walked: 2026-10-01\n")
    assert (
        HRec.checklist_status(p, today=TODAY, months=6)
        == "fixture only, checklist dated 2026-10-01"
    )
    p.write_text("Agent version: 1.2\nDate walked: 2025-10-01\n")
    got = HRec.checklist_status(p, today=TODAY, months=6)
    assert "2025-10-01" in got and "older than 6 months" in got
