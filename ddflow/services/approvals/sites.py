"""The gated-command table: every operator-only site, as data (RA-inventory, D-RA-Q1/Q7).

One row per VERB an agent may be refused. The row says what the verb is (`tier`), how strong
an answer it needs (`min_level`), which state its approval digests (`digest`), and which
functions enforce the person-only rule (`functions`: those that call `agent_marker` or
`is_agent`). `tests/test_approvals_sites.py` walks `ddflow/` and fails for a function that makes
such a call and is named by no row (or an alias import that would hide one), so a new refusal site cannot appear without a row here
This is a guard against ACCIDENTAL drift by an honest author -- it reads calls by their name in the
syntax tree and does not stop an author who hides a call (`exec`, `getattr`, a rebinding); review
and the verb's own tests do that (r-reuse-core: this is the one list; the consume check, `doctor` and the docs read it).

Tiers (research 7): T1 standing policy (opt-in, reported), T2 a person approves each instance,
T3 signed only. Levels (research 5): L1 terminal, L2 harness-attested, L3 secret or
identity-attested (one-time code, chat/PR), L4 signed. D-RA-Q6: T2 accepts any person
channel (L1/L2/L3), T3 only L4 -- on a single-uid host rated no stronger than L1.

Families (research 2): A identity-refused (an agent is refused outright), B relayed consent
(`--yes --reason`, trusted free text; kept one release, D-RA-Q2), C hidden or hook-blocked.
"""

from __future__ import annotations

from dataclasses import dataclass

T1, T2, T3 = "T1", "T2", "T3"
TIERS = (T1, T2, T3)
L1, L2, L3, L4 = 1, 2, 3, 4
FAMILIES = ("A", "B", "C")

#: Modules that implement the rule itself or only map an identity to a role; they are not
#: sites. Their marker calls are pinned too ("module:function" -> count), so a new refusal
#: cannot hide in them either.
INFRASTRUCTURE: dict[str, int] = {
    "ddflow/services/identity.py:is_agent": 1,  # defines is_agent over agent_marker
    "ddflow/services/actors.py:_role": 1,  # resolves an identity to a role for the ask engine
    "ddflow/api/surf_setup.py:reviewer_agent_marker": 1,
    "ddflow/services/approvals/answer.py:why_not_a_person": 1,  # the answer verbs' early refusal  # a shim over reviewer_trust
}

#: Marker calls per enclosing function ("module:function" -> number of AST `Call` nodes naming
#: `agent_marker`, `is_agent` or the `_is_agent`/`reviewer_agent_marker` wrappers), for every
#: module that is not infrastructure. A new call, or one moved to another function, changes
#: this map, so the test fails until the author decides which verb it gates and amends the row
#: that names the function in `Site.functions`.
MARKER_CALLS: dict[str, int] = {
    "ddflow/api/gates/_vet.py:_agent_skip_refusal": 1,
    "ddflow/api/gates/_run.py:record": 1,
    "ddflow/services/grants.py:_agent_approval": 1,
    "ddflow/api/chore.py:chore_approve": 1,
    "ddflow/api/lifecycle/claim.py:_claim_by_approval": 1,
    "ddflow/api/lifecycle/claim.py:_agent_marker_of": 1,
    "ddflow/api/lifecycle/heartbeat.py:release": 1,
    "ddflow/api/lifecycle/complete/approval.py:_approved_force": 1,
    "ddflow/api/lifecycle/complete/approval.py:_person_force": 1,
    "ddflow/api/lifecycle/complete/approval.py:_ask_hint": 1,
    "ddflow/services/merge_force.py:authorise": 1,
    "ddflow/services/phase_complete.py:complete_phase": 1,
    "ddflow/services/gate_accept.py:approve": 1,
    "ddflow/services/approval.py:grant": 1,  # approve.partial and chore.approve share it
    "ddflow/services/reviewer_trust.py:approve": 1,
    "ddflow/services/auth.py:_person": 1,
    "ddflow/services/export/select.py:_is_agent": 1,  # the wrapper
    "ddflow/services/export/select.py:enable": 1,
    "ddflow/services/export/select.py:disable": 1,
    "ddflow/services/export/select.py:acknowledge": 1,
    "ddflow/services/export/select.py:gate_force": 1,
    "ddflow/services/approvals/relayed.py:gate": 1,  # upgrade.self and plan.confirm
    "ddflow/surfaces/commands/review.py:_reviewers_add": 1,
    "ddflow/api/setup/config.py:configure": 1,
    "ddflow/api/pin_files.py:_regenerate_approval": 1,
    "ddflow/services/repairs/accepted.py:gate": 1,
}

MARKER_NAMES = frozenset({"agent_marker", "is_agent", "_is_agent", "reviewer_agent_marker"})


