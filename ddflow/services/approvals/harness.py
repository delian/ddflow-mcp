"""The harness channel (L2): a person answers an AskUserQuestion in the agent's own chat and
ddflow reads the answer from the transcript the HARNESS wrote, never from the agent (RA-channel-harness,
D-RA-Q5, research/remote-approvals-probe.md).

Flow: `ddflow approvals ask ID` prints the exact AskUserQuestion payload (the question is the text
ddflow rendered, with the ask id and digest, and two labelled options); the agent puts it to the
person; `ddflow approvals attest ID` finds the harness-written answer row in the session transcript
and records the ask answered at level 2 with the tool_use id as proof. The agent only couriers:
a row counts only when it is an accepted result for an AskUserQuestion whose tool_use asked
EXACTLY this question, with row ids that line up, after the request was filed.

What this does not prove (said in the probe and by `doctor`): that a person, not a script holding
the session, answered, and that the transcript file was not edited -- it is the agent's own uid's
file. Accepted for tier T2 only (a T3 verb needs level 4, which this never reaches).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...config import Config
from ...core import clock
from ...core.model import fold
from ..actors import resolve_actor
from ..ask import engine as E

TOOL = "AskUserQuestion"
#: attest's message begins with this while the person has simply not answered yet (exit 2)
WAITING = "nothing to attest yet: "
VIA = "harness"
LEVEL = 2


@dataclass(frozen=True)
class Attested:
    tool_use_id: str
    answer: str
    at: str
    file: str


def labels(ask_id: str) -> tuple[str, str]:
    return (f"Approve {ask_id}", f"Deny {ask_id}")


def payload(row: dict[str, Any]) -> dict[str, Any]:
    """The AskUserQuestion input the agent must send, unaltered."""
    yes, no = labels(row["id"])
    return {
        "questions": [
            {
                "question": row["text"],
                "header": "Approval",
                "multiSelect": False,
                "options": [
                    {"label": yes, "description": "approve exactly this command, once"},
                    {"label": no, "description": "deny it"},
                ],
            }
        ]
    }


def _dirs(cfg: Config) -> list[Path]:
    return [Path(d).expanduser() for d in cfg.approvals.transcript_dirs]


def _files(cfg: Config, since: float) -> list[Path]:
    found: list[Path] = []
    for base in _dirs(cfg):
        if base.is_dir():
            found += [p for p in base.rglob("*.jsonl") if p.stat().st_mtime >= since]
    return sorted(found, key=lambda p: p.stat().st_mtime)


def _text(answer: Any) -> str:
    if isinstance(answer, str):
        return answer
    return answer[0] if isinstance(answer, list) and len(answer) == 1 else ""


def _asks(row: dict[str, Any], content: list, question: str) -> dict[str, str | None]:
    """The AskUserQuestion tool_uses of an assistant row that ask exactly ``question``."""
    out: dict[str, str | None] = {}
    for b in content:
        if not (isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == TOOL):
            continue
        asked = (b.get("input") or {}).get("questions")
        if isinstance(asked, list) and any(
            isinstance(q, dict) and q.get("question") == question for q in asked
        ):
            out[str(b.get("id"))] = row.get("uuid")
    return out


def _accepted(row: dict[str, Any], uuid: str | None, question: str) -> str:
    """The answer text of an accepted harness result to ``question``, or ""."""
    answers = (row.get("toolUseResult") or {}).get("answers")
    ok = (
        uuid
        and (row.get("permissionDecision") or {}).get("decision") == "accept"
        and row.get("sourceToolAssistantUUID") == uuid
        and isinstance(answers, dict)
    )
    return _text(answers.get(question)) if ok else ""


def _results(row: dict[str, Any], content: list, uses: dict, question: str, name: str):
    for b in content:
        if isinstance(b, dict) and b.get("type") == "tool_result":
            tid = str(b.get("tool_use_id"))
            if tid in uses and (ans := _accepted(row, uses[tid], question)):
                yield Attested(tid, ans, str(row.get("timestamp", "")), name)


def scan(path: Path, question: str, marker: str) -> list[Attested]:
    """The accepted answers to exactly ``question`` in one transcript (``marker`` is a cheap
    substring every line about it contains: the ask id)."""
    uses: dict[str, str | None] = {}  # tool_use id -> uuid of the assistant row that asked
    found: list[Attested] = []
    for line in path.read_text(errors="replace").splitlines():
        if marker not in line and not any(u in line for u in uses):
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        content = (row.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        if row.get("type") == "assistant":
            uses.update(_asks(row, content, question))
        elif row.get("type") == "user":
            found.extend(_results(row, content, uses, question, path.name))
    return found


def find_all(cfg: Config, row: dict[str, Any]) -> list[Attested]:
    """Every accepted answer to this request's question, from transcripts touched since the
    request was filed (oldest first)."""
    since = clock.epoch(row["requested_at"], default=0.0)
    hits: list[Attested] = []
    for path in _files(cfg, since):
        hits += scan(path, row["text"], row["id"])
    return hits


def find(cfg: Config, row: dict[str, Any]) -> Attested | None:
    """The newest accepted answer to this request's question."""
    hits = find_all(cfg, row)
    return hits[-1] if hits else None


def verify(row: dict[str, Any], cfg: Config) -> bool:
    """Re-check a recorded harness answer from the transcript (the level verifier): the proof
    tool_use id must still be there, answered the way the row records. A later duplicate answer
    to the same question does not undo it."""
    yes, no = labels(row["id"])
    want = yes if row.get("outcome") == E.APPROVED else no
    return any(h.tool_use_id == row.get("proof") and h.answer == want for h in find_all(cfg, row))


def attest(log, cfg: Config, ask_id: str, *, now: float | None = None) -> tuple[bool, str]:
    """Record the harness-attested answer to ``ask_id``. (ok, message); the ask engine still
    enforces expiry, digest and single answer."""
    if not cfg.approvals.accept_harness:
        return False, "the harness channel is off ([approvals].accept_harness)"
    row = E.get(fold(log.read_all(), strict=False), ask_id)
    if row is None or not row["verb"]:
        return False, f"no approval request {ask_id}"
    if row["min_level"] > LEVEL:
        return False, f"'{row['verb']}' needs level L{row['min_level']}: never approved over chat"
    if E.status(row, now) != E.PENDING:
        return False, f"{ask_id} is {E.status(row, now)}"
    hit = find(cfg, row)
    if hit is None:
        return False, WAITING + (
            f"no answer to {ask_id} in the transcripts under {', '.join(cfg.approvals.transcript_dirs)}"
            f": put `ddflow approvals ask {ask_id}` to the person with AskUserQuestion first"
        )
    yes, no = labels(ask_id)
    outcome = {yes: E.APPROVED, no: E.DENIED}.get(hit.answer)
    if outcome is None:
        return False, f"the answer {hit.answer!r} is neither {yes!r} nor {no!r}"
    actor = resolve_actor(
        cfg,
        f"harness:{Path(hit.file).stem}",
        credential_role=cfg.agent.operator_role,
        authenticated=True,
    )
    try:
        E.answer(
            log, cfg, ask_id, outcome, actor=actor, digest=row["digest"],
            via=VIA, level=LEVEL, proof=hit.tool_use_id, now=now,
        )  # fmt: skip
    except E.AskRefused as exc:
        return False, str(exc)
    return True, f"{ask_id} {outcome} by the person who answered in {hit.file} ({hit.at})"


E.LEVEL_VERIFIERS[VIA] = verify
