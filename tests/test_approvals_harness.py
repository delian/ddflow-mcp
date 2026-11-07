"""RA-channel-harness: the person answers an AskUserQuestion in the agent's chat and ddflow reads
the answer from the transcript the harness wrote -- never from the agent's word.

The transcripts here are fabricated in the shape read from a real Claude Code session
(research/remote-approvals-probe.md). Their forgeability by the agent's own uid is the
documented limit; what is tested is that nothing the AGENT SAYS can stand in for the row.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.core.handlers.asks import asks_of
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import approval as AP
from ddflow.services.approvals import consume as C
from ddflow.services.approvals import harness as H
from ddflow.services.approvals import request as R
from ddflow.services.approvals.canon import Canon
from ddflow.services.ask import engine as E

VERB = "gate.skip.failed_review"


@pytest.fixture(autouse=True)
def _a_person(monkeypatch, tmp_path):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)
    # Never reach the machine's real Kilo store (it is live and huge) from these tests: the
    # Kilo tests below point transcript_dirs at their own SQLite file instead.
    monkeypatch.setattr(H, "KILO_DB", tmp_path / "kilo-does-not-exist.db")


@pytest.fixture
def log(repo):
    return EventLog(repo, "bot")


@pytest.fixture
def cfg(tmp_path):
    c = Config()
    c.approvals.transcript_dirs = [str(tmp_path / "projects")]
    return c


def _state(log):
    return fold(log.read_all(), strict=False)


def _canon(**over):
    base = {"verb": VERB, "agent": "bot", "item": "T1", "target": "critic", "state": "s1"}
    base.update(over)
    return Canon(**base)


def _file(log, cfg, canon=None):
    filed = R.file(log, cfg, _state(log), canon or _canon(), reason="r")
    return asks_of(_state(log))[filed.id]


def _transcript(cfg, name, rows):
    d = Path(cfg.approvals.transcript_dirs[0]) / "slug"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def _asked(question, uid="A1", tool_id="U1"):
    return {
        "type": "assistant", "uuid": uid,
        "message": {"content": [{"type": "tool_use", "id": tool_id, "name": H.TOOL,
                                 "input": {"questions": [{"question": question}]}}]},
    }  # fmt: skip


def _answered(question, answer, uid="A1", tool_id="U1", decision="accept"):
    row = {
        "type": "user", "uuid": "R1", "sourceToolAssistantUUID": uid, "timestamp": "2026-10-23T10:00:00Z",
        "toolUseResult": {"answers": {question: answer}},
        "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "x"}]},
    }  # fmt: skip
    if decision:
        row["permissionDecision"] = {"decision": decision, "source": "user_temporary"}
    return row


def _approved_row(row):
    yes, _no = H.labels(row["id"])
    return [_asked(row["text"]), _answered(row["text"], yes)]


def test_the_payload_is_the_rendered_text_with_three_labelled_options_for_a_generic_request(
    log, cfg
):
    row = _file(log, cfg)
    body = H.payload(row, cfg)
    (q,) = body["questions"]
    assert q["question"] == row["text"]
    assert row["id"] in row["text"] and row["digest"][:16] in row["text"]
    assert [o["label"] for o in q["options"]] == [*H.labels(row["id"]), H.window_label(row["id"])]
    assert all(o["description"] for o in q["options"])


def _kilo_question(question, answer, *, status="completed", pid="prt_1"):
    """One Kilo `question` tool part (a `part` row's `data`): the harness-written record. An
    unanswered question has `status` ``in_progress`` and no `metadata.answers`."""
    state = {
        "status": status,
        "input": {"questions": [{"question": question}]},
    }
    if answer is not None:
        state["metadata"] = {"answers": [[answer]], "truncated": False}
    return {
        "id": pid,
        "data": {"type": "tool", "tool": H.KILO_TOOL, "callID": "call_1", "state": state},
    }


def _kilo_text(text, pid="prt_txt"):
    """A Kilo text part: what the AGENT wrote. It is not a question tool part, so it is not
    an answer, however it is worded."""
    return {"id": pid, "data": {"type": "text", "text": text}}


def _kilo_db(cfg, parts):
    """A minimal `part` table shaped like Kilo's store, at the path `transcript_dirs` names."""
    import time as _time

    path = Path(cfg.approvals.transcript_dirs[0])
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE part (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, "
        "time_created INTEGER NOT NULL, data TEXT NOT NULL)"
    )
    for i, part in enumerate(parts):
        con.execute(
            "INSERT INTO part (id, session_id, time_created, data) VALUES (?, ?, ?, ?)",
            (part["id"], "ses_test", int(_time.time() * 1000) + i, json.dumps(part["data"])),
        )
    con.commit()
    con.close()
    return path


def _kilo_cfg(cfg):
    """A config whose harness transcript store is a Kilo SQLite file, not a JSONL directory."""
    cfg.approvals.transcript_dirs = [str(Path(cfg.approvals.transcript_dirs[0]).parent / "kilo.db")]
    return cfg


def test_a_non_generic_request_offers_no_standing_window_choice(log, cfg):
    cfg.approvals.standing_verbs = ["complete.force", "claim.force", "approve.partial"]
    row = _file(log, cfg)  # gate.skip.failed_review is not in the (narrowed) generic scope
    (q,) = H.payload(row, cfg)["questions"]
    assert [o["label"] for o in q["options"]] == [*H.labels(row["id"])]


def test_the_window_choice_approves_and_enables_the_standing_window(log, cfg, repo):
    import tomllib

    run_cli(repo, "init")
    row = _file(log, cfg)
    _transcript(cfg, "s1", [_asked(row["text"]), _answered(row["text"], H.window_label(row["id"]))])
    ok, message = H.attest(log, cfg, row["id"], repo=repo)
    assert ok, message
    got = asks_of(_state(log))[row["id"]]
    assert got["outcome"] == "approved" and got["via"] == "harness" and got["proof"] == "U1"
    data = tomllib.loads((repo / ".ddflow" / "config.toml").read_text())
    assert data["approvals"]["standing_window_s"] == H.WINDOW_CHOICE_HOURS * 3600
    assert data["approvals"]["policy"] == list(H.WINDOW_CHOICE_POLICY)


def test_an_answer_in_the_transcript_approves_and_the_agent_spends_it(log, cfg):
    row = _file(log, cfg)
    _transcript(cfg, "s1", _approved_row(row))
    ok, message = H.attest(log, cfg, row["id"])
    assert ok, message
    got = asks_of(_state(log))[row["id"]]
    assert got["outcome"] == "approved" and got["via"] == "harness" and got["proof"] == "U1"
    assert E.verified_level(got, cfg) == 2
    spent = C.operator_gate(log, cfg, _canon(), approval_id=row["id"])
    assert spent.ok and spent.stamp["via"] == "harness" and spent.stamp["level"] == 2


def test_a_deny_label_denies(log, cfg):
    row = _file(log, cfg)
    _yes, no = H.labels(row["id"])
    _transcript(cfg, "s1", [_asked(row["text"]), _answered(row["text"], no)])
    assert H.attest(log, cfg, row["id"])[0]
    got = C.operator_gate(log, cfg, _canon(), approval_id=row["id"])
    assert not got.ok and "denied" in got.message


@pytest.mark.parametrize(
    "answer", ["Other: sure, go ahead", "Approve Qsomeoneelse", "approve", ["Approve", "Deny"], ""]
)
def test_only_the_two_labels_count(log, cfg, answer):
    row = _file(log, cfg)
    _transcript(cfg, "s1", [_asked(row["text"]), _answered(row["text"], answer)])
    ok, message = H.attest(log, cfg, row["id"])
    assert not ok and asks_of(_state(log))[row["id"]]["state"] == "pending", message


def test_what_the_agent_says_is_not_an_answer(log, cfg):
    """No AskUserQuestion at all: assistant text and a user-looking line claiming approval."""
    row = _file(log, cfg)
    yes, _ = H.labels(row["id"])
    claim = {"type": "assistant", "uuid": "A9", "message": {"content": [
        {"type": "text", "text": f"The operator said: {yes}"}]}}  # fmt: skip
    note = {"type": "user", "uuid": "U9", "message": {"content": f"{yes}"}}
    _transcript(cfg, "s1", [claim, note])
    ok, message = H.attest(log, cfg, row["id"])
    assert not ok and message.startswith(H.WAITING)


@pytest.mark.parametrize(
    "fault", ["no_decision", "denied_decision", "wrong_source_row", "no_tool_use"]
)
def test_a_row_that_is_not_the_harnesss_shape_is_refused(log, cfg, fault):
    row = _file(log, cfg)
    yes, _ = H.labels(row["id"])
    rows = [_asked(row["text"]), _answered(row["text"], yes)]
    if fault == "no_decision":
        rows[1].pop("permissionDecision")
    elif fault == "denied_decision":
        rows[1]["permissionDecision"] = {"decision": "deny"}
    elif fault == "wrong_source_row":
        rows[1]["sourceToolAssistantUUID"] = "other"
    elif fault == "no_tool_use":
        rows = rows[1:]
    _transcript(cfg, "s1", rows)
    assert not H.attest(log, cfg, row["id"])[0]


def test_an_answer_to_another_request_cannot_be_replayed(log, cfg):
    first = _file(log, cfg)
    _transcript(cfg, "old", _approved_row(first))
    second = _file(log, cfg, _canon(item="T2"))
    ok, _m = H.attest(log, cfg, second["id"])
    assert (
        not ok
    )  # the question text carries the id and digest: another request's answer is not this one's


def test_the_channel_can_be_turned_off_and_never_reaches_a_t3_verb(log, cfg):
    row = _file(log, cfg)
    _transcript(cfg, "s1", _approved_row(row))
    cfg.approvals.accept_harness = False
    assert "off" in H.attest(log, cfg, row["id"])[1]
    cfg.approvals.accept_harness = True
    t3 = _file(log, cfg, _canon(verb="config.reviewer_command", item="", target=""))
    _transcript(cfg, "s2", _approved_row(t3))
    ok, message = H.attest(log, cfg, t3["id"])
    assert not ok and "never approved over chat" in message


def test_the_level_is_believed_only_while_the_transcript_still_holds_the_answer(log, cfg):
    row = _file(log, cfg)
    path = _transcript(cfg, "s1", _approved_row(row))
    assert H.attest(log, cfg, row["id"])[0]
    got = asks_of(_state(log))[row["id"]]
    assert E.verified_level(got, cfg) == 2
    path.write_text("")  # the proof is gone (or never was)
    assert E.verified_level(got, cfg) == 1
    # and an answer line written by hand that CLAIMS the harness has no proof to show
    other = _file(log, cfg, _canon(item="T3"))
    log.append("ask.answered", other["id"], {"id": other["id"], "outcome": "approved", "by": "me",
               "digest": other["digest"], "human": True, "source": "credential", "via": "harness",
               "level": 2, "proof": "U1"})  # fmt: skip
    assert E.verified_level(asks_of(_state(log))[other["id"]], cfg) == 1


def test_ask_and_attest_over_the_cli(repo, tmp_path):
    """The two agent-run steps through the CLI: ask prints the payload, attest records the answer
    once the transcript holds it (the gate-skip scenario on top is in test_approvals_gate_skip)."""
    run_cli(repo, "init")
    projects = tmp_path / "projects"
    cfgfile = repo / ".ddflow" / "config.toml"
    cfgfile.write_text(cfgfile.read_text() + f"\n[approvals]\ntranscript_dirs = ['{projects}']\n")
    log = EventLog(repo, "bot")
    row = _file(log, Config())
    code, out, err = run_cli(repo, "--json", "approvals", "ask", row["id"], agent="bot")
    assert code == 0, err
    question = json.loads(out)["input"]["questions"][0]["question"]
    assert question == row["text"]
    assert (
        run_cli(repo, "approvals", "attest", row["id"], agent="bot")[0] == 2
    )  # nothing answered yet
    d = projects / "slug"
    d.mkdir(parents=True)
    rows = [_asked(question), _answered(question, f"Approve {row['id']}")]
    (d / "s.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    code, out, err = run_cli(repo, "approvals", "attest", row["id"], agent="bot")
    assert code == 0, (out, err)
    code, out, _e = run_cli(repo, "approvals", "list")
    assert "approved, 0/1 used" in out
    here = Config()
    here.approvals.transcript_dirs = [str(projects)]
    spent = C.operator_gate(log, here, _canon(), approval_id=row["id"])
    assert spent.ok and spent.stamp["via"] == "harness" and spent.stamp["level"] == 2


def test_a_later_duplicate_answer_does_not_undo_a_recorded_one(log, cfg):
    row = _file(log, cfg)
    yes, _ = H.labels(row["id"])
    rows = _approved_row(row)
    path = _transcript(cfg, "s1", rows)
    assert H.attest(log, cfg, row["id"])[0]
    again = [
        _asked(row["text"], uid="A2", tool_id="U2"),
        _answered(row["text"], yes, uid="A2", tool_id="U2"),
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows + again))
    assert E.verified_level(asks_of(_state(log))[row["id"]], cfg) == 2


def test_ask_refuses_a_verb_that_chat_can_never_approve(log, cfg, repo):
    from ddflow.api import approvals_harness as AH

    run_cli(repo, "init")
    t3 = _file(log, cfg, _canon(verb="config.reviewer_command", item="", target=""))
    out = AH.approvals_ask(repo, t3["id"], agent="bot")
    assert out.exit == 3 and "never approved over chat" in out.reason


# -- the Kilo channel (RA-channel-harness + Kilo's SQLite store, not JSONL) -------------------


def test_a_kilo_question_answer_attests_and_the_agent_spends_it(log, cfg):
    row = _file(log, _kilo_cfg(cfg))
    yes, _ = H.labels(row["id"])
    _kilo_db(cfg, [_kilo_question(row["text"], yes)])
    ok, message = H.attest(log, cfg, row["id"])
    assert ok, message
    got = asks_of(_state(log))[row["id"]]
    assert got["outcome"] == "approved" and got["via"] == "harness" and got["proof"] == "kilo:prt_1"
    assert E.verified_level(got, cfg) == 2
    spent = C.operator_gate(log, cfg, _canon(), approval_id=row["id"])
    assert spent.ok and spent.stamp["via"] == "harness" and spent.stamp["level"] == 2


def test_a_kilo_deny_label_denies(log, cfg):
    row = _file(log, _kilo_cfg(cfg))
    _, no = H.labels(row["id"])
    _kilo_db(cfg, [_kilo_question(row["text"], no)])
    assert H.attest(log, cfg, row["id"])[0]
    got = C.operator_gate(log, cfg, _canon(), approval_id=row["id"])
    assert not got.ok and "denied" in got.message


def test_a_kilo_text_part_is_not_an_answer(log, cfg):
    """The agent's own word in Kilo is a text part, not a `question` tool part: never an answer."""
    row = _file(log, _kilo_cfg(cfg))
    _kilo_db(cfg, [_kilo_text(f"Approve {row['id']}")])
    ok, message = H.attest(log, cfg, row["id"])
    assert not ok and message.startswith(H.WAITING)


def test_a_kilo_uncompleted_question_is_not_an_answer(log, cfg):
    row = _file(log, _kilo_cfg(cfg))
    _kilo_db(cfg, [_kilo_question(row["text"], None, status="in_progress")])
    ok, message = H.attest(log, cfg, row["id"])
    assert not ok and message.startswith(H.WAITING)


def test_a_malformed_kilo_part_does_not_crash(log, cfg):
    """A `question` part whose metadata is a list, not a dict (the reviewer's crafted row)
    must degrade to WAITING, never raise."""
    row = _file(log, _kilo_cfg(cfg))
    bad = {
        "id": "prt_bad",
        "data": {
            "type": "tool",
            "tool": H.KILO_TOOL,
            "callID": "call_bad",
            "state": {
                "status": "completed",
                "input": {"questions": [{"question": row["text"]}]},
                "metadata": [["yes"]],
            },
        },
    }
    _kilo_db(cfg, [bad])
    ok, message = H.attest(log, cfg, row["id"])
    assert not ok and message.startswith(H.WAITING)


def test_a_kilo_answer_to_another_request_cannot_be_replayed(log, cfg):
    first = _file(log, _kilo_cfg(cfg))
    _kilo_db(cfg, [_kilo_question(first["text"], H.labels(first["id"])[0])])
    second = _file(log, cfg, _canon(item="T2"))
    assert not H.attest(log, cfg, second["id"])[0]


def test_a_kilo_question_answer_never_satisfies_a_t3_verb(log, cfg):
    t3 = _file(log, _kilo_cfg(cfg), _canon(verb="config.reviewer_command", item="", target=""))
    _kilo_db(cfg, [_kilo_question(t3["text"], H.labels(t3["id"])[0])])
    ok, message = H.attest(log, cfg, t3["id"])
    assert not ok and "never approved over chat" in message


def test_a_kilo_answer_holds_only_while_the_part_is_there(log, cfg):
    row = _file(log, _kilo_cfg(cfg))
    yes, _ = H.labels(row["id"])
    path = _kilo_db(cfg, [_kilo_question(row["text"], yes)])
    assert H.attest(log, cfg, row["id"])[0]
    got = asks_of(_state(log))[row["id"]]
    assert E.verified_level(got, cfg) == 2
    path.unlink()  # the proof is gone (or never was)
    assert E.verified_level(got, cfg) == 1


def test_the_newest_answer_across_both_channels_wins(log, cfg):
    """The age sort in find_all: the person answered once in Claude (older) and once in Kilo
    (older still); find must return the NEWEST, whichever channel held it (roborev finding:
    the sort's own defect class needed a regression test)."""
    root = Path(cfg.approvals.transcript_dirs[0]).parent
    kilo = root / "kilo.db"
    claude = root / "claude"
    cfg.approvals.transcript_dirs = [str(claude), str(kilo)]

    row = _file(log, cfg)
    yes, _ = H.labels(row["id"])

    con = sqlite3.connect(kilo)
    con.execute(
        "CREATE TABLE part (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, "
        "time_created INTEGER NOT NULL, data TEXT NOT NULL)"
    )
    part = _kilo_question(row["text"], yes)
    con.execute(
        "INSERT INTO part (id, session_id, time_created, data) VALUES (?, ?, ?, ?)",
        (part["id"], "ses_test", 1000, json.dumps(part["data"])),  # 1970: oldest
    )
    con.commit()
    con.close()

    cdir = claude / "slug"
    cdir.mkdir(parents=True)
    (cdir / "s1.jsonl").write_text(
        "\n".join(json.dumps(r) for r in [_asked(row["text"]), _answered(row["text"], yes)]) + "\n"
    )
    assert H.find(cfg, row).tool_use_id == "U1"  # 2026-10-23 > 1970: the newer Claude answer


def test_an_answer_in_kilo_to_the_canonical_ask_is_auto_attested_to_approved(log, cfg):
    """The auto-attest loop (B3068414423): after the operator answers the surfaced CANONICAL
    question in Kilo, `attest_pending` attests it without the agent naming the id -- a chat
    answer alone flips the request to approved, no terminal, no `--all` approve."""
    row = _file(log, _kilo_cfg(cfg))
    yes, _ = H.labels(row["id"])
    _kilo_db(cfg, [_kilo_question(row["text"], yes)])
    results = H.attest_pending(log, cfg)
    assert any(r["id"] == row["id"] and r["ok"] for r in results)
    got = asks_of(_state(log))[row["id"]]
    assert got["outcome"] == "approved" and got["via"] == "harness" and got["proof"] == "kilo:prt_1"
    spent = C.operator_gate(log, cfg, _canon(), approval_id=row["id"])
    assert spent.ok and spent.stamp["via"] == "harness" and spent.stamp["level"] == 2


def test_auto_attest_leaves_a_request_without_an_answer_pending(log, cfg):
    """Auto-attest waits, never invents: a pending request with no transcript answer is not
    attested, and the run reports it still waiting."""
    row = _file(log, _kilo_cfg(cfg))
    results = H.attest_pending(log, cfg)
    assert any(r["id"] == row["id"] and not r["ok"] for r in results)
    assert asks_of(_state(log))[row["id"]]["state"] == "pending"


def test_attest_all_over_the_cli_auto_attests(log, cfg, repo):
    """`ddflow approvals attest --all`: one agent-run call after the operator answers attests
    every pending request whose canonical question is answered, no id named."""
    run_cli(repo, "init")
    projects = Path(cfg.approvals.transcript_dirs[0])
    cfgfile = repo / ".ddflow" / "config.toml"
    cfgfile.write_text(cfgfile.read_text() + f"\n[approvals]\ntranscript_dirs = ['{projects}']\n")
    log = EventLog(repo, "bot")
    row = _file(log, Config())
    code, out, err = run_cli(repo, "approvals", "attest", "--all", agent="bot")
    assert code == 2, (out, err)  # nothing answered yet
    d = projects / "slug"
    d.mkdir(parents=True)
    (d / "s.jsonl").write_text("\n".join(json.dumps(r) for r in _approved_row(row)))
    code, out, err = run_cli(repo, "approvals", "attest", "--all", agent="bot")
    assert code == 0, (out, err)
    here = Config()
    here.approvals.transcript_dirs = [str(projects)]
    spent = C.operator_gate(log, here, _canon(), approval_id=row["id"])
    assert spent.ok and spent.stamp["via"] == "harness" and spent.stamp["level"] == 2


def test_attest_with_no_id_and_no_all_is_refused_not_a_misdispatch(log, cfg, repo):
    """`ddflow approvals attest` with no id and no --all returns a usage refusal, never a parse
    error that silently attests an empty id (review finding)."""
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "approvals", "attest", agent="bot")
    assert code == 3 and "request id" in err, (out, err)


