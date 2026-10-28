"""An author family from config (`[agent].family` / DDFLOW_AGENT_FAMILY) stands in for the
author model id (DX-author-family).

`complete` used to refuse without `--model <author model>`, so agents wrote exact model
ids into the committed event log. A harness declares its family once instead: `complete`
and `gate record` accept that family (or a bare `[agent].families` value after `--model`)
in place of a model id, and the completion event then stores the family only — a model
name is recorded only when one was actually given.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import _CROSS_FAMILY_REVIEWER, pass_pipeline, run_cli, write_config

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import gates as G

OK, REFUSED = 0, 3


@pytest.fixture
def gd(repo, cfg):
    return G.load_gates(repo, cfg)


# -- the knob -----------------------------------------------------------------------


def test_agent_family_is_a_knob_with_env_and_default_off(monkeypatch):
    monkeypatch.delenv("DDFLOW_AGENT_FAMILY", raising=False)
    assert Config().agent.family == ""
    monkeypatch.setenv("DDFLOW_AGENT_FAMILY", "DeepSeek")
    assert Config().agent.family == "DeepSeek"


def test_a_bare_family_name_is_that_family_and_a_model_id_is_not():
    cfg = Config()
    assert G.family_name_of("alibaba", cfg.agent.families) == "alibaba"
    assert G.family_name_of("Alibaba", cfg.agent.families) == "alibaba"
    assert G.family_name_of("qwen", cfg.agent.families) == "", "a needle, not a family value"
    assert G.family_name_of("deepseek-ai/DeepSeek-V4", cfg.agent.families) == ""
    assert G.family_name_of("alibaba/qwen-3-turbo", cfg.agent.families) == "", "by family_for"


# -- the author identity -------------------------------------------------------------


def _deepseek_review() -> dict:
    """The evidence shape `ddflow review` records for a plain DeepSeek reviewer."""
    return {
        "model": "deepseek-ai/DeepSeek-V4.1-Flash",
        "reviewer": "lan-deepseek-v4.1-flash",
        "family": "deepseek",
        "status": "REVIEWED",
    }


def _alibaba_review() -> dict:
    return _deepseek_review() | {"model": "qwen-3-flash", "family": "alibaba"}


def _bare_family_review() -> dict:
    """A reviewer recorded with a bare family VALUE after `--model` and no declared
    `family` field: `_reviewer_fams` resolves it via `family_name_of` (DX-author-family)."""
    return {"model": "alibaba", "reviewer": "lan-qwen-flash", "status": "REVIEWED"}


def _independence_for(log, cfg, gd, author: str, evidence: dict) -> tuple[bool, str]:
    log.append("phase.added", "P1", {})
    log.append("task.added", "T1", {"parent": "P1"})
    G.record(log, cfg, "T1", "critic", "passed", evidence=evidence, gates=gd)
    return G.reviewer_independence(fold(log.read_all()), cfg, "T1", author)


def test_a_reviewer_with_a_bare_family_model_resolves_through_family_name_of(log, cfg, gd):
    """roborev standards (low): the reviewer-side bare-family branch of `_reviewer_fams`
    (no declared `family` field) resolves the family VALUE, so it is not anonymous."""
    ok, why = _independence_for(log, cfg, gd, "deepseek-chat", _bare_family_review())
    assert ok, why
    assert "alibaba" in why, why


def test_a_bare_family_author_is_judged_as_that_family(log, cfg, gd):
    ok, why = _independence_for(log, cfg, gd, "alibaba", _deepseek_review())
    assert ok, why
    assert "alibaba" in why.lower(), why


def test_a_same_family_reviewer_of_a_bare_family_author_does_not_pass(log, cfg, gd):
    ok, why = _independence_for(log, cfg, gd, "alibaba", _alibaba_review())
    assert not ok, why
    assert "same as the author" in why, why


def test_a_bare_family_author_is_not_taken_for_a_router_of_that_name(log, cfg, gd):
    """A router key spelling a configured FAMILY must not hijack the family identity, and
    an EMPTY entry of that name must not misdiagnose the family as a router (qwen #2)."""
    cfg.agent.routers = {"deepseek": ["anthropic"]}
    ok, why = _independence_for(log, cfg, gd, "deepseek", _deepseek_review())
    assert not ok, why
    assert "same as the author" in why, why  # the family, not the router misdiagnosis
    cfg.agent.routers = {"deepseek": []}
    ok, why = _independence_for(log, cfg, gd, "deepseek", _alibaba_review())
    assert ok, why  # an independent reviewer passes; the old code refused "router, set unknown"


def test_configured_sequences_canonicalise_case_across_legs(log, cfg, gd):
    """Critic canon: a configured VALUE 'DeepSeek' and a needle ANSWER 'deepseek' are one
    family however they were spelled, on the value leg (`family_name_of`) and the model
    leg (`family_of`)."""
    cfg.agent.families = dict(cfg.agent.families, deepseek="DeepSeek")
    assert G.family_name_of("DeepSeek", cfg.agent.families) == "deepseek"
    assert G.family_of("deepseek-chat", cfg).strip().lower() == "deepseek"
    ok, why = _independence_for(log, cfg, gd, "deepseek-chat", _alibaba_review())
    assert ok, why


# -- complete records the family only ------------------------------------------------


def _done(repo, author_flag, monkeypatch):
    """Complete T1 (a deepseek-family author's task, gemini reviewer) and return the
    completion event's data."""
    monkeypatch.delenv("DDFLOW_AGENT_FAMILY", raising=False)
    run_cli(repo, "init")
    error, _ = write_config(repo, [("agent.family", "deepseek")])
    assert error == "", error
    code, out, err = run_cli(repo, "task", "add", "T1", "--title", "add a")
    assert code == OK, out + err
    pass_pipeline(repo, "T1", reviewer=_CROSS_FAMILY_REVIEWER)
    code, out, err = run_cli(repo, "complete", "T1", *author_flag)
    assert code == OK, f"{out}{err}"
    events = [e for e in EventLog(repo).read_all() if e.kind == "item.completed"]
    assert events, "no completion event"
    return events[-1].data


def test_complete_with_a_configured_family_records_the_family_only(repo, monkeypatch):
    data = _done(repo, (), monkeypatch)
    assert data.get("author_family") == "deepseek", data
    assert data.get("model_source") == "config", data
    assert "author_model" not in data, "the log must keep the family, not a model id"
    assert "model" not in data, "no model id may reach the event under any key"


def test_complete_accepts_a_bare_family_after_model_and_records_it_only(repo, monkeypatch):
    data = _done(repo, ("--model", "alibaba"), monkeypatch)
    assert data.get("author_family") == "alibaba", data
    assert data.get("model_source") == "flag", data
    assert "author_model" not in data, "the log must keep the family, not a model id"
    assert "model" not in data, "no model id may reach the event under any key"


def test_a_configured_family_wins_over_a_session_model_record(repo, monkeypatch):
    """The completion author-family refusals above exercised no session record: a
    session-declared model id must not reach the log while the harness configured a
    family -- the family is the author then (qwen #1, deepseek #2)."""
    monkeypatch.delenv("DDFLOW_AGENT_FAMILY", raising=False)
    run_cli(repo, "init")
    error, _ = write_config(repo, [("agent.family", "deepseek")])
    assert error == "", error
    run_cli(repo, "task", "add", "T1", "--title", "add a")
    code, out, err = run_cli(
        repo, "session", "start", "--model", "deepseek-chat", agent="impl"
    )
    assert code == OK, out + err
    pass_pipeline(repo, "T1", reviewer=_CROSS_FAMILY_REVIEWER)
    code, out, err = run_cli(repo, "complete", "T1", agent="impl")
    assert code == OK, f"{out}{err}"
    data = [e for e in EventLog(repo).read_all() if e.kind == "item.completed"][-1].data
    assert data.get("author_family") == "deepseek", data
    assert data.get("model_source") == "config", data
    assert "author_model" not in data, "the session's model id must not reach the log"
    assert "model" not in data, "no model id may reach the event under any key"


def test_a_non_canonical_configured_family_recorded_as_its_family_value(repo, monkeypatch):
    """roborev standards (low): [agent].family spelled as a NEEDLE ('kimi') is stored as
    its CANONICAL family value ('moonshot'), the one independence judged against."""
    monkeypatch.delenv("DDFLOW_AGENT_FAMILY", raising=False)
    run_cli(repo, "init")
    error, _ = write_config(repo, [("agent.family", "kimi")])
    assert error == "", error
    run_cli(repo, "task", "add", "T1", "--title", "add a")
    pass_pipeline(repo, "T1", reviewer=_CROSS_FAMILY_REVIEWER)
    code, out, err = run_cli(repo, "complete", "T1")
    assert code == OK, f"{out}{err}"
    data = [e for e in EventLog(repo).read_all() if e.kind == "item.completed"][-1].data
    assert data.get("author_family") == "moonshot", data
    assert data.get("model_source") == "config", data
    assert "author_model" not in data
    assert "model" not in data


def test_an_unrecognised_configured_family_is_recorded_verbatim_only(repo, monkeypatch):
    """roborev standards (low): a configured family the map does not know is recorded
    VERBATIM as `author_family` -- the operator's declaration, never a model name --
    past the force it takes (independence refuses it otherwise)."""
    monkeypatch.delenv("DDFLOW_AGENT_FAMILY", raising=False)
    run_cli(repo, "init")
    error, _ = write_config(repo, [("agent.family", "myorg-x")])
    assert error == "", error
    run_cli(repo, "task", "add", "T1", "--title", "add a")
    pass_pipeline(repo, "T1", reviewer=_CROSS_FAMILY_REVIEWER)
    code, out, err = run_cli(repo, "complete", "T1", "--force")
    assert code == OK, f"{out}{err}"
    data = [e for e in EventLog(repo).read_all() if e.kind == "item.completed"][-1].data
    assert data.get("author_family") == "myorg-x", data
    assert data.get("model_source") == "config", data
    assert "author_model" not in data
    assert "model" not in data


# -- gate record: the author-family refusal reads the configured family too ------------


def test_gate_record_refuses_the_configured_family_without_a_session_model(repo, monkeypatch):
    monkeypatch.delenv("DDFLOW_AGENT_FAMILY", raising=False)
    run_cli(repo, "init")
    error, written = write_config(repo, [("agent.family", "deepseek")])
    assert error == "", written
    run_cli(repo, "task", "add", "T1", "--title", "add a")
    code, out, err = run_cli(
        repo, "gate", "record", "T1", "critic", "--evidence", "found nothing",
        "--model", "deepseek", agent="impl",
    )
    assert code == REFUSED, out + err
    assert "author's own family" in err and "deepseek" in err, err
    code, out, err = run_cli(
        repo, "gate", "record", "T1", "critic", "--evidence", "found nothing",
        "--model", "qwen-3-flash", agent="impl",
    )
    assert code == OK, out + err
    rec = fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"]
    assert rec.evidence["model"] == "qwen-3-flash"


def test_gate_record_refuses_an_unrecognised_configured_family_spelling(repo, monkeypatch):
    """roborev standards (optional low): for a family the map does not know, the bare
    strings compare too, so the writer IS told "--model myorg-x" named its own family."""
    monkeypatch.delenv("DDFLOW_AGENT_FAMILY", raising=False)
    run_cli(repo, "init")
    error, _ = write_config(repo, [("agent.family", "myorg-x")])
    assert error == "", error
    run_cli(repo, "task", "add", "T1", "--title", "add a")
    code, out, err = run_cli(
        repo, "gate", "record", "T1", "critic", "--evidence", "found nothing",
        "--model", "myorg-x", agent="impl",
    )
    assert code == REFUSED, out + err
    assert "author's own family" in err and "myorg-x" in err, err


def test_a_needle_after_model_is_recorded_as_the_model_id_pinned(repo, monkeypatch):
    """roborev standards (low): `--model kimi` (a NEEDLE) is a MODEL id given out loud,
    recorded as `author_model`; the same spelling as `[agent].family` resolves to its
    canonical family value instead (its own test). The asymmetry is intended."""
    monkeypatch.delenv("DDFLOW_AGENT_FAMILY", raising=False)
    run_cli(repo, "init")
    error, _ = write_config(repo, [("agent.family", "")])
    assert error == "", error
    run_cli(repo, "task", "add", "T1", "--title", "add a")
    pass_pipeline(repo, "T1", reviewer=_CROSS_FAMILY_REVIEWER)
    code, out, err = run_cli(repo, "complete", "T1", "--model", "kimi")
    assert code == OK, f"{out}{err}"
    data = [e for e in EventLog(repo).read_all() if e.kind == "item.completed"][-1].data
    assert data.get("author_model") == "kimi", data
    assert data.get("model_source") == "flag", data
    assert "author_family" not in data


def test_a_value_needle_after_model_is_the_family_value(repo, monkeypatch):
    """roborev standards (low, doc pin): a needle that IS ALSO a family value, such as
    "mistral", is a family on the flag leg too, and records `author_family`."""
    monkeypatch.delenv("DDFLOW_AGENT_FAMILY", raising=False)
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "add a")
    pass_pipeline(repo, "T1", reviewer=_CROSS_FAMILY_REVIEWER)
    code, out, err = run_cli(repo, "complete", "T1", "--model", "mistral")
    assert code == OK, f"{out}{err}"
    data = [e for e in EventLog(repo).read_all() if e.kind == "item.completed"][-1].data
    assert data.get("author_family") == "mistral", data
    assert data.get("model_source") == "flag", data
    assert "author_model" not in data


def test_complete_surfaces_the_author_family_to_the_caller(repo, monkeypatch):
    """roborev standards (medium): the --json surface carries `author_family` (projected
    through the declared payload), not a dangling `model_source` with no identity key."""
    monkeypatch.delenv("DDFLOW_AGENT_FAMILY", raising=False)
    run_cli(repo, "init")
    error, _ = write_config(repo, [("agent.family", "deepseek")])
    assert error == "", error
    run_cli(repo, "task", "add", "T1", "--title", "add a")
    pass_pipeline(repo, "T1", reviewer=_CROSS_FAMILY_REVIEWER)
    code, out, err = run_cli(repo, "complete", "T1", "--json")
    assert code == OK, f"{out}{err}"
    assert '"author_family"' in out and '"deepseek"' in out, out
