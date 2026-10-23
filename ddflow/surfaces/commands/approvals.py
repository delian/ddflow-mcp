"""`ddflow approvals ...` -- the human surface for `api.approvals` (RA-cli)."""

from __future__ import annotations

import sys

from ...api import approvals as A
from ...api import approvals_answer as AA
from ..context import OK, Ctx
from .chore import _finish as finish_payload

PAYLOADS = {
    "list": ("approvals",),
    "show": ("approval",),
    "request": ("id", "new", "digest"),
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


def cmd_approvals_show(a, c: Ctx) -> int:
    return _finish(c, A.approvals_show(c.repo, a.id, agent=c.requested_agent), "show")


def cmd_approvals_request(a, c: Ctx) -> int:
    out = A.approvals_request(
        c.repo, a.verb, item=a.item or "", target=a.target or "", reason=a.reason or "",
        agent=c.requested_agent,
    )  # fmt: skip
    return _finish(c, out, "request")


def _digest_or_ask(a, c: Ctx) -> str:
    """The digest the person read: --digest, or typed at a terminal after the text is shown."""
    if a.digest or AA.is_an_agents_call(
        c.requested_agent
    ):  # an agent is refused by the API, unread
        return a.digest or ""
    if sys.stdin.isatty() and not c.json:
        shown = A.approvals_show(c.repo, a.id, agent=c.requested_agent)
        if shown.exit == OK:
            print(shown.data["text"])
            n = AA.digest_chars(c.repo, c.requested_agent)
            return input(f"type the first {n} characters of the digest above to answer: ").strip()
    return ""


def cmd_approvals_approve(a, c: Ctx) -> int:
    out = AA.approvals_approve(c.repo, a.id, digest=_digest_or_ask(a, c), agent=c.requested_agent)
    return _finish(c, out, "approve")


def cmd_approvals_deny(a, c: Ctx) -> int:
    out = AA.approvals_deny(c.repo, a.id, digest=_digest_or_ask(a, c), agent=c.requested_agent)
    return _finish(c, out, "deny")


def cmd_approvals_revoke(a, c: Ctx) -> int:
    return _finish(c, AA.approvals_revoke(c.repo, a.id, agent=c.requested_agent), "revoke")


def cmd_approvals_withdraw(a, c: Ctx) -> int:
    return _finish(c, A.approvals_withdraw(c.repo, a.id, agent=c.requested_agent), "withdraw")
