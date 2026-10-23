"""RA-channel-chat: the person TYPES the approval in their own chat.

A user row the harness marked `origin.kind == human` whose whole text is
`approve|deny <request id> <digest prefix>` answers the request at level 2 (same channel and the
same limits as the AskUserQuestion variant: the transcript is the agent's own uid's file; whether
a person or an orchestrating script typed is the harness's classification, not ours).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


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


def _transcript(cfg, rows, name="s1"):
    d = Path(cfg.approvals.transcript_dirs[0]) / "slug"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _typed(text, kind="human", rtype="user", uuid="T1"):
    row = {"type": rtype, "uuid": uuid, "timestamp": "2026-10-23T10:00:00Z",
           "message": {"content": text}}  # fmt: skip
    if kind:
        row["origin"] = {"kind": kind}
    return row


def test_the_person_types_the_approval_and_the_agent_spends_it(log, cfg):
    row = _file(log, cfg)
    _transcript(cfg, [_typed(f"approve {row['id']} {row['digest'][:8]}")])
    ok, message = H.attest(log, cfg, row["id"])
    assert ok, message
    got = asks_of(_state(log))[row["id"]]
    assert got["outcome"] == "approved" and got["proof"] == "typed:T1" and got["via"] == "harness"
    assert E.verified_level(got, cfg) == 2
    assert C.operator_gate(log, cfg, _canon(), approval_id=row["id"]).ok


def test_a_typed_deny_denies(log, cfg):
    row = _file(log, cfg)
    _transcript(cfg, [_typed(f"Deny {row['id']} {row['digest'][:10]}")])
    assert H.attest(log, cfg, row["id"])[0]
    assert not C.operator_gate(log, cfg, _canon(), approval_id=row["id"]).ok


def test_text_blocks_count_like_a_string(log, cfg):
    row = _file(log, cfg)
    block = _typed("")
    block["message"]["content"] = [
        {"type": "text", "text": f"approve {row['id']} {row['digest'][:8]}"}
    ]
    _transcript(cfg, [block])
    assert H.attest(log, cfg, row["id"])[0]


@pytest.mark.parametrize(
    "kind,rtype",
    [("task-notification", "user"), ("", "user"), ("human", "assistant"), ("system", "user")],
)
def test_only_a_row_the_harness_marked_human_counts(log, cfg, kind, rtype):
    row = _file(log, cfg)
    _transcript(cfg, [_typed(f"approve {row['id']} {row['digest'][:8]}", kind=kind, rtype=rtype)])
    ok, message = H.attest(log, cfg, row["id"])
    assert not ok and message.startswith(H.WAITING)


@pytest.mark.parametrize(
    "template",
    [
        "approve {id}",  # no digest
        "approve {id} {d3}",  # too short
        "approve {id} 00000000",  # wrong digest
        "please approve {id} {d8} thanks",  # not the whole message
        "I think we could approve {id} {d8}",  # a quotation inside a sentence
        "approve Qother {d8}",  # another request
        "approve {id} {d8} and also deny {id} {d8}",
    ],
)
def test_the_exact_form_is_required(log, cfg, template):
    row = _file(log, cfg)
    text = template.format(id=row["id"], d8=row["digest"][:8], d3=row["digest"][:3])
    _transcript(cfg, [_typed(text)])
    assert not H.attest(log, cfg, row["id"])[0]
    assert asks_of(_state(log))[row["id"]]["state"] == "pending"


def test_another_requests_approval_cannot_be_replayed(log, cfg):
    first = _file(log, cfg)
    _transcript(cfg, [_typed(f"approve {first['id']} {first['digest'][:8]}")])
    second = _file(log, cfg, _canon(item="T2"))
    assert not H.attest(log, cfg, second["id"])[0]


def test_the_level_holds_only_while_the_typed_row_is_there(log, cfg):
    row = _file(log, cfg)
    _transcript(cfg, [_typed(f"approve {row['id']} {row['digest'][:8]}")])
    assert H.attest(log, cfg, row["id"])[0]
    got = asks_of(_state(log))[row["id"]]
    assert E.verified_level(got, cfg) == 2
    _transcript(cfg, [])
    assert E.verified_level(got, cfg) == 1


def test_the_digest_length_is_the_configured_minimum(log, cfg):
    row = _file(log, cfg)
    cfg.approvals.min_digest_chars = 12
    _transcript(cfg, [_typed(f"approve {row['id']} {row['digest'][:8]}")])
    assert not H.attest(log, cfg, row["id"])[0]
    _transcript(cfg, [_typed(f"approve {row['id']} {row['digest'][:12]}")])
    assert H.attest(log, cfg, row["id"])[0]
