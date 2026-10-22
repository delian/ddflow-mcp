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
    "ddflow/api/surf_setup.py:reviewer_agent_marker": 1,  # a shim over reviewer_trust
}

#: Marker calls per enclosing function ("module:function" -> number of AST `Call` nodes naming
#: `agent_marker`, `is_agent` or the `_is_agent`/`reviewer_agent_marker` wrappers), for every
#: module that is not infrastructure. A new call, or one moved to another function, changes
#: this map, so the test fails until the author decides which verb it gates and amends the row
#: that names the function in `Site.functions`.
MARKER_CALLS: dict[str, int] = {
    "ddflow/api/gates.py:_agent_skip_refusal": 1,
    "ddflow/api/gates.py:record": 1,
    "ddflow/services/grants.py:add": 1,
    "ddflow/services/approval.py:grant": 1,  # approve.partial and chore.approve share it
    "ddflow/services/reviewer_trust.py:approve": 1,
    "ddflow/services/export/select.py:_is_agent": 1,  # the wrapper
    "ddflow/services/export/select.py:enable": 1,
    "ddflow/services/export/select.py:disable": 1,
    "ddflow/services/export/select.py:acknowledge": 1,
    "ddflow/surfaces/commands/review.py:_reviewers_add": 1,
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
        ("ddflow/api/gates.py:_agent_skip_refusal", "ddflow/api/gates.py:record"),
        "ddflow gate skip <id> <gate> --reason ...",
    ),
    Site(
        "grant.add",
        "A",
        T2,
        L1,
        "the item, the grant kind and count, and the grants already recorded",
        ("ddflow/services/grants.py:add",),
        "ddflow grant add ...",
    ),
    Site(
        "approve.partial",
        "A",
        T2,
        L1,
        "the recorded partial outcome (gate_accept digest)",
        ("ddflow/services/approval.py:grant",),
        "ddflow approve <id> <gate>",
    ),
    Site(
        "chore.approve",
        "A",
        T2,
        L1,
        "the current [chore].globs",
        ("ddflow/services/approval.py:grant",),
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
        "export.ack",
        "A",
        T2,
        L1,
        "the export document selection and its content digest",
        (
            "ddflow/services/export/select.py:_is_agent",
            "ddflow/services/export/select.py:enable",
            "ddflow/services/export/select.py:disable",
            "ddflow/services/export/select.py:acknowledge",
        ),
        "ddflow export ack ...",
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
        "upgrade.self",
        "B",
        T2,
        L1,
        "the target version and the upgrade plan digest",
        (),
        "ddflow upgrade --self --yes --reason ...",
        anchor=("ddflow/surfaces/commands/setup.py", 'confirm=["self"]'),
    ),
    Site(
        "plan.confirm",
        "B",
        T2,
        L1,
        "the proposed plan",
        (),
        "ddflow plan confirm --yes --reason ...",
        anchor=("ddflow/api/plan.py", "Confirm with --yes --reason"),
    ),
    Site(
        "skew.override",
        "B",
        T2,
        L1,
        "the installed and the repository version",
        (),
        "ddflow <write command> --allow-older-version --reason ...",
        anchor=("ddflow/surfaces/history_verbs.py", "skew.overridden"),
    ),
    Site(
        "claim.force",
        "C",
        T2,
        L1,
        "the item and the other clone's live lease",
        (),
        "ddflow claim <id> --force --reason ...",
        anchor=("ddflow/services/forbidden.py", "no force flags"),
    ),
    Site(
        "complete.force",
        "C",
        T2,
        L1,
        "the item and its unmet completion conditions",
        (),
        "ddflow complete <id> --force ...",
        anchor=("ddflow/services/forbidden.py", "no force flags"),
    ),
)

BY_VERB = {s.verb: s for s in SITES}


def site(verb: str) -> Site:
    """The row for ``verb``; a KeyError for a verb nobody inventoried."""
    return BY_VERB[verb]
