"""Passes due by the CALENDAR: "a bug hunt every week".

Both source projects ask for a weekly bug hunt and dedupe pass, in prose, and nothing
schedules either -- which is where such a rule stops happening. ddflow's cadences counted
completed work only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api.operations import _calendar_due
from ddflow.config import Config
from ddflow.core.model import State

OK, FAIL, NOTHING = 0, 1, 2


def _due(repo: Path) -> dict[str, dict]:
    code, out, _e = run_cli(repo, "--json", "cadence")
    body = json.loads(out)
    rows = body if isinstance(body, list) else body.get("due", [])
    return {d["cadence"]: d for d in rows} if code == OK else {}


def test_a_pass_that_has_never_run_is_due_now_and_running_it_clears_it(repo):
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text('[cadence]\nevery_days = ["bug_hunt=7"]\n')
    due = _due(repo)
    assert due["bug_hunt"]["since"] == "never"
    code, _o, err = run_cli(repo, "cadence", "--ran", "bug_hunt", "--note", "3 findings")
    assert code == OK, err
    assert "bug_hunt" not in _due(repo)


def test_it_falls_due_again_after_its_period():
    cfg = Config()
    cfg.cadence.every_days = ["bug_hunt=7"]
    st = State()
    st.cadences["bug_hunt"] = [{"at": "2026-09-01T00:00:00Z", "result": "2026-09-01"}]
    from ddflow.core.progress import epoch

    ran = epoch("2026-09-01T00:00:00Z")
    assert _calendar_due(st, cfg, now=ran + 6 * 86400) == []
    assert _calendar_due(st, cfg, now=ran + 7 * 86400)[0]["cadence"] == "bug_hunt"


def test_a_calendar_name_replaces_the_count_based_pass_of_the_same_name(repo):
    """Its runs record a DATE; read as a completion count it would raise."""
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text('[cadence]\nevery_days = ["dedupe_sweep=7"]\n')
    run_cli(repo, "cadence", "--ran", "dedupe_sweep")
    code, _out, err = run_cli(repo, "--json", "cadence")
    assert code in (OK, NOTHING), err
    assert "dedupe_sweep" not in _due(repo)


def test_the_session_start_hook_says_what_is_due(repo):
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text('[cadence]\nevery_days = ["bug_hunt=7"]\n')
    _c, out, _e = run_cli(repo, "hooks", "session-start")
    assert "Periodic passes DUE: bug_hunt (last: never)" in out
