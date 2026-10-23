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


def test_the_tables_global_key_is_the_same_value_as_the_global_knob(repo):
    run_cli(repo, "init")
    cfgp = repo / ".ddflow" / "config.toml"
    cfgp.write_text(
        cfgp.read_text() + "\n[session.context_budget]\nglobal = 30000\nclaude = 25000\n"
    )
    got = Config.load(repo).session
    assert got.context_budget_tokens == 30000 and "global" not in got.context_budget
    assert SC.effective_budget(got, "claude") == 25000 and SC.effective_budget(got, "x") == 30000
    # the later layer's spelling wins, whichever knob it uses
    local = repo / ".ddflow" / "local"
    local.mkdir(exist_ok=True)
    (local / "config.toml").write_text("[session]\ncontext_budget_tokens = 12000\n")
    later = Config.load(repo).session
    assert later.context_budget_tokens == 12000
    assert later.context_budget == {"claude": 25000}, "a scalar in a later layer keeps the table"
    assert SC.effective_budget(later, "claude") == 12000
    # a hand-built session carrying a `global` key still counts it, as the same ceiling
    assert (
        SC.effective_budget(
            _session(context_budget_tokens=0, context_budget={"global": 30000}), "x"
        )
        == 30000
    )


def test_a_type_at_zero_lifts_nothing_and_leaves_the_global_in_force():
    s = _session(context_budget_tokens=30000, context_budget={"claude": 0})
    assert SC.effective_budget(s, "claude") == 30000, "0 at one level is no ceiling THERE, not 0"


def test_the_manifest_default_is_an_empty_table_not_a_string():
    from ddflow.services import upgrade_manifest as UM

    change = next(
        c
        for _v, _d, changes in UM.load().releases
        for c in changes
        if c.key == "session.context_budget"
    )
    assert change.new == {} and isinstance(change.new, dict)


def test_the_headline_follows_the_harness_running_doctor_not_the_ddflow_agent_name(
    repo, monkeypatch
):
    """`agent` is a ddflow identity (a role or worker name), never a harness id: the type comes
    from the session variables of the harness running the command."""
    run_cli(repo, "init")
    cfgp = repo / ".ddflow" / "config.toml"
    cfgp.write_text(cfgp.read_text() + "\n[session.context_budget]\nclaude = 7\n")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    _code, out, _ = run_cli(repo, "--json", "doctor", agent="claude")
    head = next(n for n in json.loads(out)["notes"] if n.startswith("session start cost:"))
    assert "(budget 40000)" in head, head


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
    [
        {"global": -1},
        {"global": "big"},
        {"global": True},
        {"claude": -1},
        {"claude": "big"},
        {"claude": True},
        {"Claude Code": 1},
        {"": 1},
        [1],
        "x",
    ],
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


def test_an_invalid_global_in_the_table_is_refused_when_the_config_loads(repo):
    from ddflow.config import InvalidValue

    run_cli(repo, "init")
    cfgp = repo / ".ddflow" / "config.toml"
    cfgp.write_text(cfgp.read_text() + "\n[session.context_budget]\nglobal = -5\n")
    with pytest.raises(InvalidValue):
        Config.load(repo)