@dataclass(frozen=True)
class Site:
    verb: str
    family: str
    tier: str
    min_level: int
    #: What the approval's digest binds, in words (the canonical record is built in RA-request).
    digest: str
    #: Functions ("module:function") that call agent_marker/is_agent for this verb. Empty for a site with no marker call
    #: (a relayed-consent flag or a hook guard).
    functions: tuple[str, ...] = ()
    #: The CLI form of the verb; an agent re-runs it with `--approval ID` appended.
    command: str = ""
    #: For a site with no marker call (relayed consent, a hook guard): (file, text) that must
    #: still be in that file, so a rename or removal of the guard makes the row stale.
    anchor: tuple[str, str] = ("", "")


SITES: tuple[Site, ...] = (
    Site(
        "gate.skip.failed_review",
        "A",
        T2,
        L1,
        "the failed review outcome and its unresolved findings",
        ("ddflow/api/gates/_vet.py:_agent_skip_refusal", "ddflow/api/gates/_run.py:record"),
        "ddflow gate skip <id> <gate> --reason ...",
    ),
    Site(
        "grant.add",
        "A",
        T2,
        L1,
        "the item, the grant kind and count, and the grants already recorded",
        ("ddflow/services/grants.py:_agent_approval",),
        "ddflow grant add ...",
    ),
    # Relay the channel-consent grant (channels.grant_consent) so `approvals ask`/`attest` can offer it.
    Site(
        "channel.consent",
        "A",
        T2,
        L1,
        "the channel kind and its consent digest (once per kind, D7e0d117ba5)",
        (),  # no marker call here: the refusal lives in approval.grant, which this relays to
        "ddflow approvals consent <kind> --reason ...",
        anchor=(
            "ddflow/services/ask/channels.py",
            "A person consents to one kind of network channel",
        ),
    ),
    Site(
        "approve.partial",
        "A",
        T2,
        L1,
        "the recorded partial outcome (gate_accept digest)",
        ("ddflow/services/approval.py:grant", "ddflow/services/gate_accept.py:approve"),
        "ddflow approve <id> <gate> --approval ID",
    ),
    Site(
        "chore.approve",
        "A",
        T2,
        L1,
        "the current [chore].globs and skip_gates",
        ("ddflow/services/approval.py:grant", "ddflow/api/chore.py:chore_approve"),
        "ddflow chore approve",
    ),
    Site(
        "reviewers.approve",
        "A",
        T2,
        L1,
        "the pending reviewer entry (endpoint and model)",
        ("ddflow/services/reviewer_trust.py:approve",),
        "ddflow reviewers approve <name>",
    ),
    Site(
        "reviewers.approve_command",
        "A",
        T3,
        L4,
        "the pending command reviewer entry (the program it runs)",
        ("ddflow/services/reviewer_trust.py:approve",),
        "ddflow reviewers approve <name>",
    ),
    Site(
        "token.manage",
        "A",
        T3,
        L4,
        "the client name and role of the credential minted, rotated or revoked",
        ("ddflow/services/auth.py:_person",),
        "ddflow pair <name> --role <role>",
    ),
    Site(
        "export.ack",
        "A",
        T2,
        L1,
        "the agent-enabled documents still unacknowledged",
        (
            "ddflow/services/export/select.py:_is_agent",
            "ddflow/services/export/select.py:acknowledge",
        ),
        "ddflow export ack --approval ID",
    ),
    Site(
        "export.unlock",
        "A",
        T2,
        L1,
        "the operator's lock (or another agent's) on the document an agent enables again",
        ("ddflow/services/export/select.py:enable",),
        "ddflow export enable <doc> --approval ID",
    ),
    Site(
        "export.force",
        "A",
        T2,
        L1,
        "the document and the target file's bytes as they are now (the hand edit it would replace)",
        ("ddflow/services/export/select.py:gate_force",),
        "ddflow export <doc> --update|--out PATH --force --approval ID",
    ),
    Site(
        "export.machine",
        "A",
        T2,
        L1,
        "the document and whether this is an enable or a disable (a per-machine selection)",
        ("ddflow/services/export/select.py:enable", "ddflow/services/export/select.py:disable"),
        "ddflow export enable|disable <doc> --local --approval ID",
    ),
    Site(
        "config.reviewer_command",
        "A",
        T3,
        L4,
        "the [[reviewer]] command line (it executes code)",
        ("ddflow/surfaces/commands/review.py:_reviewers_add",),
        "ddflow reviewers add --preset ...",
    ),
    Site(
        "history.accept",
        "A",
        T2,
        L1,
        "the findings accepted (a digest of their keys: an event's id and content, or a "
        "clock regression's shard, position and values) and the ids named",
        ("ddflow/services/repairs/accepted.py:gate",),
        "ddflow doctor --accept-history --history-reason WHY --approval request|ID",
    ),
    Site(
        "config.set",
        "C",
        T2,
        L1,
        "the key, the value and the layer this edit writes",
        ("ddflow/api/setup/config.py:configure",),
        "ddflow config <key> <value> [--local] --approval request|ID",
    ),
    Site(
        "pins.regenerate",
        "C",
        T2,
        L1,
        "the pin files as recorded before the re-record",
        ("ddflow/api/pin_files.py:_regenerate_approval",),
        "ddflow pins regenerate [--only NAME] --approval request|ID",
    ),
    Site(
        "upgrade.self",
        "B",
        T2,
        L1,
        "the running and the target version and the exact install command (relayed `--yes --reason` still works, warned, until [approvals].require_approval lists the verb)",
        ("ddflow/services/approvals/relayed.py:gate",),
        "ddflow upgrade --self --approval ID",
    ),
    Site(
        "plan.confirm",
        "B",
        T2,
        L1,
        "the plan and the tasks it still holds back (relayed `--yes --reason` still works, warned, until [approvals].require_approval lists the verb)",
        ("ddflow/services/approvals/relayed.py:gate",),
        "ddflow plan confirm <plan> --approval ID",
    ),
    Site(
        "skew.override",
        "B",
        T2,
        L1,
        "none: the version guard refuses the approval engine's own writes, so this keeps relayed consent, warned per use (D-RA-Q2)",
        (),
        "ddflow <write command> --allow-older-version --reason ...",
        anchor=("ddflow/surfaces/history_verbs.py", "skew.overridden"),
    ),
    Site(
        "claim.force",
        "C",
        T2,
        L1,
        "the item and who holds its lease (or that it is in review)",
        (
            "ddflow/api/lifecycle/claim.py:_claim_by_approval",
            "ddflow/api/lifecycle/claim.py:_agent_marker_of",
        ),
        "ddflow claim <id> --approval request|ID",
    ),
    Site(
        "complete.force",
        "C",
        T2,
        L1,
        "the item and its unmet completion conditions (never a failed review gate)",
        (
            "ddflow/api/lifecycle/complete/approval.py:_approved_force",
            "ddflow/api/lifecycle/complete/approval.py:_person_force",
            "ddflow/api/lifecycle/complete/approval.py:_ask_hint",
        ),
        "ddflow complete <id> --approval request|ID",
    ),
    Site(
        "merge.force",
        "C",
        T2,
        L1,
        "the item and the exact required gates left unsettled (never a failed review or ci gate)",
        ("ddflow/services/merge_force.py:authorise",),
        "ddflow merge <id> --force --approval request|ID",
    ),
    Site(
        "release.force",
        "C",
        T2,
        L1,
        "the item and who holds its lease",
        ("ddflow/api/lifecycle/heartbeat.py:release",),
        "ddflow release <id> --force --reason ...",
    ),
    Site(
        "phase.complete",
        "C",
        T2,
        L1,
        "the phase and the settled state of every task under it",
        ("ddflow/services/phase_complete.py:complete_phase",),
        "ddflow phase complete <id> --approval request|ID",
    ),
)

