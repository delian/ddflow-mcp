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
from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services.ask import engine as E
from ddflow.services.approvals import harness as H


def _question(repo: Path, *, options: tuple[str, ...] = ()) -> str:
    log = EventLog(repo, "bot")
    return E.request(
        log,
        Config(),
        subject="which color?",
        text="pick red or blue?",
        kind=E.QUESTION,
        options=options,
    ).id


def _row(repo: Path, qid: str) -> dict:
    return asks_of(fold(EventLog(repo, "bot").read_all(), strict=False))[qid]


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


def test_a_free_text_question_payload_has_no_empty_options(repo):
    """A question with no preset options is typed, not selected: the AskUserQuestion payload
    must not carry the empty `options` list that made it unanswerable."""
    run_cli(repo, "init")
    qid = _question(repo)
    (q,) = H.payload(_row(repo, qid), Config())["questions"]
    assert q["question"] == "pick red or blue?"
    assert "options" not in q, "a free-text question is typed, not selected"


def test_a_question_payload_uses_its_preset_options(repo):
    """A question asked WITH options is a multiple-choice: each option is the answer."""
    run_cli(repo, "init")
    qid = _question(repo, options=("red", "blue"))
    (q,) = H.payload(_row(repo, qid), Config())["questions"]
    assert [o["label"] for o in q["options"]] == ["red", "blue"]
