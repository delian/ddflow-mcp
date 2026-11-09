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
    """A question with no preset options is typed, not selected: the surfaced question embeds
    the ask id (the transcript scanner filters on it) and carries no empty `options` list."""
    run_cli(repo, "init")
    qid = _question(repo)
    (q,) = H.payload(_row(repo, qid), Config())["questions"]
    assert "pick red or blue?" in q["question"]
    assert qid in q["question"]
    assert "options" not in q, "a free-text question is typed, not selected"


def test_a_question_payload_uses_its_preset_options(repo):
    """A question asked WITH options is a multiple-choice: each option is the answer."""
    run_cli(repo, "init")
    qid = _question(repo, options=("red", "blue"))
    (q,) = H.payload(_row(repo, qid), Config())["questions"]
    assert [o["label"] for o in q["options"]] == ["red", "blue"]
    assert all(o["description"] for o in q["options"])


def test_a_free_text_question_answer_is_attested(repo, tmp_path):
    """The answer round-trip: the surfaced question embeds its id (the scanner filters on it)
    and the person's free text is recorded by attest."""
    run_cli(repo, "init")
    projects = tmp_path / "projects"
    cfgfile = repo / ".ddflow" / "config.toml"
    cfgfile.write_text(cfgfile.read_text() + f"\n[approvals]\ntranscript_dirs = ['{projects}']\n")
    log = EventLog(repo, "bot")
    qid = _question(repo)
    row = _row(repo, qid)
    cfg = Config()
    cfg.approvals.transcript_dirs = [str(projects)]
    surfaced = H._surfaced_question(row)
    asked = {
        "type": "assistant",
        "uuid": "A1",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "U1",
                    "name": H.TOOL,
                    "input": {"questions": [{"question": surfaced}]},
                },
            ]
        },  # fmt: skip
    }
    answered = {
        "type": "user",
        "uuid": "R1",
        "sourceToolAssistantUUID": "A1",
        "timestamp": "2026-10-23T10:00:00Z",
        "toolUseResult": {"answers": {surfaced: "red"}},
        "message": {"content": [{"type": "tool_result", "tool_use_id": "U1", "content": "x"}]},
        "permissionDecision": {"decision": "accept", "source": "user_temporary"},
    }
    d = projects / "slug"
    d.mkdir(parents=True)
    (d / "s.jsonl").write_text("\n".join(json.dumps(r) for r in [asked, answered]))
    ok, message = H.attest(log, cfg, qid)
    assert ok, message
    answered_row = _row(repo, qid)
    assert answered_row["outcome"] == E.ANSWERED_WORD
    assert answered_row["answer"] == "red"


def test_a_multiple_choice_question_answer_is_attested(repo, tmp_path):
    """The multiple-choice round-trip mirrors the free-text one: the selected option becomes
    the outcome, the answer stays empty, and verify passes against that convention."""
    run_cli(repo, "init")
    projects = tmp_path / "projects"
    cfgfile = repo / ".ddflow" / "config.toml"
    cfgfile.write_text(cfgfile.read_text() + f"\n[approvals]\ntranscript_dirs = ['{projects}']\n")
    log = EventLog(repo, "bot")
    qid = _question(repo, options=("red", "blue"))
    row = _row(repo, qid)
    cfg = Config()
    cfg.approvals.transcript_dirs = [str(projects)]
    surfaced = H._surfaced_question(row)
    asked = {
        "type": "assistant",
        "uuid": "A1",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "U1",
                    "name": H.TOOL,
                    "input": {"questions": [{"question": surfaced}]},
                },
            ]
        },  # fmt: skip
    }
    answered = {
        "type": "user",
        "uuid": "R1",
        "sourceToolAssistantUUID": "A1",
        "timestamp": "2026-10-23T10:00:00Z",
        "toolUseResult": {"answers": {surfaced: "red"}},
        "message": {"content": [{"type": "tool_result", "tool_use_id": "U1", "content": "x"}]},
        "permissionDecision": {"decision": "accept", "source": "user_temporary"},
    }
    d = projects / "slug"
    d.mkdir(parents=True)
    (d / "s.jsonl").write_text("\n".join(json.dumps(r) for r in [asked, answered]))
    ok, message = H.attest(log, cfg, qid)
    assert ok, message
    answered_row = _row(repo, qid)
    assert answered_row["outcome"] == "red"
    assert answered_row["answer"] == ""
    assert H.verify(answered_row, cfg)