def test_attest_all_with_a_positional_id_is_refused(log, cfg, repo):
    """`ddflow approvals attest --all <id>` is refused, not silently ignoring the id (the guard
    mirrors `approve --all` with ids; roborev/round-3 finding)."""
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "approvals", "attest", "--all", "Qwhatever", agent="bot")
    assert code == 3 and "--all" in err, (out, err)


def test_attest_all_reports_a_refused_request_beside_an_attested_one(log, cfg, repo):
    """`attest --all` is not an overall success when one answered request attests and another
    is refused: the refusal is reported, never dropped behind the ok (review finding)."""
    run_cli(repo, "init")
    projects = Path(cfg.approvals.transcript_dirs[0])
    cfgfile = repo / ".ddflow" / "config.toml"
    cfgfile.write_text(cfgfile.read_text() + f"\n[approvals]\ntranscript_dirs = ['{projects}']\n")
    log = EventLog(repo, "bot")
    row = _file(log, Config())  # T2, answerable over chat
    _file(log, Config(), _canon(verb="config.reviewer_command", item="", target=""))  # T3: refused
    d = projects / "slug"
    d.mkdir(parents=True)
    (d / "s.jsonl").write_text("\n".join(json.dumps(r) for r in _approved_row(row)))
    code, out, err = run_cli(repo, "approvals", "attest", "--all", agent="bot")
    assert code == 3, (out, err)  # attested one, refused one: not a clean success
    assert "refused" in err
    got = asks_of(fold(EventLog(repo, "bot").read_all(), strict=False))[row["id"]]
    assert got["outcome"] == "approved" and got["via"] == "harness"