#: Refusals that name only a terminal because the act is T3 (D-RA-4, D-RA-Q6): no chat route
#: exists, by decision, and the reason says why. `tests/test_approvals_conformance.py` fails for a
#: refusal that names a terminal, offers no approval route and is not listed here.
#: Also T3 and not listed as a refusal text: the approval system's own configuration
#: (`[approvals]`, `[agent].roles/actors`), signer pins and key rotation (no signer exists yet),
#: `[hooks].forbidden_commands`, disabling hooks, company-layer floors.
TERMINAL_ONLY: dict[str, str] = {
    "ddflow/services/reviewer_trust.py:write": (
        "T3: a command reviewer runs any program and can print any verdict, so only a person "
        "adds one; over chat it would let whoever types into the session run code"
    ),
    "ddflow/services/auth.py:_person": (
        "T3: minting or ending a credential prints or relies on a secret, and a token that "
        "passed through chat sits in an agent's context and transcript (P-remote.auth)"
    ),
}

#: Refusals NOT yet routed, honestly listed with the follow-up that routes them (not T3: the
#: conformance test requires the reason to name its task, so this table only shrinks). Empty since
#: RA-consume-sites-4 routed the last ones (`export --force`/`--local`: `export.force`,
#: `export.machine`; `upgrade --self`, `plan confirm`: relayed consent beside `--approval`).
NOT_YET_ROUTED: dict[str, str] = {}

#: Strings that mention a terminal without refusing anyone (input prompts, docs).
NOT_A_REFUSAL: dict[str, str] = {
    "ddflow/services/export/select.py:brief_line": "an operator-facing brief line: the operator is its reader",
    "ddflow/surfaces/commands/knowledge.py:_session_text": "refuses to read prose from a tty, not an act",
    "ddflow/services/help.py:<module>": "help text about the CLI as a surface",
}

BY_VERB = {s.verb: s for s in SITES}


def site(verb: str) -> Site:
    """The row for ``verb``; a KeyError for a verb nobody inventoried."""
    return BY_VERB[verb]
