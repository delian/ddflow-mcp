"""The gated-command table: every operator-only site, as data (RA-inventory, D-RA-Q1/Q7).

One row per VERB an agent may be refused. The row says what the verb is (`tier`), how strong
an answer it needs (`min_level`), which state its approval digests (`digest`), and which
modules enforce the person-only rule (`modules`: the files that call `agent_marker` or
`is_agent`). `tests/test_approvals_sites.py` walks `ddflow/` and fails for a module that makes
such a call and is named by no row, so a new refusal site cannot appear without a row here
(r-reuse-core: this is the one list; the consume check, `doctor` and the docs read it).

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

#: Modules that implement the rule itself or only map an identity to a role; they are not sites.
INFRASTRUCTURE = (
    "ddflow/services/identity.py",  # defines agent_marker / is_agent
    "ddflow/services/actors.py",  # resolves an identity to a role for the ask engine
    "ddflow/api/surf_setup.py",  # a one-line shim over reviewer_trust.agent_marker
)


@dataclass(frozen=True)
class Site:
    verb: str
    family: str
    tier: str
    min_level: int
    #: What the approval's digest binds, in words (the canonical record is built in RA-request).
    digest: str
    #: Files that call agent_marker/is_agent for this verb. Empty for a site with no marker call
    #: (a relayed-consent flag or a hook guard).
    modules: tuple[str, ...] = ()
    #: The CLI form an agent re-runs with `--approval ID`.
    command: str = ""


SITES: tuple[Site, ...] = (
    Site(
        "gate.skip.failed_review",
        "A",
        T2,
        L1,
        "the failed review outcome and its unresolved findings",
        ("ddflow/api/gates.py",),
        "ddflow gate skip <id> <gate> --reason ...",
    ),
    Site(
        "grant.add",
        "A",
        T2,
        L1,
        "the item, the grant kind and count, and the grants already recorded",
        ("ddflow/services/grants.py",),
        "ddflow grant add ...",
    ),
    Site(
        "approve.partial",
        "A",
        T2,
        L1,
        "the recorded partial outcome (gate_accept digest)",
        ("ddflow/services/approval.py",),
        "ddflow approve <id> <gate>",
    ),
    Site(
        "chore.approve",
        "A",
        T2,
        L1,
        "the current [chore].globs",
        ("ddflow/services/approval.py",),
        "ddflow chore approve",
    ),
    Site(
        "reviewers.approve",
        "A",
        T2,
        L1,
        "the pending reviewer entry (endpoint and model)",
        ("ddflow/services/reviewer_trust.py",),
        "ddflow reviewers approve <name>",
    ),
    Site(
        "export.ack",
        "A",
        T2,
        L1,
        "the export document selection and its content digest",
        ("ddflow/services/export/select.py",),
        "ddflow export ack ...",
    ),
    Site(
        "config.reviewer_command",
        "A",
        T3,
        L4,
        "the [[reviewer]] command line (it executes code)",
        ("ddflow/surfaces/commands/review.py",),
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
    ),
    Site(
        "plan.confirm",
        "B",
        T2,
        L1,
        "the proposed plan",
        (),
        "ddflow plan confirm --yes --reason ...",
    ),
    Site(
        "skew.override",
        "B",
        T2,
        L1,
        "the installed and the repository version",
        (),
        "ddflow_* allow_older_version",
    ),
    Site(
        "claim.force",
        "C",
        T2,
        L1,
        "the item and the other clone's live lease",
        (),
        "ddflow claim <id> --force --reason ...",
    ),
    Site(
        "complete.force",
        "C",
        T2,
        L1,
        "the item and its unmet completion conditions",
        (),
        "ddflow complete <id> --force ...",
    ),
)

BY_VERB = {s.verb: s for s in SITES}


def site(verb: str) -> Site:
    """The row for ``verb``; a KeyError for a verb nobody inventoried."""
    return BY_VERB[verb]


def marker_modules() -> frozenset[str]:
    """Every file the table names as calling agent_marker/is_agent."""
    return frozenset(m for s in SITES for m in s.modules)
