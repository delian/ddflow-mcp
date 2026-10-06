"""One approve-by-digest primitive (B-uni-approval).

Several flows need a PERSON to approve something before ddflow acts on it: a reviewer
entry a tool wrote (D-reviewer-trust, the first one moved here), and, planned, a skill
install, an upstream bug report, a draft, a schedule's escalation and an upgrade. Each
was about to grow its own event, its own "is it approved" check and its own idea of
what was approved. This is the one of each:

* `Approval(subject, digest, actor, token)`: ``subject`` names the thing
  (``reviewer:critic``, ``skill:review``, ``upstream:B123``), ``digest`` is what it was
  when the person looked, ``actor`` who they were, and ``token`` -- for a single-use
  approval -- the secret an agent presents to spend it (D-upstream-reporting: a one-time
  token the operator mints at a terminal and hands over);
* `grant` records it (`approval.granted`), and refuses under any agent identity;
* `check` is the ONE test: the digest of what is about to run must equal a digest a
  person approved -- a later edit is not approved, whoever made it;
* `use` spends a single-use approval (`approval.used`): a token works once.

Like every human checkpoint here, this makes a forged approval VISIBLE (the OS user,
the host, ``human``), not impossible: an agent with a shell can run the command.
"""

from __future__ import annotations

import getpass
import os
import secrets
import socket
from dataclasses import dataclass
from typing import Any

from ..core.digest import content_digest
from ..core.model import fold

#: Environment variables an agent harness sets in the shells it runs, so a command it
#: runs is known not to come from a person at their own terminal. Only the ones known
#: for certain: Claude Code exports CLAUDECODE=1. Another harness is recognised by
#: `--agent` or `DDFLOW_AGENT`, which ddflow's own setup has it pass.
HARNESS_MARKERS = ("CLAUDECODE",)


class ApprovalRefused(ValueError):
    """An approval asked for under an agent identity, or with nothing to approve."""


def agent_marker(requested_agent: str = "") -> str:
    """Why this invocation is an agent's, or ``""`` when nothing says it is.

    An explicit `--agent`, `DDFLOW_AGENT`, or a harness's own marker. A person at their
    own terminal sets none of them; an agent that unsets all three is the shell edit the
    decision accepts it cannot stop.
    """
    if requested_agent:
        return f"--agent {requested_agent}"
    if os.environ.get("DDFLOW_AGENT"):
        return f"DDFLOW_AGENT={os.environ['DDFLOW_AGENT']}"
    for var in HARNESS_MARKERS:
        if os.environ.get(var):
            return f"{var} is set (an agent harness's shell)"
    return ""


def refusal(why: str) -> str:
    """The one refusal of an approval asked for under an agent identity."""
    return (
        f"refusing: approving is a person's act, and this command runs under an agent "
        f"identity ({why}). Run it from your own terminal."
    )


def os_user() -> str:
    """The OS user, or "unknown-user": an approval whose approver is unknown is still
    real, and saying so is honest where inventing a name would not be."""
    try:
        return getpass.getuser()
    except Exception:
        return "unknown-user"


@dataclass(frozen=True)
class Actor:
    user: str
    host: str
    human: bool = True


@dataclass(frozen=True)
class Approval:
    subject: str
    digest: str
    actor: Actor
    #: The single-use secret, as minted: shown to the person once and never stored (the
    #: log keeps its hash). "" for a standing approval.
    token: str = ""
    note: str = ""


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str
    #: The approval that answered, as `State.approvals` keeps it; None when none did.
    row: dict[str, Any] | None = None


def token_hash(token: str) -> str:
    return content_digest(token, "sha256")


def grant(
    log,
    subject: str,
    digest: str,
    *,
    note: str = "",
    requested_agent: str = "",
    single_use: bool = False,
    extra: dict[str, Any] | None = None,
) -> Approval:
    """A PERSON approves ``subject`` as it is at ``digest`` (`approval.granted`).

    ``single_use`` mints a token, returned in the `Approval` and recorded only as its
    hash: whoever presents it may act once (`use`). ``extra`` is carried in the event
    for the flow's own record (a reviewer's identity fields, say), never interpreted.
    Raises `ApprovalRefused` under an agent identity or without a subject and digest.
    """
    if why := agent_marker(requested_agent):
        raise ApprovalRefused(refusal(why))
    if not subject or not digest:
        raise ApprovalRefused("an approval needs a subject and the digest of what it approves")
    actor = Actor(user=os_user(), host=socket.gethostname().split(".")[0])
    token = secrets.token_urlsafe(18) if single_use else ""
    data: dict[str, Any] = {
        **(extra or {}),
        "subject": subject,
        "digest": digest,
        "user": actor.user,
        "host": actor.host,
        "human": actor.human,
        "note": note,
    }
    if token:
        data["token_hash"] = token_hash(token)
    log.append("approval.granted", subject, data)
    return Approval(subject, digest, actor, token, note)


def check(state, subject: str, digest: str, *, token: str = "") -> Verdict:
    """Is ``subject`` approved as it is NOW (``digest``)? The one test every flow uses.

    Approved when a person approved exactly this digest: a standing approval, or a
    single-use one whose ``token`` is presented and not yet spent. An approval of an
    earlier digest does not carry over to a later edit.
    """
    # A grant that says a person made it (`human`; `agent_marker` refused the rest at
    # grant time). An event written by hand without it approves nothing.
    rows = [r for r in state.approvals.get(subject, []) if r.get("human")]
    if not rows:
        return Verdict(False, f"{subject} has not been approved")
    same = [r for r in rows if r["digest"] == digest]
    if not same:
        return Verdict(
            False,
            f"{subject} changed since it was approved (approved {rows[-1]['digest']}, now "
            f"{digest}); it needs approving again",
        )
    standing = [r for r in same if not r["token_hash"]]
    if standing:
        return Verdict(True, f"{subject} approved by {standing[-1]['user']}", standing[-1])
    if not token:
        return Verdict(False, f"{subject} has a single-use approval: present its token")
    wanted = token_hash(token)
    for r in same:
        if r["token_hash"] == wanted:
            if r["used_at"]:
                return Verdict(False, f"that token for {subject} was spent at {r['used_at']}")
            return Verdict(True, f"{subject} approved by {r['user']} (single use)", r)
    return Verdict(False, f"that token does not approve {subject} at {digest}")


def use(log, state, subject: str, digest: str, token: str) -> Verdict:
    """Spend a single-use approval: `check` it, then record it spent (`approval.used`).
    A standing approval is not spent. Returns the verdict; nothing is written on a no."""
    verdict = check(state, subject, digest, token=token)
    if not (verdict.ok and verdict.row is not None and verdict.row["token_hash"]):
        return verdict
    with log.transaction():
        # Checked again under the log's lock: two agents presenting one token at once
        # must not both spend it.
        verdict = check(fold(log.read_all(), strict=False), subject, digest, token=token)
        if verdict.ok and verdict.row is not None and verdict.row["token_hash"]:
            log.append(
                "approval.used",
                subject,
                {"subject": subject, "digest": digest, "token_hash": verdict.row["token_hash"]},
            )
    return verdict
