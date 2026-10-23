"""What can wait on the operator, as data (OD-inventory, D-OD-Q2/Q8).

One row per KIND of operator-queue entry: who owns it, how severe it starts, what detects it
(`collector`, built by OD-queue as a pure function of State, Config and now), the exact command
that answers it and the chat route that puts it to a person. `tests/test_digest_sources.py`
fails when an operator-only verb in `approvals/sites.py` is neither covered by a row here nor
named in `EXEMPT_SITES` with a reason, so a new refusal site cannot appear without deciding
whether it can wait on a person (r-reuse-core: `sites.py` stays the one list of such verbs).

No thresholds live here: ages, escalation and caps are `[digest]` knobs (OD-queue,
D-everything-configurable). Answer text never contains a secret.
"""

from __future__ import annotations

from dataclasses import dataclass

OPERATOR, LEAD = "operator", "lead"
OWNERS = (OPERATOR, LEAD)
#: Rising with age (D-OD-Q3): nudge -> needs -> urgent.
SEVERITIES = ("info", "nudge", "needs", "urgent")

#: The chat route for an entry a person answers in the agent's chat: print the exact payload.
ASK = "ddflow digest --ask {id}  (the exact AskUserQuestion payload; show it unaltered)"
APPROVAL_ASK = "ddflow approvals ask {id}  (the exact AskUserQuestion payload; show it unaltered)"
LEAD_ONLY = "the lead acts; nothing to ask the operator"


@dataclass(frozen=True)
class Source:
    kind: str
    #: One line naming what waits; the entry text is rendered from a template, never agent prose.
    what: str
    owner: str
    severity: str
    #: The fact in State that makes it true ("module:function" collectors are named by OD-queue).
    detected_from: str
    #: "ddflow.services.digest.queue:collect_<kind>"; OD-queue's test resolves every one.
    collector: str
    #: The exact command that answers it.
    answer: str
    #: How the lead puts it to the operator in chat.
    chat: str
    #: Who is blocked and how the impact count is taken.
    blocks: str
    #: Operator-only verbs (`approvals/sites.py` `Site.verb`) this kind answers.
    sites: tuple[str, ...] = ()


def _s(kind, what, owner, severity, detected, answer, chat, blocks, sites=()):
    return Source(
        kind,
        what,
        owner,
        severity,
        detected,
        f"ddflow.services.digest.queue:collect_{kind}",
        answer,
        chat,
        blocks,
        tuple(sites),
    )


