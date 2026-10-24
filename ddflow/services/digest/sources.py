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

from ddflow.services.approvals import sites

__all__ = ("EXEMPT_SITES", "SOURCES", "by_kind", "covered_sites", "unregistered_sites")

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
    #: "ddflow.services.digest.queue:collect_<kind>", the name OD-queue must provide (its test makes resolution strict).
    collector: str
    #: How it is answered, in words (may be an act outside ddflow, e.g. raising a limit).
    answer: str
    #: The runnable `ddflow ...` command that answers or inspects it, as a template: each <placeholder> is filled from the entry.
    command: str
    #: How the lead puts it to the operator in chat.
    chat: str
    #: Who is blocked and how the impact count is taken.
    blocks: str
    #: Operator-only verbs (`approvals/sites.py` `Site.verb`) this kind answers.
    sites: tuple[str, ...] = ()


def _s(kind, what, owner, severity, detected, answer, command, chat, blocks, sites=()):
    return Source(
        kind,
        what,
        owner,
        severity,
        detected,
        f"ddflow.services.digest.queue:collect_{kind}",
        answer,
        command,
        chat,
        blocks,
        tuple(sites),
    )


SOURCES: tuple[Source, ...] = (
    _s(
        "approval",
        "an approval request is pending",
        OPERATOR,
        "nudge",
        "approval ask records without an answer",
        "approve or deny the request",
        "ddflow approvals approve <id>",
        APPROVAL_ASK,
        "the requesting agent and its item; dependants via needs",
    ),
    _s(
        "decide",
        "a DECIDE task awaits the operator's answers",
        OPERATOR,
        "nudge",
        "kind doc, title starts DECIDE, open, needs done",
        "answer the task's questions; the lead records each as a decision",
        "ddflow decision add --id D-<task>-Q<n> --title <title> --decision <text>",
        ASK,
        "every item whose needs reach the task",
    ),
    _s(
        "gate_skip",
        "a gate needs a skip or an extra review round",
        OPERATOR,
        "nudge",
        "gate at its round cap, or unavailable with no alternative",
        "approve the skip, then re-run it with the approval id",
        "ddflow gate skip <id> <gate> --reason <reason>",
        APPROVAL_ASK,
        "the item and its dependants",
        ("gate.skip.failed_review",),
    ),
    _s(
        "gate_partial",
        "a partial gate outcome awaits acceptance",
        OPERATOR,
        "nudge",
        "gate recorded partial, not accepted",
        "accept the partial outcome",
        "ddflow approve <id> <gate>",
        APPROVAL_ASK,
        "the item",
        ("approve.partial",),
    ),
    _s(
        "force_completion",
        "an item needs --force completion",
        OPERATOR,
        "nudge",
        "pipeline complete except a refused gate; the hook refuses an agent's --force",
        "approve the forced completion",
        "ddflow complete <id> --approval <ask>",
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
        "approve the chore policy",
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
        "approve the reviewer",
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
        "acknowledge the selection",
        "ddflow export ack <document>",
        APPROVAL_ASK,
        "none",
        ("export.ack",),
    ),
    _s(
        "plan",
        "a proposed plan awaits confirmation",
        OPERATOR,
        "nudge",
        "plan proposed, not confirmed",
        "confirm or reject the plan",
        "ddflow plan confirm --yes --reason <reason>",
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
        "read it, then accept or supersede it",
        "ddflow decision show <id>",
        ASK,
        "items under its globs",
    ),
    _s(
        "gate_spotcheck",
        "forced, partial or refuted gate outcomes await a spot-check",
        OPERATOR,
        "nudge",
        "gate list --refuted / --overrides",
        "inspect each, then accept or reopen",
        "ddflow gate list --refuted",
        ASK,
        "trust, not flow",
    ),
    _s(
        "red_main",
        "main is red",
        OPERATOR,
        "needs",
        "latest CI result of the merge or schedule stage failed (urgent after [digest].escalate_after_red_main)",
        "get main green (find or file the fix task); nothing should merge meanwhile",
        "ddflow ci status",
        ASK,
        "every merge, which is not itemised here (impact is shown as 0)",
    ),
    _s(
        "lease",
        "a lease is stalled, idle or dead",
        LEAD,
        "nudge",
        "health finding older than the cap, or lease expired without a heartbeat",
        "run the correcting command health prints",
        "ddflow health",
        LEAD_ONLY,
        "the waiters on its files",
    ),
    _s(
        "agent_failed",
        "an agent's run failed (rate or spend limit, crash)",
        OPERATOR,
        "nudge",
        "run record failure text (rate or spend limit, crash); an expired lease is the `lease` source",
        "restart the agent; raise the limit if that was the cause",
        "ddflow show <item>",
        ASK,
        "its item",
    ),
    _s(
        "credential",
        "a reviewer is unavailable for a missing credential",
        OPERATOR,
        "nudge",
        "unavailable reviewer whose cause is a missing key",
        "put the key where the reviewer's doc says (never in chat)",
        "ddflow reviewers list",
        ASK,
        "the gate",
    ),
    _s(
        "question",
        "an agent's question has no answer",
        OPERATOR,
        "nudge",
        "session note or ask of kind question without an answer",
        "answer the question in chat; the lead records the answer",
        "ddflow approvals show <id>",
        ASK,
        "the asker",
    ),
    _s(
        "phase_close",
        "a finished phase awaits closing",
        LEAD,
        "info",
        "every task done, phase open",
        "complete the phase",
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
    "export.lock": "an immediate act by a person; nothing pends in State",
    "export.unlock": "an immediate act by a person; nothing pends in State",
    "claim.force": "an immediate act by a person; nothing pends in State",
}


def by_kind() -> dict[str, Source]:
    return {s.kind: s for s in SOURCES}


def covered_sites() -> set[str]:
    return {v for s in SOURCES for v in s.sites} | set(EXEMPT_SITES)


def unregistered_sites() -> list[str]:
    """Operator-only verbs in `approvals/sites.py` that no source covers and no exemption names."""
    have = covered_sites()
    return sorted(s.verb for s in sites.SITES if s.verb not in have)
