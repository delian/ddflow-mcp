"""B-question-ask-unreachable: a `question` ask (type `question`, empty verb) must be REACHABLE.

`approvals show` / `list` / `pending` (the brief) all filtered on `verb`, so a question --
which has none -- returned "no approval request" and never surfaced; the harness `ask` payload
also refused it, so the person had no surface to read the question or answer it in chat."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli  # noqa: E402

from ddflow.config import Config
from ddflow.infra.log import EventLog
from ddflow.services.ask import engine as E


def _question(repo: Path) -> str:
    log = EventLog(repo, "bot")
    return E.request(
        log, Config(), subject="which color?", text="pick red or blue?", kind=E.QUESTION
    ).id


def test_a_question_can_be_shown(repo):
    """`approvals show <id>` renders a question's text, not "no approval request"."""
    run_cli(repo, "init")
    qid = _question(repo)
    code, out, _e = run_cli(repo, "approvals", "show", qid)
    assert code == 0, out
    assert "pick red or blue?" in out
    assert "no approval request" not in out


def test_a_question_surfaces_in_pending_and_the_brief(repo):
    run_cli(repo, "init")
    qid = _question(repo)
    _c, out, _e = run_cli(repo, "--json", "status")
    pending = json.loads(out).get("approvals_pending", [])
    assert any(r["id"] == qid for r in pending), pending
    code, out, _e = run_cli(repo, "brief")
    assert code == 0 and qid in out, out


def test_a_question_is_listed_with_the_asks(repo):
    run_cli(repo, "init")
    qid = _question(repo)
    code, out, _e = run_cli(repo, "approvals", "list")
    assert code == 0 and qid in out, out
