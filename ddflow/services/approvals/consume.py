"""Spend an approval at a gated command's refusal site (RA-consume, D-RA-1).

`operator_gate` is what a site asks once it knows the caller is an agent (the person-at-a-terminal
path is unchanged and never comes here). With an approval id it recomputes the canonical digest
from the state NOW and spends one use under the log lock; without one it files the request
(`[approvals].auto_request`) and returns the route. The effect the site then writes carries the
returned `stamp`. The agent identity cannot answer, widen or replay: every check below reads the
ask engine's record, and `engine.judge` re-verifies the answer's own proof.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...config import Config
from ...core import clock
from ...core.handlers.asks import ANSWERED, asks_of
from ...core.model import fold
from ..ask import engine as E
from . import harness as H  # registers the level verifier of the harness channel
from . import policy as PO
from . import request as R
from . import sites
from .canon import Canon


@dataclass(frozen=True)
class Decision:
    ok: bool
    message: str = ""
    #: What the effect event records: who approved, by which channel and level, the digest.
    stamp: dict[str, Any] = field(default_factory=dict)
    ask_id: str = ""


def route(ask_id: str, canon: Canon, *, new: bool) -> str:
    s = canon.site
    state = "requested" if new else "is still waiting"
    if s.tier == sites.T3:
        return (
            f"approval {ask_id} {state} for '{canon.verb}' (tier T3, level L{s.min_level}+): a "
            "signed approval is not available; if the operator opted in on the host "
            "([approvals].accept_chat_for_t3, rated L2-typed; a layer's forbid_chat_for_t3 overrides it) they type `approve "
            f"{ask_id} <first digest characters>` in their own chat and the agent runs `ddflow "
            f"approvals attest {ask_id}`, then re-runs this command with --approval {ask_id}; "
            "otherwise the operator does this themselves at their own terminal."
        )
    return (
        f"approval {ask_id} {state} for '{canon.verb}' ({s.tier}, level L{s.min_level}+): the operator "
        f"approves it with `ddflow approvals approve {ask_id}` from their own terminal (or answers "
        f"it over a channel they use); then re-run this command with --approval {ask_id}. It is "
        f"single-use, bound to this exact command and its current state, and expires."
    )


def _problem(
    row: dict[str, Any] | None, canon: Canon, now: float, level: int, cfg: Config | None = None
) -> str:
    if row is None or not row["verb"]:
        return "no such approval request"
    ask_id = row["id"]
    if row["verb"] != canon.verb:
        return f"{ask_id} is an approval for '{row['verb']}', not '{canon.verb}'"
    if row["requested_by"] != canon.agent:
        return f"{ask_id} was requested by {row['requested_by']}; only that agent may spend it"
    if row["state"] != ANSWERED:
        state = E.status(row, now)
        return f"{ask_id} is {state}" + (
            ": the operator has not answered it yet" if state == "pending" else ""
        )
    if row["outcome"] != E.APPROVED:
        return f"{ask_id} was denied by {row['answered_by']}"
    if row["digest"] != canon.digest():
        return (
            f"{ask_id} is of other state than this command acts on now (the command, its "
            "arguments, the gate's outcome or the findings changed): request a new approval"
        )
    verdict = E.judge(row, canon.digest())
    if not verdict.ok:
        return verdict.reason
    if level < row["min_level"] and not H.typed_t3(row, cfg or Config(), level):
        return (
            f"{ask_id} was answered at a level that holds up as L{level} but '{canon.verb}' "
            f"needs L{row['min_level']}"
            + (" (a signed approval; none is available)" if row["min_level"] >= sites.L4 else "")
        )
    answered = clock.epoch(row["answered_at"], default=0.0)
    if row["use_ttl_s"] > 0 and now > answered + row["use_ttl_s"]:
        return f"{ask_id} was answered more than {row['use_ttl_s']}s ago: request a new approval"
    if row["used"] >= row["uses"]:
        return f"{ask_id} is spent ({row['used']} of {row['uses']} use(s))"
    return ""


def spend(
    log, canon: Canon, ask_id: str, *, now: float | None = None, cfg: Config | None = None
) -> Decision:
    """Check ``ask_id`` against ``canon`` and, if it holds, record one use. All under the lock."""
    with log.transaction():
        # read the clock INSIDE the lock: waiting for it must not make an expiry look earlier
        now = clock.now_utc().timestamp() if now is None else now
        row = asks_of(fold(log.read_all(), strict=False)).get(ask_id)
        # once: the level of a harness answer depends on a file outside the lock
        level = E.verified_level(row, cfg) if row is not None else 0
        if why := _problem(row, canon, now, level, cfg):
            return Decision(False, f"refusing: {why}.", ask_id=ask_id)
        assert row is not None
        grade = H.GRADE_T3_CHAT if H.typed_t3(row, cfg or Config(), level) else ""
        log.append(
            "ask.consumed",
            ask_id,
            {
                "id": ask_id, "by": canon.agent, "verb": canon.verb, "digest": row["digest"],
                **({"grade": grade} if grade else {}),
            },
        )  # fmt: skip
    return Decision(
        True,
        stamp={
            "ask": ask_id,
            "approved_by": row["answered_by"],
            "via": row["via"],
            "level": level,
            "digest": row["digest"],
            "proxy": canon.agent,
            **({"grade": grade} if grade else {}),
        },
        ask_id=ask_id,
    )


#: `--approval request`: the agent asks for the approval now (sites that must not file one on
#: every refusal, because the refusal is routine: a premature `complete`).
REQUEST = "request"


def operator_gate(
    log,
    cfg: Config,
    canon: Canon,
    *,
    approval_id: str = "",
    reason: str = "",
    now: float | None = None,
    auto: bool = True,
) -> Decision:
    """The one question an agent-refusing site asks. Never answers for a person. ``auto`` False:
    file no request unless the agent says `--approval request`."""
    if approval_id and approval_id != REQUEST:
        return spend(log, canon, approval_id, now=now, cfg=cfg)
    state = fold(log.read_all(), strict=False)
    if stamp := PO.apply(log, cfg, state, canon, reason=reason):  # a standing rule (off by default)
        return Decision(True, stamp=stamp, ask_id=stamp["ask"])
    if not approval_id and not auto:
        return Decision(
            False,
            f"to ask the operator to approve '{canon.verb}' for {canon.item or 'this'}, "
            f"re-run with `--approval {REQUEST}`: a request id comes back and the operator "
            "answers it in chat.",
        )

    filed = R.file(log, cfg, state, canon, reason=reason, now=now, auto=approval_id != REQUEST)
    if filed is None:
        return Decision(
            False,
            f"refusing: '{canon.verb}' is the operator's act; file a request with "
            "`ddflow approvals request`, have them approve it, then re-run with --approval ID.",
        )
    return Decision(False, "refusing: " + route(filed.id, canon, new=filed.new), ask_id=filed.id)
