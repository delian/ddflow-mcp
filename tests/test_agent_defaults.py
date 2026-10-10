"""A command run under an agent identity uses what that agent already holds.

Two bugs filed from home-simulator, one shape: the identity was known and its own
records were ignored.

* B226d8db6e8 -- `ddflow --agent X brief` was headed `Current: <top ready item>`, an
  item X never claimed, while X held a live lease on another. The brief now leads with
  the agent's own lease; with several it says so and lists them; only an agent holding
  nothing gets the top ready item, and then as "Suggested next", never "Current".
* B7a5c63e3d2 -- `ddflow --agent X complete` without `--model` judged reviewer
  independence against an EMPTY author model, though X had declared its model at
  `session start`. It now defaults from X's open session; when nothing names the model
  the refusal says to pass `--model`, instead of quoting `''`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import pass_pipeline, run_cli
from helpers import rpc_call as _call

from ddflow.surfaces.mcp import Server


def _text(result: dict) -> str:
    return "\n".join(c.get("text", "") for c in result.get("content", []))


def _queue(repo: Path) -> None:
    run_cli(repo, "init")
    # C11 first: it is the top ready item, the one the bug showed as Current.
    run_cli(repo, "task", "add", "C11", "--title", "top ready item", "--globs", "c.py")
    run_cli(repo, "task", "add", "T2", "--title", "the claimed one", "--globs", "b.py")
    run_cli(repo, "task", "add", "T3", "--title", "another claimed one", "--globs", "d.py")


# ---------------------------------------------------------------- B226d8db6e8: brief


def test_brief_current_is_the_agents_own_lease_not_the_top_ready_item(repo):
    _queue(repo)
    code, out, err = run_cli(repo, "claim", "T2", agent="impl")
    assert code == 0, out + err

    code, out, err = run_cli(repo, "brief", agent="impl")
    assert code == 0, out + err
    assert "## Current: T2" in out, out
    assert "Current: C11" not in out, out

    code, out, _ = run_cli(repo, "--json", "brief", agent="impl")
    assert json.loads(out)["item"] == "T2"


def test_brief_with_several_leases_says_so_and_lists_them(repo):
    _queue(repo)
    # Claimed against id order, so "most recent claim" and "highest id" disagree.
    for iid in ("T3", "T2"):
        code, out, err = run_cli(repo, "claim", iid, agent="impl")
        assert code == 0, out + err

    code, out, _ = run_cli(repo, "brief", agent="impl")
    assert code == 0
    assert "You hold 2 leases" in out, out
    assert "`T2`" in out and "`T3`" in out, out
    assert "Current: C11" not in out, out
    # The most recent claim is the one in focus.
    assert "## Current: T2" in out, out


def test_brief_for_an_agent_holding_nothing_suggests_rather_than_claims(repo):
    _queue(repo)
    run_cli(repo, "claim", "T2", agent="someone-else")

    code, out, _ = run_cli(repo, "brief", agent="idle")
    assert code == 0
    assert "## Current" not in out, out
    assert "## Suggested next: C11" in out, out


def test_brief_item_flag_still_names_the_current_item(repo):
    _queue(repo)
    run_cli(repo, "claim", "T2", agent="impl")
    code, out, _ = run_cli(repo, "brief", "--item", "T3", agent="impl")
    assert code == 0
    assert "## Current: T3" in out, out


def test_mcp_brief_under_as_agent_follows_the_same_rule(repo):
    _queue(repo)
    srv = Server(repo)
    claimed = _call(srv, "ddflow_claim", id="T2", as_agent="impl")
    assert claimed["_meta"]["exit"] == 0, claimed

    text = _text(_call(srv, "ddflow_brief", as_agent="impl"))
    assert "## Current: T2" in text, text
    assert "Current: C11" not in text, text

    idle = _text(_call(srv, "ddflow_brief", as_agent="idle"))
    assert "## Current" not in idle, idle
    assert "## Suggested next: C11" in idle, idle


# ------------------------------------------------------ B7a5c63e3d2: complete --model


def _ready_to_complete(repo: Path, monkeypatch, agent: str) -> None:
    monkeypatch.setenv("DDFLOW_AGENT", agent)
    code, out, err = run_cli(repo, "claim", "T2")
    assert code == 0, out + err
    pass_pipeline(repo, "T2")  # cross-family reviewer: gemini-2.5-pro


def test_complete_defaults_the_author_model_from_the_agents_open_session(repo, monkeypatch):
    _queue(repo)
    code, out, err = run_cli(repo, "session", "start", "--model", "claude-opus", agent="impl")
    assert code == 0, out + err
    _ready_to_complete(repo, monkeypatch, "impl")

    code, out, err = run_cli(repo, "--json", "complete", "T2")
    assert code == 0, out + err
    body = json.loads(out)
    assert "anthropic" in body["independence"], body["independence"]


def test_complete_does_not_borrow_another_agents_session_model(repo, monkeypatch):
    _queue(repo)
    run_cli(repo, "session", "start", "--model", "claude-opus", agent="other")
    _ready_to_complete(repo, monkeypatch, "impl")

    code, out, err = run_cli(repo, "complete", "T2")
    assert code == 3, out + err
    assert "pass `--model <author model>`" in out + err, out + err
    assert "model ''" not in out + err, out + err


def test_complete_explicit_model_wins_over_the_session(repo, monkeypatch):
    _queue(repo)
    # The session names the REVIEWER's family; the explicit author model must decide.
    # (`--reviewer-model`: a plain --model in the session's family is refused on a
    # reviewer gate, B1979dac602.)
    run_cli(repo, "session", "start", "--model", "claude-opus", agent="impl")
    _ready_to_complete(repo, monkeypatch, "impl")
    code, out, err = run_cli(
        repo,
        "gate",
        "record",
        "T2",
        "critic",
        "--evidence",
        "x",
        "--reviewer-model",
        "claude-sonnet",
    )
    assert code == 0, out + err
    code, out, err = run_cli(repo, "complete", "T2", "--model", "gemini-2.5-pro")
    assert code == 0, out + err


def test_mcp_complete_defaults_from_the_session_started_over_mcp(repo, monkeypatch):
    _queue(repo)
    srv = Server(repo)
    started = _call(srv, "ddflow_session_start", model="claude-opus", as_agent="impl")
    assert started["_meta"]["exit"] == 0, started
    _ready_to_complete(repo, monkeypatch, "impl")

    done = _call(srv, "ddflow_complete", id="T2", as_agent="impl")
    assert done["_meta"]["exit"] == 0, _text(done)