def test_harness_imports_no_http_roots_and_holds_no_http_home_entry():
    """Regression for Baf66b3bd2b: the harness channel reads Kilo's transcript store through
    ``infra.docindex``, not ``sqlite3``/``urllib``, so ``harness.py`` imports no
    ``urllib``/``http``/``socket`` and the http-home ratchet holds no entry for it. A stale
    entry (listed but no matching import) reds ``test_import_contracts``; a re-added import
    belongs in ``ddflow.adapters.http``, never here."""
    import ast
    import tomllib

    here = Path(__file__).resolve().parents[1]
    harness = here / "ddflow" / "services" / "approvals" / "harness.py"
    roots = set()
    for node in ast.parse(harness.read_text("utf-8")).body:
        if isinstance(node, ast.Import):
            roots.update(n.name.split(".")[0] for n in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    assert not roots & {"urllib", "http", "socket"}, (
        f"{harness.name} imports {sorted(roots & {'urllib', 'http', 'socket'})}; "
        "the harness channel reads the transcript through infra.docindex, not the network"
    )
    baseline = tomllib.loads(
        (here / "tests" / "guard_baselines" / "importlinter-http-home.toml").read_text("utf-8")
    )
    stale = [
        e for e in baseline["ignore_imports"]
        if e.split(" -> ")[0] == "ddflow.services.approvals.harness"
    ]
    assert not stale, f"stale http-home entry for a module that imports none of it: {stale}"
