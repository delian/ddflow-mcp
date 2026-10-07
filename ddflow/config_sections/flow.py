"""The `[flow]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob

#: `[flow]`'s enumerated values. Declared here, beside the knobs, so `Config.check`
#: refuses a value outside them; `core/flow.py` imports them rather than keeping a copy.
FLOW_MODELS = ("trunk", "gitflow")
FLOW_INTEGRATIONS = ("merge", "pr")
FLOW_FORGES = ("auto", "github", "gitlab")
FLOW_CLAIMS = ("local", "remote")
FLOW_PR_MERGE = ("on_approval", "auto", "human")
FLOW_ON_CHANGES = ("reopen", "block")
FLOW_PORT_STRATEGIES = ("forward-merge", "cherry-pick")


@declare("flow")
@dataclass
class FlowConfig:
    """Branching model, pull-request integration and version tags (RESEARCH R16)."""

    #: trunk | gitflow
    model: str = knob(
        "trunk",
        doc="'trunk' (default): every task branches from and lands on one base branch — today's behaviour. 'gitflow': tasks branch from `develop_branch` as feature/ or bugfix/ branches, hotfixes branch from and land on `production_branch` and are back-merged into develop, and `version cut` makes a release/ branch, merges it to production, tags it and back-merges it.",
        choices=FLOW_MODELS,
        strictest=("trunk", "no safety dimension; the plain model, which moves no branches"),
    )
    #: merge | pr
    # D-fallback-no-remote: a typo never makes ddflow push, open a pull request or write
    # remote refs; outward behaviour happens only when someone sets it correctly.
    integration: str = knob(
        "merge",
        doc="'merge' (default): `ddflow merge` lands the branch locally. 'pr': it pushes the branch and opens (or updates) a pull/merge request instead, releases the lease and parks the item in REVIEW so the agent can take the next task; `ddflow pr sync` completes it when the forge says it merged. Use 'pr' wherever merges need approval.",
        choices=FLOW_INTEGRATIONS,
        strictest=("merge", "a typo never pushes or opens a pull request; lands locally"),
        outward=frozenset({"pr"}),  # pushes the branch, opens a pull request
    )
    #: auto | github | gitlab
    forge: str = knob(
        "auto",
        doc="Which forge CLI opens and reads pull requests: 'github' (`gh`), 'gitlab' (`glab`), or 'auto' — decided from the remote URL. ddflow shells out to the CLI the operator already authenticated; it stores no token.",
        choices=FLOW_FORGES,
        strictest=("auto", "no safety dimension; reads the forge from the remote URL"),
    )
    remote: str = knob(
        "origin",
        doc="The git remote branches are pushed to and tags are published on.",
    )
    #: local | remote
    claims: str = knob(
        "local",
        doc="'local' (default): a claim is an event in this clone's log, so two offline clones can both claim one item (detected afterwards as 'contested'). 'remote': a claim also creates refs/ddflow/claims/<id> on `remote` by compare-and-swap, so only one clone wins while online; release, complete and expiry delete it and a heartbeat extends it. A refused claim names the holder; an unreachable remote refuses the claim (never silently local).",
        choices=FLOW_CLAIMS,
        strictest=(
            "local",
            "a typo never writes claim refs to the remote; claims stay in this clone",
        ),
        outward=frozenset({"remote"}),  # writes refs/ddflow/claims/<id> on the remote
    )
    develop_branch: str = knob(
        "develop",
        doc="gitflow only: the integration branch features and bugfixes branch from and land on.",
    )
    #: "" = the repo's default branch
    production_branch: str = knob(
        "",
        doc="gitflow only: the released branch hotfixes branch from and releases land on. Empty means the repo's default branch (origin/HEAD, else main, else master).",
    )
    feature_prefix: str = knob(
        "feature/",
        doc="gitflow only: branch prefix for ordinary tasks.",
    )
    bugfix_prefix: str = knob(
        "bugfix/",
        doc="gitflow only: branch prefix for a task carrying one of `bugfix_tags`.",
    )
    hotfix_prefix: str = knob(
        "hotfix/",
        doc="gitflow only: branch prefix for a task carrying one of `hotfix_tags`. A hotfix forks from production and lands on production AND develop.",
    )
    release_prefix: str = knob(
        "release/",
        doc="gitflow only: branch prefix `version cut` uses for the release branch.",
    )
    bugfix_tags: list[str] = knob(
        factory=lambda: ["bug", "bugfix", "fix"],
        doc="Item tags that make a task a bugfix: a bugfix/ branch under gitflow, and a PATCH bump when versions are computed.",
    )
    hotfix_tags: list[str] = knob(
        factory=lambda: ["hotfix"],
        doc="Item tags that make a task a hotfix under gitflow (forks from production). A hotfix is also a PATCH bump.",
    )
    #: on_approval | auto | human
    pr_merge: str = knob(
        "on_approval",
        doc="Who presses merge in 'pr' mode. 'on_approval' (default): `pr sync` merges once the forge reports the request APPROVED with no failing checks — a person's approval is still required, and branch protection still applies. 'auto': ask the forge to auto-merge when its own rules are met, at open time. 'human': ddflow never merges; a person does.",
        choices=FLOW_PR_MERGE,
        strictest=("human", "ddflow never merges; a person does"),
        outward=frozenset({"on_approval", "auto"}),  # a merge on the forge
    )
    pr_draft: bool = knob(
        False,
        doc="Open pull requests as drafts.",
    )
    pr_labels: list[str] = knob(
        factory=list,
        doc="Labels added to every pull request ddflow opens.",
    )
    pr_reviewers: list[str] = knob(
        factory=list,
        doc="Reviewers requested on every pull request ddflow opens (GitHub logins or teams; GitLab usernames).",
    )
    stack: bool = knob(
        True,
        doc="While a dependency waits in REVIEW, let its dependents START on top of its branch (a stacked pull request) instead of waiting for the merge. This is what keeps an agent working while humans review. A dependent with two unmerged dependencies on different branches still waits: one branch cannot sit on two.",
    )
    #: reopen | block
    on_changes_requested: str = knob(
        "reopen",
        doc="What `pr sync` does when a reviewer requests changes. 'reopen' (default): the item returns to the queue with the review text attached, so the next agent to claim it sees what to fix. 'block': park it for a person.",
        choices=FLOW_ON_CHANGES,
        strictest=("block", "the item is parked for a person"),
    )
    sync_on_next: bool = knob(
        True,
        doc="Let `ddflow next` run `pr sync` first when items wait in REVIEW or a hotfix's back-merge request is open, so merged work completes and requested changes come back without anyone remembering to ask. Costs one forge call per open request; a forge that cannot be reached is reported, never treated as 'nothing changed'.",
    )
    tag_prefix: str = knob(
        "v",
        doc="Prefix of version tags: `v` makes `v1.4.0`. Tags without it are not versions to ddflow.",
    )
    initial_version: str = knob(
        "0.1.0",
        doc="The version `version cut` proposes when no version tag exists yet.",
    )
    #: maintenance line -> branch, OLDEST first
    lines: dict[str, str] = knob(
        factory=dict,
        doc='Maintenance lines -- older majors still receiving fixes -- as name -> branch, OLDEST FIRST (e.g. `[flow.lines]` `"1" = "maint/1.x"`, `"2" = "maint/2.x"`). The newest line is always the current one, which follows `model` as usual. Empty (default): one line, today\'s behaviour. An item targets a line with `--line`; a fix that must reach several gets `--lines` and ports are generated per `port_strategy`.',
    )
    current_line: str = knob(
        "current",
        doc="The name of the newest line -- the one `model` governs (trunk, or gitflow's develop/production). Items with no `--line` belong to it.",
    )
    #: forward-merge | cherry-pick
    port_strategy: str = knob(
        "forward-merge",
        doc="How a fix reaches several lines. 'forward-merge' (default): it is written on the OLDEST line and each line is merged into the next newer one, so newer lines contain older ones by ancestry -- least bookkeeping, needs lines that have not diverged too far. 'cherry-pick': it is written on the NEWEST line and its landed change is applied to each older line independently -- the usual choice once lines have diverged. A choice, not a preference: see `ddflow flow show`; nobody choosing means the default is applied at first use and recorded, so the project keeps following it.",
        choices=FLOW_PORT_STRATEGIES,
        strictest=("forward-merge", "no safety dimension; the least bookkeeping"),
    )
    #: downstream branches, in order
    environments: list[str] = knob(
        factory=list,
        doc='Environment branches downstream of the current line, IN ORDER -- e.g. ["pre-production", "production"] (GitLab flow). Each mirrors what is deployed there. Work reaches one only by PROMOTION (`ddflow promote add <env>`), one step at a time from the branch before it (the first from the current line\'s target), so production only ever receives what pre-production already has. Empty (default): no environment branches.',
    )
    auto_promote: list[str] = knob(
        factory=list,
        doc="Environments `ddflow next` promotes to by itself: when the branch upstream of one is ahead and no promotion to it is open, a promotion task is filed and offered like any other work -- continuous delivery to, say, pre-production. Empty by default, because a deploy is the operator's call; list only the environments where it should happen without one.",
    )
    #: path -> regex with ONE capture group (the version text): `version cut` bumps these.
    version_files: dict[str, str] = knob(
        factory=dict,
        doc='Files `version cut` bumps, as path -> regex with exactly ONE capture group, the version text (e.g. `[flow.version_files]` `\'pyproject.toml\' = \'^version = "([^"]*)"$\'`). The pattern is matched in multiline mode and must match exactly once; the group is replaced by the new version (no tag prefix) and committed on the branch the tag names -- the release branch under gitflow, so in pr mode it travels in the release request. A trunk or maintenance cut with `integration = "pr"` is refused: the bump would have no request to travel in. Empty (default): nothing is bumped.',
    )
