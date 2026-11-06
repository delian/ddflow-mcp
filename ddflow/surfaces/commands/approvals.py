"""`ddflow approvals ...` -- the human surface for `api.approvals` (RA-cli)."""

from __future__ import annotations

import sys

from ...api import approvals as A
from ...api import approvals_answer as AA
from ...api import approvals_harness as AH
from ...core import outcome as O
from ..context import OK, Ctx
from .chore import _finish as finish_payload

PAYLOADS = {
    "list": ("approvals",),
    "pending": ("approvals_pending",),
    "log": ("approvals", "warnings"),
    "show": ("approval",),
    "request": ("id", "new", "digest"),
    "ask": ("id", "input"),
    "attest": ("id",),
    "approve": ("id", "outcome"),
    "deny": ("id", "outcome"),
    "revoke": ("id",),
    "withdraw": ("id",),
}


def _finish(c: Ctx, out, which: str) -> int:
    return finish_payload(c, out, PAYLOADS[which])


def cmd_approvals_list(a, c: Ctx) -> int:
    return _finish(
        c, A.approvals_list(c.repo, all_rows=bool(a.all), agent=c.requested_agent), "list"
    )


def cmd_approvals_pending(a, c: Ctx) -> int:
    return _finish(c, A.approvals_pending(c.repo, agent=c.requested_agent), "pending")


def cmd_approvals_show(a, c: Ctx) -> int:
    return _finish(c, A.approvals_show(c.repo, a.id, agent=c.requested_agent), "show")


def cmd_approvals_request(a, c: Ctx) -> int:
    out = A.approvals_request(
        c.repo, a.verb, item=a.item or "", target=a.target or "", reason=a.reason or "",
        agent=c.requested_agent,
    )  # fmt: skip
    return _finish(c, out, "request")


def _digest_or_ask(c: Ctx, ask_id: str, digest: str) -> str:
    """The digest the person read: --digest, or typed at a terminal after the text is shown."""
    if digest or AA.is_an_agents_call(c.requested_agent):  # an agent is refused by the API, unread
        return digest or ""
    if sys.stdin.isatty() and not c.json:
        shown = A.approvals_show(c.repo, ask_id, agent=c.requested_agent)
        if shown.exit == OK:
            print(shown.data["text"])
            n = AA.digest_chars(c.repo, c.requested_agent)
            return input(f"type the first {n} characters of the digest above to answer: ").strip()
    return ""


def cmd_approvals_approve(a, c: Ctx) -> int:
    ids = [str(s) for s in (a.ids or [])]
    digests = [str(s) for s in (a.digest or [])]
    if a.all and (ids or digests):
        out = O.refused(
            "approvals.approve", "--all is every pending request: give it no ids and no --digest"
        )
    elif a.all:
        out = AA.approvals_approve_all(c.repo, agent=c.requested_agent)
    elif not ids:
        out = O.refused(
            "approvals.approve",
            "no request ids: give one or more ids, or --all for every pending one",
        )
    elif len(ids) == 1 and not digests and ":" not in ids[0]:
        # the one-request form keeps its strict read-receipt: the person types the digest
        out = AA.approvals_approve(
            c.repo, ids[0], digest=_digest_or_ask(c, ids[0], ""), agent=c.requested_agent
        )
    else:
        out = AA.approvals_approve_bulk(c.repo, ids, digests=digests, agent=c.requested_agent)
    return _finish(c, out, "approve")


def cmd_approvals_deny(a, c: Ctx) -> int:
    out = AA.approvals_deny(
        c.repo, a.id, digest=_digest_or_ask(c, a.id, a.digest or ""), agent=c.requested_agent
    )
    return _finish(c, out, "deny")


def cmd_approvals_revoke(a, c: Ctx) -> int:
    return _finish(c, AA.approvals_revoke(c.repo, a.id, agent=c.requested_agent), "revoke")


def cmd_approvals_withdraw(a, c: Ctx) -> int:
    return _finish(c, A.approvals_withdraw(c.repo, a.id, agent=c.requested_agent), "withdraw")


def cmd_approvals_ask(a, c: Ctx) -> int:
    return _finish(c, AH.approvals_ask(c.repo, a.id, agent=c.requested_agent), "ask")


def cmd_approvals_attest(a, c: Ctx) -> int:
    if a.all and a.id:
        out = O.refused(
            "approvals.attest",
            "--all is every pending request with an answer: give it no id",
        )
        return _finish(c, out, "attest")
    if a.all:
        return _finish(c, AH.approvals_attest_all(c.repo, agent=c.requested_agent), "attest")
    if not a.id:
        out = O.refused(
            "approvals.attest",
            "no request id: give one to attest it, or --all for every pending one with an "
            "answer (a chat answer alone approves each, no terminal)",
        )
        return _finish(c, out, "attest")
    return _finish(c, AH.approvals_attest(c.repo, a.id, agent=c.requested_agent), "attest")


def cmd_approvals_log(a, c: Ctx) -> int:
    out = A.approvals_log(
        c.repo, verb=a.verb or "", agent_filter=a.agent_filter or "", since=a.since or "",
        agent=c.requested_agent,
    )  # fmt: skip
    return _finish(c, out, "log")
