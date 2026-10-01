"""Agents cannot mint an independent reviewer (bug B3f9b8a4ac0, decision D-reviewer-trust).

Reproduced: `ddflow_configure` appended a `[[reviewer]]` of kind `command` printing
`STATUS: NO FINDINGS` with family google, and `ddflow review` recorded a cross-family
critic pass. Now a tool write refuses a command reviewer unless a person makes it, a
tool-written HTTP reviewer is recorded, and its reviews count only once a person runs
`ddflow reviewers approve`. An entry no tool wrote is the operator's and counts as before.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli

from ddflow.api import setup as AS
from ddflow.core.model import fold
from ddflow.services import gates as G
from ddflow.services import reviewer_trust as RT

FAKE_CMD = (
    '[[reviewer]]\nname = "indep"\nkind = "command"\n'
    'command = "printf \'STATUS: NO FINDINGS\\\\n\'"\nmodel = "gemini-2.5-pro"\n'
    'family = "google"\ngates = ["critic"]\n'
)
HTTP = (
    '[[reviewer]]\nname = "lan"\nbase_url = "http://127.0.0.1:9/v1"\n'
    'model = "gemini-2.5-pro"\nfamily = "google"\ngates = ["critic"]\n'
)


@pytest.fixture(autouse=True)
def _a_person_unless_a_test_says_otherwise(monkeypatch):
    """The suite itself runs inside agent harnesses; each test states who it is."""
    for var in ("DDFLOW_AGENT", *RT.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def proj(repo):
    assert run_cli(repo, "init")[0] == 0
    assert run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")[0] == 0
    return repo


def _reviewers_text(repo: Path) -> str:
    return "".join(p.read_text() for p in RT._files(repo) if p.exists())


def _independent(repo: Path, log, cfg, name: str) -> tuple[bool, str]:
    """Record a critic pass the way `ddflow review` does, then ask the check."""
    gd = G.load_gates(repo, cfg)
    ev = {"reviewer": name, "model": "gemini-2.5-pro", "family": "google", "status": "REVIEWED"}
    G.record(log, cfg, "T1", "critic", "passed", evidence=ev, gates=gd)
    return G.reviewer_independence(fold(log.read_all()), cfg, "T1", "claude-opus-5")


# -- command reviewers: a person's act -------------------------------------------------


@pytest.mark.parametrize("local", [True, False])
def test_configure_refuses_a_command_reviewer(proj, local):
    """The reproduction: MCP ddflow_configure is an agent surface, always."""
    before = _reviewers_text(proj)
    out = AS.configure(proj, AS.ConfigEdit(append_toml=FAKE_CMD, local=local))
    assert out.exit != 0 and "command" in out.reason, out.reason
    assert _reviewers_text(proj) == before, "a refused write must leave no trace"
    assert "indep" not in RT.snapshot(proj)


def test_reviewers_add_refuses_a_command_preset_under_an_agent_identity(proj):
    code, _out, err = run_cli(proj, "reviewers", "add", "--preset", "claude-cli", agent="bot")
    assert code != 0 and "person" in err, err
    assert "claude-cli" not in RT.snapshot(proj)


def test_reviewers_add_refuses_a_command_preset_inside_a_harness(proj, monkeypatch):
    monkeypatch.setenv(RT.HARNESS_MARKERS[0], "1")
    code, _out, err = run_cli(proj, "reviewers", "add", "--preset", "claude-cli")
    assert code != 0 and "person" in err, err


def test_a_person_may_add_a_command_reviewer_and_it_still_needs_approval(proj, log, cfg):
    code, _out, err = run_cli(proj, "reviewers", "add", "--preset", "claude-cli")
    assert code == 0, err
    ok, why = _independent(proj, log, cfg, "claude-cli")
    assert not ok and "reviewers approve claude-cli" in why, why


# -- tool-written HTTP reviewers count after a person approves ---------------------------


def test_a_tool_written_http_reviewer_is_recorded(proj):
    out = AS.configure(proj, AS.ConfigEdit(append_toml=HTTP, local=True))
    assert out.exit == 0, out.reason
    st = fold(AS._load(proj)[0].read_all())
    dig = RT.digest_of(proj, "lan")
    assert dig and dig in st.reviewer_writes, st.reviewer_writes


def test_its_reviews_do_not_count_until_a_person_approves(proj, log, cfg):
    assert AS.configure(proj, AS.ConfigEdit(append_toml=HTTP, local=True)).exit == 0
    ok, why = _independent(proj, log, cfg, "lan")
    assert not ok and "not approved" in why and "reviewers approve lan" in why, why

    code, out, err = run_cli(proj, "reviewers", "approve", "lan")
    assert code == 0 and "approved" in out, err
    ok, why = G.reviewer_independence(fold(log.read_all()), cfg, "T1", "claude-opus-5")
    assert ok, why


def test_approval_is_for_the_entry_as_approved(proj, log, cfg):
    """A tool changing the endpoint after approval makes a new, unapproved reviewer."""
    from ddflow.services.configwrite import append_block

    assert AS.configure(proj, AS.ConfigEdit(append_toml=HTTP, local=True)).exit == 0
    assert run_cli(proj, "reviewers", "approve", "lan")[0] == 0
    append_block(proj, HTTP.replace("127.0.0.1:9", "127.0.0.1:10"), own="reviewers.toml")
    ok, why = _independent(proj, log, cfg, "lan")
    assert not ok and "reviewers approve lan" in why, why


@pytest.mark.parametrize("how", ["--agent", "env", "harness"])
def test_approve_refuses_under_an_agent_identity(proj, monkeypatch, how):
    assert AS.configure(proj, AS.ConfigEdit(append_toml=HTTP, local=True)).exit == 0
    if how == "env":
        monkeypatch.setenv("DDFLOW_AGENT", "bot")
    if how == "harness":
        monkeypatch.setenv(RT.HARNESS_MARKERS[0], "1")
    code, _out, err = run_cli(
        proj, "reviewers", "approve", "lan", agent="bot" if how == "--agent" else ""
    )
    assert code != 0 and "person" in err, err
    assert not fold(AS._load(proj)[0].read_all()).reviewer_approvals


def test_approve_is_not_an_mcp_tool():
    from ddflow.surfaces import mcp

    assert not [t for t in mcp.TOOLS if "approve" in t], "a person's act has no MCP tool"


# -- nothing the operator configured stops counting ----------------------------------------


def test_a_hand_written_reviewer_counts_as_before(proj, log, cfg):
    (proj / ".ddflow" / "local").mkdir(exist_ok=True)
    (proj / ".ddflow" / "local" / "reviewers.toml").write_text(HTTP)
    ok, why = _independent(proj, log, cfg, "lan")
    assert ok, why


def test_a_hand_written_command_reviewer_counts_as_before(proj, log, cfg):
    (proj / ".ddflow" / "local").mkdir(exist_ok=True)
    (proj / ".ddflow" / "local" / "reviewers.toml").write_text(FAKE_CMD)
    ok, why = _independent(proj, log, cfg, "indep")
    assert ok, why


def test_evidence_recorded_before_this_change_counts_as_before(proj, log, cfg):
    """No digest in the evidence: nothing to judge it by, so it is judged as it was."""
    assert AS.configure(proj, AS.ConfigEdit(append_toml=HTTP, local=True)).exit == 0
    log.append(
        "gate.passed",
        "T1",
        {
            "gate": "critic",
            "by": "gemini-2.5-pro",
            "evidence": {"reviewer": "lan", "model": "gemini-2.5-pro", "family": "google"},
        },
    )
    ok, why = G.reviewer_independence(fold(log.read_all()), cfg, "T1", "claude-opus-5")
    assert ok, why


def test_a_tool_tuning_an_operator_entry_does_not_taint_it(proj, log, cfg):
    """max_tokens is not identity: an agent tuning it must not make the operator's
    reviewer stop counting."""
    (proj / ".ddflow" / "local").mkdir(exist_ok=True)
    (proj / ".ddflow" / "local" / "reviewers.toml").write_text(HTTP)
    tune = '[[reviewer]]\nname = "lan"\nmax_tokens = 64000\n'
    assert AS.configure(proj, AS.ConfigEdit(append_toml=tune, local=True)).exit == 0
    assert not fold(log.read_all()).reviewer_writes
    ok, why = _independent(proj, log, cfg, "lan")
    assert ok, why


def test_record_stamps_the_entry_digest(proj, log, cfg):
    (proj / ".ddflow" / "local").mkdir(exist_ok=True)
    (proj / ".ddflow" / "local" / "reviewers.toml").write_text(HTTP)
    _independent(proj, log, cfg, "lan")
    rec = fold(log.read_all()).items["T1"].gates["critic"]
    assert rec.evidence.get("reviewer_digest") == RT.digest_of(proj, "lan")
