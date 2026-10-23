"""One global start budget and one per agent type (D-session-start-budget, LS-budget-per-agent).

`[session].context_budget_tokens` is the global ceiling on what ddflow costs a session before its
first turn. `[session.context_budget]` adds a ceiling per agent type -- the harness ids
`ddflow harness list` prints (claude, codex, copilot, cursor, dsh, ...) -- so each subscription or
LLM target is managed separately. A session's effective budget is the MIN of the global and its
type's; an unset type inherits the global; 0 means no ceiling at that level.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import _KNOB_CHECKS, Config
from ddflow.services import harnessreg as HR
from ddflow.services import startcost as SC


def _session(**kw):
    cfg = Config()
    for k, v in kw.items():
        setattr(cfg.session, k, v)
    return cfg.session


def test_an_unset_type_inherits_the_global():
    s = _session(context_budget_tokens=40000)
    assert SC.effective_budget(s, "claude") == 40000
    assert SC.effective_budget(s, "") == 40000


def test_the_effective_budget_is_the_min_of_global_and_type():
    s = _session(context_budget_tokens=40000, context_budget={"claude": 20000, "codex": 60000})
    assert SC.effective_budget(s, "claude") == 20000
    assert SC.effective_budget(s, "codex") == 40000, "a type above the global does not raise it"
    assert SC.effective_budget(s, "cursor") == 40000


def test_the_table_has_its_own_global_key_that_is_the_same_ceiling():
    s = _session(context_budget_tokens=40000, context_budget={"global": 30000})
    assert SC.effective_budget(s, "claude") == 30000
    s = _session(context_budget_tokens=0, context_budget={"global": 30000, "claude": 25000})
    assert SC.effective_budget(s, "claude") == 25000 and SC.effective_budget(s, "x") == 30000


def test_zero_means_no_ceiling_at_that_level_and_none_at_all_is_zero():
    s = _session(context_budget_tokens=0, context_budget={"claude": 0})
    assert SC.effective_budget(s, "claude") == 0
    s = _session(context_budget_tokens=0, context_budget={"claude": 15000})
    assert SC.effective_budget(s, "claude") == 15000 and SC.effective_budget(s, "codex") == 0


@pytest.mark.parametrize("hid", [h.id for h in HR.load()])
def test_every_agent_type_has_an_effective_budget_by_default(hid):
    """The per-target ratchet: a harness the registry knows is never left without a number."""
    assert SC.effective_budget(Config().session, hid) == Config().session.context_budget_tokens


@pytest.mark.parametrize(
    "bad",
    [{"claude": -1}, {"claude": "big"}, {"claude": True}, {"Claude Code": 1}, {"": 1}, [1], "x"],
)
def test_the_table_refuses_what_is_not_a_budget(bad):
    assert _KNOB_CHECKS["session.context_budget"](bad)


def test_the_table_accepts_global_and_types():
    assert not _KNOB_CHECKS["session.context_budget"]({"global": 30000, "claude": 20000, "dsh": 0})
    assert not _KNOB_CHECKS["session.context_budget"]({})


def test_a_layer_sets_the_table_and_the_local_layer_wins(repo):
    run_cli(repo, "init")
    cfgp = repo / ".ddflow" / "config.toml"
    cfgp.write_text(
        cfgp.read_text() + "\n[session.context_budget]\nclaude = 30000\ncodex = 20000\n"
    )
    local = repo / ".ddflow" / "local"
    local.mkdir(exist_ok=True)
    (local / "config.toml").write_text("[session.context_budget]\nclaude = 10000\n")
    got = Config.load(repo).session.context_budget
    assert got["claude"] == 10000, got
    assert got.get("codex") == 20000, "the layers merge key by key, as other tables do"


def test_doctor_reports_each_configured_target(repo):
    run_cli(repo, "init")
    cfgp = repo / ".ddflow" / "config.toml"
    cfgp.write_text(cfgp.read_text() + "\n[session.context_budget]\nclaude = 5\ncursor = 90000\n")
    _code, out, _ = run_cli(repo, "--json", "doctor")
    notes = [n for n in json.loads(out)["notes"] if "session start cost" in n]
    text = "\n".join(notes)
    assert "claude: " in text and "(budget 5)" in text and "OVER" in text, text
    assert "cursor: " in text and "(budget 40000)" in text, "above the global: the global wins"
    assert "session start cost:" in text, "the global line is still there"


def test_doctor_with_no_table_has_the_one_line_it_always_had(repo):
    run_cli(repo, "init")
    _code, out, _ = run_cli(repo, "--json", "doctor")
    notes = [n for n in json.loads(out)["notes"] if "session start cost" in n]
    assert len(notes) == 1 and "\n" not in notes[0], notes


def test_the_running_agent_type_sets_the_headline_budget(repo, monkeypatch):
    run_cli(repo, "init")
    cfgp = repo / ".ddflow" / "config.toml"
    cfgp.write_text(cfgp.read_text() + "\n[session.context_budget]\nclaude = 7\n")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s-1")
    _code, out, _ = run_cli(repo, "--json", "doctor")
    head = next(n for n in json.loads(out)["notes"] if n.startswith("session start cost:"))
    assert "(budget 7)" in head, head
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    _code, out, _ = run_cli(repo, "--json", "doctor")
    head = next(n for n in json.loads(out)["notes"] if n.startswith("session start cost:"))
    assert "(budget 40000)" in head, head


def test_measure_carries_the_per_target_budgets():
    cost = SC.measure(
        {"a": "x" * 400}, bytes_per_token=4.0, budget=50, targets={"claude": 30, "codex": 500}
    )
    lines = cost.notes()[0].splitlines()
    assert lines[0].startswith("session start cost: 100 tokens (budget 50) -- OVER by 50")
    assert any(
        ln.strip().startswith("claude: 100 tokens (budget 30) -- OVER by 70") for ln in lines
    )
    assert any(ln.strip().startswith("codex: 100 tokens (budget 500)") for ln in lines)
    assert cost.as_dict()["targets"] == {"claude": 30, "codex": 500}
