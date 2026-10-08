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
    from conftest import add_block as append_block

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
    """TOOLS is keyed by tool NAME, so this looks at names (rubber duck asked)."""
    from ddflow.surfaces import mcp

    assert isinstance(mcp.TOOLS, dict) and "ddflow_review" in mcp.TOOLS
    assert not [name for name in mcp.TOOLS if "approve" in name], "a person's act has no tool"


def test_the_approve_hint_names_a_reviewer_with_a_space_whole(proj, log, cfg):
    block = HTTP.replace('name = "lan"', 'name = "lan box"')
    assert AS.configure(proj, AS.ConfigEdit(append_toml=block, local=True)).exit == 0
    ok, why = _independent(proj, log, cfg, "lan box")
    assert not ok and "reviewers approve 'lan box'" in why, why


def test_a_write_fails_closed_when_the_reviewers_do_not_load(proj):
    """roborev: a snapshot that read "no reviewers" whenever the files did not load
    waved through exactly the write the guard exists to stop."""
    (proj / ".ddflow" / "local").mkdir(exist_ok=True)
    (proj / ".ddflow" / "local" / "reviewers.toml").write_text("[[reviewer]\nname = \n")
    assert RT.snapshot(proj) is None
    target = proj / ".ddflow" / "local" / "config.toml"
    before = target.read_text() if target.exists() else None
    out = AS.configure(proj, AS.ConfigEdit(append_toml=FAKE_CMD, local=True))
    assert out.exit != 0 and "do not load" in out.reason, out.reason
    assert (target.read_text() if target.exists() else None) == before


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


def test_a_write_whose_record_cannot_be_appended_is_undone(proj, monkeypatch):
    """rubber duck + critic: a tool-written reviewer with no reviewer.configured would
    count as the operator's, so a failed append undoes the write."""
    target = proj / ".ddflow" / "local" / "config.toml"
    before = target.read_text() if target.exists() else None

    def boom(*a, **k):
        raise TimeoutError("event log lock busy")

    monkeypatch.setattr(RT, "_log", boom)
    out = AS.configure(proj, AS.ConfigEdit(append_toml=HTTP, local=True))
    assert out.exit != 0 and "could not record" in out.reason, out.reason
    assert (target.read_text() if target.exists() else None) == before
    assert "lan" not in (RT.snapshot(proj) or {})


def test_approve_lists_what_waits_and_keeps_the_note(proj, monkeypatch):
    """roborev: the listing works under any identity, and --note is kept and shown."""
    assert AS.configure(proj, AS.ConfigEdit(append_toml=HTTP, local=True)).exit == 0
    code, out, err = run_cli(proj, "reviewers", "approve", agent="bot")
    assert code == 0 and "lan" in out, err
    code, out, err = run_cli(proj, "reviewers", "approve", "lan", "--note", "checked endpoint")
    assert code == 0 and "checked endpoint" in out, err
    st = fold(AS._load(proj)[0].read_all())
    assert [a["note"] for a in st.reviewer_approvals.values()] == ["checked endpoint"]
    assert run_cli(proj, "reviewers", "approve")[0] == 2  # nothing waits any more


# -- the author of a tool-written reviewer is the per-call identity (B6dd8467780) -----------


def _configured_authors(repo: Path) -> set[str]:
    return {e.agent for e in AS._load(repo)[0].read_all() if e.kind == "reviewer.configured"}


def test_configure_records_the_per_call_agent(proj):
    out = AS.configure(proj, AS.ConfigEdit(append_toml=HTTP, local=True), agent="sub-7")
    assert out.exit == 0, out.reason
    assert _configured_authors(proj) == {"sub-7"}


def test_reviewers_detect_write_records_the_per_call_agent(proj, monkeypatch):
    """Follow-up of B6dd8467780: detect --write dropped agent= before append_block."""
    from ddflow.api import review as AR
    from ddflow.services import review as R

    monkeypatch.setattr(
        R, "detect", lambda *a, **k: [("http://127.0.0.1:9/v1", "probe", ["gemini-2.5-pro"])]
    )
    out = AR.reviewers_detect(proj, write=True, agent="sub-9")
    assert out.exit == 0, out.reason
    assert _configured_authors(proj) == {"sub-9"}
