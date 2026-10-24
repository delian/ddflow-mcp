"""reviewers.approve by an agent (RA-consume-sites-3): an HTTP entry a tool wrote can be vouched
for with the operator's chat approval; a command reviewer (it runs a program) is T3, terminal only."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import setup as AS
from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import reviewer_trust as RT

HTTP = (
    '[[reviewer]]\nname = "lan"\nbase_url = "http://127.0.0.1:9/v1"\n'
    'model = "gemini-2.5-pro"\nfamily = "google"\ngates = ["critic"]\n'
)


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *RT.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    out = AS.configure(repo, AS.ConfigEdit(append_toml=HTTP, local=True))
    assert out.exit == 0, out.reason
    return repo


def _file(repo) -> Path:
    """The file a tool-written reviewer entry went to."""
    return next(p for p in RT._files(repo) if p.exists() and "lan" in p.read_text())


def _state(repo):
    return fold(EventLog(repo, "probe").read_all(), strict=False)


def _ask_id(text: str) -> str:
    found = re.search(r"approval (Q[0-9a-f]+)", text)
    assert found, text
    return found.group(1)


def _answer(repo, ask_id):
    digest = asks_of(_state(repo))[ask_id]["digest"]
    assert run_cli(repo, "approvals", "approve", ask_id, "--digest", digest[:12])[0] == 0


def test_an_agent_vouches_for_a_tool_written_http_reviewer_with_the_operators_approval(proj):
    code, _o, err = run_cli(proj, "reviewers", "approve", "lan", agent="bot")
    assert code == 3 and "approval Q" in err and "--approval" in err
    ask_id = _ask_id(err)
    row = asks_of(_state(proj))[ask_id]
    assert row["verb"] == "reviewers.approve" and row["requested_by"] == "bot"  # the named agent
    _answer(proj, ask_id)
    code, out, err = run_cli(proj, "reviewers", "approve", "lan", "--approval", ask_id, agent="bot")
    assert code == 0, (out, err)
    assert not RT.pending(proj, _state(proj))  # the entry is approved now


def test_the_approval_is_for_that_entry_only(proj):
    ask_id = _ask_id(run_cli(proj, "reviewers", "approve", "lan", agent="bot")[2])
    _answer(proj, ask_id)
    cfg = _file(proj)
    cfg.write_text(cfg.read_text().replace("gemini-2.5-pro", "another-model"))  # the entry changed
    code, _o, err = run_cli(proj, "reviewers", "approve", "lan", "--approval", ask_id, agent="bot")
    assert code == 3 and "other state" in err
    assert not _state(proj).approvals.get("reviewer:lan")


def test_a_command_reviewer_is_never_approved_over_chat(proj):
    cmd = (
        '[[reviewer]]\nname = "prog"\nkind = "command"\ncommand = "true"\n'
        'model = "gemini-2.5-pro"\nfamily = "google"\ngates = ["critic"]\n'
    )
    path = _file(proj)
    path.write_text(path.read_text() + "\n" + cmd)
    code, _o, err = run_cli(proj, "reviewers", "approve", "prog", agent="bot")
    assert code == 3 and "T3: terminal only" in err
    assert not asks_of(_state(proj))  # no request is filed for it
    assert run_cli(proj, "reviewers", "approve", "prog")[0] == 0  # a person at a terminal can


def test_an_agent_is_not_told_which_reviewers_exist(proj):
    code, _o, err = run_cli(proj, "reviewers", "approve", "nosuch", agent="bot")
    assert code == 3 and "lan" not in err and "no reviewer named" not in err
    code, _o, err = run_cli(proj, "reviewers", "approve", "nosuch")  # a person is told
    assert code == 3 and "lan" in err and "no reviewer named" in err