SOURCES: tuple[Source, ...] = (
    _s(
        "approval",
        "an approval request is pending",
        OPERATOR,
        "needs",
        "approval ask records without an answer",
        "ddflow approvals approve <id> | deny <id>",
        APPROVAL_ASK,
        "the requesting agent and its item; dependants via needs",
    ),
    _s(
        "decide",
        "a DECIDE task awaits the operator's answers",
        OPERATOR,
        "needs",
        "kind doc, title starts DECIDE, open, needs done",
        "answer the task's questions, then decision add D-<id>-Qn",
        ASK,
        "every item whose needs reach the task",
    ),
    _s(
        "gate_skip",
        "a gate needs a skip or an extra review round",
        OPERATOR,
        "needs",
        "gate at its round cap, or unavailable with no alternative",
        "ddflow gate skip <id> <gate> --reason ... --approval <id>",
        APPROVAL_ASK,
        "the item and its dependants",
        ("gate.skip.failed_review",),
    ),
    _s(
        "gate_partial",
        "a partial gate outcome awaits acceptance",
        OPERATOR,
        "needs",
        "gate recorded partial, not accepted",
        "ddflow approve <id> <gate>",
        APPROVAL_ASK,
        "the item",
        ("approve.partial",),
    ),
    _s(
        "force_completion",
        "an item needs --force completion",
        OPERATOR,
        "needs",
        "pipeline complete except a refused gate; the hook refuses an agent's --force",
        "ddflow complete <id> --force ... --approval <id>",
        APPROVAL_ASK,
        "the item and dependants",
        ("complete.force",),
    ),
    _s(
        "chore",
        "a chore proposal awaits approval",
        OPERATOR,
        "nudge",
        "pending [chore] globs or skip_gates change",
        "ddflow chore approve",
        APPROVAL_ASK,
        "none usually",
        ("chore.approve",),
    ),
    _s(
        "reviewer",
        "a reviewer entry awaits approval",
        OPERATOR,
        "nudge",
        "pending [[reviewer]] entry",
        "ddflow reviewers approve <name>",
        APPROVAL_ASK,
        "the gate that wanted it",
        ("reviewers.approve",),
    ),
    _s(
        "export_ack",
        "an export selection awaits acknowledgement",
        OPERATOR,
        "nudge",
        "export document selection changed, not acknowledged",
        "ddflow export ack ...",
        APPROVAL_ASK,
        "none",
        ("export.ack",),
    ),
    _s(
        "plan",
        "a proposed plan awaits confirmation",
        OPERATOR,
        "needs",
        "plan proposed, not confirmed",
        "ddflow plan confirm --yes --reason ...",
        ASK,
        "the work the plan creates",
        ("plan.confirm",),
    ),
    _s(
        "decision",
        "a decision is proposed or unconfirmed",
        OPERATOR,
        "nudge",
        "decision in status proposed, or accepted by default and unconfirmed",
        "ddflow decision show <id>, then accept or supersede",
        ASK,
        "items under its globs",
    ),
    _s(
        "gate_spotcheck",
        "forced, partial or refuted gate outcomes await a spot-check",
        OPERATOR,
        "nudge",
        "gate list --refuted / --overrides",
        "ddflow gate list --refuted, then accept or reopen",
        ASK,
        "trust, not flow",
    ),
    _s(
        "red_main",
        "main is red",
        OPERATOR,
        "urgent",
        "latest ci.recorded on main failed, or the red-main bug is open",
        "ddflow show <fix task>",
        ASK,
        "every merge; impact is the ready queue",
    ),
    _s(
        "lease",
        "a lease is stalled, idle or dead",
        LEAD,
        "needs",
        "health finding older than the cap, or lease expired without a heartbeat",
        "ddflow health (its correcting command)",
        LEAD_ONLY,
        "the waiters on its files",
    ),
    _s(
        "agent_failed",
        "an agent's run failed (rate or spend limit, crash)",
        OPERATOR,
        "needs",
        "run record failure text, or lease expired with no heartbeat",
        "restart the agent; raise the limit",
        ASK,
        "its item",
    ),
    _s(
        "credential",
        "a reviewer is unavailable for a missing credential",
        OPERATOR,
        "needs",
        "unavailable reviewer whose cause is a missing key",
        "put the key where the reviewer's doc says (never in chat)",
        ASK,
        "the gate",
    ),
    _s(
        "question",
        "an agent's question has no answer",
        OPERATOR,
        "needs",
        "session note or ask of kind question without an answer",
        "answer in chat; the lead records it",
        ASK,
        "the asker",
    ),
    _s(
        "phase_close",
        "a finished phase awaits closing",
        LEAD,
        "info",
        "every task done, phase open",
        "ddflow complete <phase>",
        LEAD_ONLY,
        "none",
    ),
)

#: Operator-only verbs that never WAIT: the act happens at the call and leaves nothing pending.
EXEMPT_SITES: dict[str, str] = {
    "grant.add": "an immediate act by a person; any request for it is an 'approval'",
    "config.reviewer_command": "T3 signed act; nothing pends in State",
    "upgrade.self": "an immediate act by a person; nothing pends in State",
    "skew.override": "an immediate flag on one command; nothing pends in State",
    "claim.force": "an immediate act by a person; nothing pends in State",
}


def by_kind() -> dict[str, Source]:
    return {s.kind: s for s in SOURCES}


def covered_sites() -> set[str]:
    return {v for s in SOURCES for v in s.sites} | set(EXEMPT_SITES)


def unregistered_sites() -> list[str]:
    """Operator-only verbs in `approvals/sites.py` that no source covers and no exemption names."""
    from ddflow.services.approvals import sites

    have = covered_sites()
    return sorted(s.verb for s in sites.SITES if s.verb not in have)
