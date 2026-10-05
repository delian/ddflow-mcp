"""Configuration — every significant constant is a documented, overridable knob.

Resolution order (last wins):

1. the dataclass defaults below,
2. ``<repo>/.ddflow/config.toml``,
3. environment variables ``DDFLOW_<SECTION>_<KNOB>`` (upper-case, e.g.
   ``DDFLOW_LEASE_TTL_S=900``).

Nothing in ddflow reads a bare literal for a policy decision; if you find one, it is
a bug.  ``ddflow config --explain`` prints every knob with its value, its source and
its docstring, which is the discoverability contract this module exists to keep.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------------------
# Knob documentation lives beside the knob, in this dict, keyed "section.knob".
# `ddflow config --explain` renders it.  A knob with no entry here fails a ratchet test
# (tests/test_config.py::test_every_knob_is_documented), so the docs cannot silently rot.
# --------------------------------------------------------------------------------------
KNOB_DOCS: dict[str, str] = {}


def _doc(section: str, knob: str, text: str) -> None:
    KNOB_DOCS[f"{section}.{knob}"] = text


@dataclass
class LeaseConfig:
    """Crash-recoverable ownership of a work item."""

    ttl_s: int = 1800
    heartbeat_s: int = 300
    grace_s: int = 120
    acquire_timeout_s: int = 30
    #: How long a waiter keeps its place in line once it could claim (see the knob doc).
    waiter_reservation_s: int = 300
    reclaim_policy: str = "report"  # report | auto
    #: Paths many items may hold at once (D-shared-globs). See the knob docs below.
    shared_globs: list[str] = field(default_factory=list)
    append_only_globs: list[str] = field(default_factory=list)


_doc(
    "lease",
    "ttl_s",
    "Seconds a lease stays valid without a heartbeat. After this it is EXPIRED and reclaimable. Longer = fewer false expiries when an agent is deep in a slow gate; shorter = faster recovery after a crash.",
)
_doc(
    "lease",
    "heartbeat_s",
    "How often a live agent renews its lease. Must be comfortably below ttl_s (a 6x margin is the default) or a slow turn looks like a crash.",
)
_doc(
    "lease",
    "grace_s",
    "Extra slack added to ttl_s before an expired lease is reported as reclaimable, absorbing clock skew between machines sharing an NFS checkout.",
)
_doc(
    "lease",
    "acquire_timeout_s",
    "How long to block on the flock arbitrating lease acquisition before giving up. On NFS a contended acquire measured ~135 ms, so 30 s is ~200x headroom.",
)
_doc(
    "lease",
    "shared_globs",
    'Files EVERY item edits that ddflow must not merge for you -- generated files (a regenerated config, a lockfile): ["configs/default.toml"]. A path inside one of these is exempt from lease-overlap checks (claim, update, next, wait) and counts as covered for any agent holding a live lease at commit time. ddflow does NOT write a merge driver for them: union-merging a generated file interleaves it. Regenerate it after merging, or declare a driver yourself in .gitattributes; doctor notes a shared glob that has none. For append-only files (a changelog) use append_only_globs instead.',
)
_doc(
    "lease",
    "append_only_globs",
    'Files every item APPENDS to -- a changelog, a research log: ["docs/CHANGELOG.md"]. Shared like shared_globs (no lease-overlap check; covered for any live lease holder), and ddflow WRITES "<glob> merge=union" to .gitattributes for each one, so two items\' added lines both survive the merge. Written when this is set through `ddflow config --set/--append-toml` (or ddflow_configure), and re-synced by `ddflow init`/`adopt` after a hand edit; .gitattributes is tracked, so commit it with the config. Only globs in the COMMITTED config get a line: one set with --local is this machine\'s and writes no rule for every clone. doctor reports a missing line.',
)
_doc(
    "lease",
    "reclaim_policy",
    "'report' (default) never steals an expired lease — it names the worktree so a human can rescue in-flight work. 'auto' reclaims it. 'report' exists because a killed agent leaves FINISHED, uncommitted work behind more often than it leaves garbage.",
)
_doc(
    "lease",
    "waiter_reservation_s",
    "Claims on a contended file are served first come, first served. A live `ddflow wait` (or a claim refused for an overlap and retried) is a place in line; while a waiter is next and its item could be claimed now, a younger or unqueued claim of an overlapping file is refused as 'reserved for <agent>'. The place lapses this many seconds after the waiter could claim (it woke and did not come back), and at once when its process died or its wait deadline passed, so a dead or slow waiter never blocks anyone for long. 0 turns the queue off: whoever claims first wins.",
)


@dataclass
class WorktreeConfig:
    """Git worktree isolation for parallel agents."""

    enabled: bool = True
    root: str = ".ddflow/worktrees"
    branch_prefix: str = "ddflow/"
    base_ref: str = ""  # "" = the repo's default branch, auto-detected
    merge_strategy: str = "no-ff"  # no-ff | ff-only | squash
    remove_on_merge: bool = True
    max_parallel: int = 4
    sync_before_start: bool = True
    adopt_existing: bool = True
    local_files: list[str] = field(default_factory=list)


_doc(
    "worktree",
    "enabled",
    "Whether tasks run in isolated git worktrees. Turn off only for a single-agent, single-task project; parallel agents in one tree destroy each other's work.",
)
_doc(
    "worktree",
    "root",
    "Where worktrees are created, relative to the repo root. Default `.ddflow/worktrees`: inside the project for every agent and harness, so the trees are found, kept and shared in one place (decision D-worktree-home), and git-ignored (`.ddflow/.gitignore`, and a `.gitignore` the root writes into itself), a dot-directory that the default settings of pytest, ruff and ripgrep skip; a tool configured to walk dot-directories or ignore `.gitignore` needs `.ddflow/worktrees` excluded. The former default, `../.ddflow-worktrees`, still works when set; trees created there keep their recorded paths.",
)
_doc(
    "worktree",
    "branch_prefix",
    "Prefix for auto-created task branches, so `git branch --list 'ddflow/*'` enumerates exactly the machine-managed ones.",
)
_doc(
    "worktree",
    "base_ref",
    "Branch new worktrees fork from. Empty means auto-detect the default branch (origin/HEAD, else main, else master) — hardcoding 'main' breaks every 'master' repo.",
)
_doc(
    "worktree",
    "merge_strategy",
    "'no-ff' keeps a merge commit per task (best audit trail), 'ff-only' keeps history linear, 'squash' collapses a task to one commit.",
)
_doc(
    "worktree",
    "remove_on_merge",
    "Remove the worktree after a successful merge. Disable while debugging the pipeline so post-mortem inspection is possible.",
)
_doc(
    "worktree",
    "max_parallel",
    "Ceiling on simultaneously active task worktrees. Guards disk and CPU; the scheduler queues beyond it rather than refusing.",
)
_doc(
    "worktree",
    "adopt_existing",
    "When the caller is already inside a linked git worktree, bind the item to THAT tree and branch instead of creating another. Agent harnesses (Claude Code, Cursor) often isolate the agent themselves; without this, claim builds a rival tree and tells the agent to leave the one holding its uncommitted work. Turn off to always create ddflow's own.",
)
_doc(
    "worktree",
    "sync_before_start",
    "Merge the base branch into the task branch before work starts. Prevents the 98-commits-behind-and-unmergeable failure that motivated this knob.",
)
_doc(
    "worktree",
    "local_files",
    "Git-IGNORED, machine-local files (repo-relative paths) copied from the primary checkout into every worktree a claim binds -- one it creates, the harness tree it adopts, or the item's own tree on a re-claim -- a tool config that must not be committed but is read from each checkout, like .roborev.toml. Never overwrites a file already in the worktree, and never copies a path git tracks (that one arrives with the checkout). A copy that fails is skipped, not raised.",
)


#: `[flow]`'s enumerated values. Declared here, beside the knobs, so `Config.check`
#: refuses a value outside them; `core/flow.py` imports them rather than keeping a copy.
FLOW_MODELS = ("trunk", "gitflow")
FLOW_INTEGRATIONS = ("merge", "pr")
FLOW_FORGES = ("auto", "github", "gitlab")
FLOW_CLAIMS = ("local", "remote")
FLOW_PR_MERGE = ("on_approval", "auto", "human")
FLOW_ON_CHANGES = ("reopen", "block")
FLOW_PORT_STRATEGIES = ("forward-merge", "cherry-pick")


@dataclass
class FlowConfig:
    """Branching model, pull-request integration and version tags (RESEARCH R16)."""

    model: str = "trunk"  # trunk | gitflow
    integration: str = "merge"  # merge | pr
    forge: str = "auto"  # auto | github | gitlab
    remote: str = "origin"
    claims: str = "local"  # local | remote
    develop_branch: str = "develop"
    production_branch: str = ""  # "" = the repo's default branch
    feature_prefix: str = "feature/"
    bugfix_prefix: str = "bugfix/"
    hotfix_prefix: str = "hotfix/"
    release_prefix: str = "release/"
    bugfix_tags: list[str] = field(default_factory=lambda: ["bug", "bugfix", "fix"])
    hotfix_tags: list[str] = field(default_factory=lambda: ["hotfix"])
    pr_merge: str = "on_approval"  # on_approval | auto | human
    pr_draft: bool = False
    pr_labels: list[str] = field(default_factory=list)
    pr_reviewers: list[str] = field(default_factory=list)
    stack: bool = True
    on_changes_requested: str = "reopen"  # reopen | block
    sync_on_next: bool = True
    tag_prefix: str = "v"
    initial_version: str = "0.1.0"
    lines: dict[str, str] = field(default_factory=dict)  # maintenance line -> branch, OLDEST first
    current_line: str = "current"
    port_strategy: str = "forward-merge"  # forward-merge | cherry-pick
    environments: list[str] = field(default_factory=list)  # downstream branches, in order
    auto_promote: list[str] = field(default_factory=list)
    #: path -> regex with ONE capture group (the version text): `version cut` bumps these.
    version_files: dict[str, str] = field(default_factory=dict)


_doc(
    "flow",
    "model",
    "'trunk' (default): every task branches from and lands on one base branch — today's behaviour. 'gitflow': tasks branch from `develop_branch` as feature/ or bugfix/ branches, hotfixes branch from and land on `production_branch` and are back-merged into develop, and `version cut` makes a release/ branch, merges it to production, tags it and back-merges it.",
)
_doc(
    "flow",
    "integration",
    "'merge' (default): `ddflow merge` lands the branch locally. 'pr': it pushes the branch and opens (or updates) a pull/merge request instead, releases the lease and parks the item in REVIEW so the agent can take the next task; `ddflow pr sync` completes it when the forge says it merged. Use 'pr' wherever merges need approval.",
)
_doc(
    "flow",
    "forge",
    "Which forge CLI opens and reads pull requests: 'github' (`gh`), 'gitlab' (`glab`), or 'auto' — decided from the remote URL. ddflow shells out to the CLI the operator already authenticated; it stores no token.",
)
_doc(
    "flow",
    "remote",
    "The git remote branches are pushed to and tags are published on.",
)
_doc(
    "flow",
    "claims",
    "'local' (default): a claim is an event in this clone's log, so two offline clones can both claim one item (detected afterwards as 'contested'). 'remote': a claim also creates refs/ddflow/claims/<id> on `remote` by compare-and-swap, so only one clone wins while online; release, complete and expiry delete it and a heartbeat extends it. A refused claim names the holder; an unreachable remote refuses the claim (never silently local).",
)
_doc(
    "flow",
    "develop_branch",
    "gitflow only: the integration branch features and bugfixes branch from and land on.",
)
_doc(
    "flow",
    "production_branch",
    "gitflow only: the released branch hotfixes branch from and releases land on. Empty means the repo's default branch (origin/HEAD, else main, else master).",
)
_doc(
    "flow",
    "feature_prefix",
    "gitflow only: branch prefix for ordinary tasks.",
)
_doc(
    "flow",
    "bugfix_prefix",
    "gitflow only: branch prefix for a task carrying one of `bugfix_tags`.",
)
_doc(
    "flow",
    "hotfix_prefix",
    "gitflow only: branch prefix for a task carrying one of `hotfix_tags`. A hotfix forks from production and lands on production AND develop.",
)
_doc(
    "flow",
    "release_prefix",
    "gitflow only: branch prefix `version cut` uses for the release branch.",
)
_doc(
    "flow",
    "bugfix_tags",
    "Item tags that make a task a bugfix: a bugfix/ branch under gitflow, and a PATCH bump when versions are computed.",
)
_doc(
    "flow",
    "hotfix_tags",
    "Item tags that make a task a hotfix under gitflow (forks from production). A hotfix is also a PATCH bump.",
)
_doc(
    "flow",
    "pr_merge",
    "Who presses merge in 'pr' mode. 'on_approval' (default): `pr sync` merges once the forge reports the request APPROVED with no failing checks — a person's approval is still required, and branch protection still applies. 'auto': ask the forge to auto-merge when its own rules are met, at open time. 'human': ddflow never merges; a person does.",
)
_doc(
    "flow",
    "pr_draft",
    "Open pull requests as drafts.",
)
_doc(
    "flow",
    "pr_labels",
    "Labels added to every pull request ddflow opens.",
)
_doc(
    "flow",
    "pr_reviewers",
    "Reviewers requested on every pull request ddflow opens (GitHub logins or teams; GitLab usernames).",
)
_doc(
    "flow",
    "stack",
    "While a dependency waits in REVIEW, let its dependents START on top of its branch (a stacked pull request) instead of waiting for the merge. This is what keeps an agent working while humans review. A dependent with two unmerged dependencies on different branches still waits: one branch cannot sit on two.",
)
_doc(
    "flow",
    "on_changes_requested",
    "What `pr sync` does when a reviewer requests changes. 'reopen' (default): the item returns to the queue with the review text attached, so the next agent to claim it sees what to fix. 'block': park it for a person.",
)
_doc(
    "flow",
    "sync_on_next",
    "Let `ddflow next` run `pr sync` first when items wait in REVIEW or a hotfix's back-merge request is open, so merged work completes and requested changes come back without anyone remembering to ask. Costs one forge call per open request; a forge that cannot be reached is reported, never treated as 'nothing changed'.",
)
_doc(
    "flow",
    "tag_prefix",
    "Prefix of version tags: `v` makes `v1.4.0`. Tags without it are not versions to ddflow.",
)
_doc(
    "flow",
    "lines",
    'Maintenance lines -- older majors still receiving fixes -- as name -> branch, OLDEST FIRST (e.g. `[flow.lines]` `"1" = "maint/1.x"`, `"2" = "maint/2.x"`). The newest line is always the current one, which follows `model` as usual. Empty (default): one line, today\'s behaviour. An item targets a line with `--line`; a fix that must reach several gets `--lines` and ports are generated per `port_strategy`.',
)
_doc(
    "flow",
    "version_files",
    'Files `version cut` bumps, as path -> regex with exactly ONE capture group, the version text (e.g. `[flow.version_files]` `\'pyproject.toml\' = \'^version = "([^"]*)"$\'`). The pattern is matched in multiline mode and must match exactly once; the group is replaced by the new version (no tag prefix) and committed on the branch the tag names -- the release branch under gitflow, so in pr mode it travels in the release request. A trunk or maintenance cut with `integration = "pr"` is refused: the bump would have no request to travel in. Empty (default): nothing is bumped.',
)
_doc(
    "flow",
    "current_line",
    "The name of the newest line -- the one `model` governs (trunk, or gitflow's develop/production). Items with no `--line` belong to it.",
)
_doc(
    "flow",
    "port_strategy",
    "How a fix reaches several lines. 'forward-merge' (default): it is written on the OLDEST line and each line is merged into the next newer one, so newer lines contain older ones by ancestry -- least bookkeeping, needs lines that have not diverged too far. 'cherry-pick': it is written on the NEWEST line and its landed change is applied to each older line independently -- the usual choice once lines have diverged. A choice, not a preference: see `ddflow flow show`; nobody choosing means the default is applied at first use and recorded, so the project keeps following it.",
)
_doc(
    "flow",
    "environments",
    'Environment branches downstream of the current line, IN ORDER -- e.g. ["pre-production", "production"] (GitLab flow). Each mirrors what is deployed there. Work reaches one only by PROMOTION (`ddflow promote add <env>`), one step at a time from the branch before it (the first from the current line\'s target), so production only ever receives what pre-production already has. Empty (default): no environment branches.',
)
_doc(
    "flow",
    "auto_promote",
    "Environments `ddflow next` promotes to by itself: when the branch upstream of one is ahead and no promotion to it is open, a promotion task is filed and offered like any other work -- continuous delivery to, say, pre-production. Empty by default, because a deploy is the operator's call; list only the environments where it should happen without one.",
)
_doc(
    "flow",
    "initial_version",
    "The version `version cut` proposes when no version tag exists yet.",
)


@dataclass
class GatesConfig:
    """The per-task and per-phase quality pipelines."""

    task_pipeline: list[str] = field(
        default_factory=lambda: [
            "research",
            "rules",
            "implement",
            "rubber_duck",
            "critic",
            "standards",
            "unit_tests",
            "bug_hunt",
            "dedupe",
            "merge",
        ]
    )
    phase_pipeline: list[str] = field(
        default_factory=lambda: [
            "research",
            "tasks",
            "unit_tests",
            "bug_hunt",
            "dedupe",
            "live_test",
            "corrections",
            "docs",
            "merge",
        ]
    )
    required: list[str] = field(
        default_factory=lambda: [
            "implement",
            "unit_tests",
            "merge",
        ]
    )
    unavailable_is_failure: bool = False
    allow_skip_with_reason: bool = True
    require_outcome: bool = True
    enforce_order: str = "warn"
    promotion_pipeline: list[str] = field(default_factory=lambda: ["unit_tests", "merge"])
    rate_min_runs: int = 5
    rate_max_fail: float = 0.9
    evidence_required: list[str] = field(
        default_factory=lambda: [
            "unit_tests",
            "rubber_duck",
            "critic",
            "standards",
            "docs",
        ]
    )


_doc(
    "gates",
    "task_pipeline",
    "Ordered gate ids every TASK passes through. Reorder or trim per project; ids must exist in [gate.*] definitions.",
)
_doc(
    "gates",
    "phase_pipeline",
    "Ordered gate ids every PHASE passes through. 'tasks' is the fan-out point where member tasks run (in parallel where dependencies allow). 'docs' reviews and updates the documentation for everything the phase changed, before it merges.",
)
_doc(
    "gates",
    "promotion_pipeline",
    "Ordered gate ids a PROMOTION task passes through ([flow].environments). Short by default: a promotion carries work that already passed its own pipeline, so re-running research and review on it measures nothing. Add a human gate here to require a person's sign-off on a deploy.",
)
_doc(
    "gates",
    "required",
    "Gates whose failure BLOCKS completion. Everything else records its outcome and lets the pipeline continue — advisory vs blocking is an explicit field, never a convention.",
)
_doc(
    "gates",
    "require_outcome",
    "Every gate in the pipeline must carry SOME recorded outcome before an item completes — passed, failed, unavailable, partial, or an explicit `gate skip --reason`. Silence is not a pass, for the same reason UNAVAILABLE is not. With this false, `required` alone blocks and the other gates become documentation. Default true: the fixed order is the point of the pipeline.",
)
_doc(
    "gates",
    "enforce_order",
    "What `gate record` does when an EARLIER pipeline gate has no outcome yet: 'warn' (record it, say so), 'block' (refuse), 'off'. Default 'warn' — reviewing before the tests run is sometimes deliberate, skipping research entirely never is, and `require_outcome` is what catches the latter.",
)
_doc(
    "gates",
    "unavailable_is_failure",
    "If true, a gate that could not run (tool missing, endpoint down) blocks like a failure. Default false, but UNAVAILABLE is always recorded distinctly and NEVER as a pass — that distinction is the point.",
)
_doc(
    "gates",
    "allow_skip_with_reason",
    "Permit `ddflow gate skip <id> --reason '...'`. The reason is mandatory and is recorded in the event log, so a skip is auditable rather than invisible.",
)
_doc(
    "gates",
    "evidence_required",
    "Gates that must attach evidence (command, exit code, output digest) for their outcome to count. A bare 'it passed' from these gates is rejected.",
)


@dataclass
class LessonsConfig:
    """Searchable, self-compressing lessons store."""

    search_backend: str = "fts5"  # fts5 | like
    max_results: int = 5
    snippet_chars: int = 320
    cadence_growth_pct: float = 20.0
    cadence_min_entries: int = 25
    auto_capture_on_bug: bool = True
    require_regression_test: bool = True
    reflect_after_items: int = 3


_doc(
    "lessons",
    "reflect_after_items",
    "How many items may finish with NO lesson ever recorded before ddflow says so. One task finishing without a lesson is normal and reporting it would be noise; several in a row is the pattern the rule exists for. 0 disables the check.",
)
_doc(
    "lessons",
    "search_backend",
    "'fts5' uses SQLite full-text BM25 ranking; 'like' is a portable fallback for a SQLite built without FTS5. Detected automatically at init; this knob forces one.",
)
_doc(
    "lessons",
    "max_results",
    "Default number of lessons returned by a search and injected into a session brief. The whole point is to spend ~600 tokens instead of reading a 1.5 MB file.",
)
_doc(
    "lessons",
    "snippet_chars",
    "Characters of each lesson shown in search results and briefs before truncation to the full-entry pointer.",
)
_doc(
    "lessons",
    "cadence_growth_pct",
    "Percent growth in bytes-per-entry AND total bytes since the last compression pass that triggers a new one. Relative, not absolute, so a pass that just ran makes the trigger go quiet.",
)
_doc(
    "lessons",
    "cadence_min_entries",
    "Below this many entries the cadence never fires — compressing a small corpus costs more than it saves.",
)
_doc(
    "lessons",
    "auto_capture_on_bug",
    "Record a lesson automatically whenever a bug is closed, pre-filled from the bug record, so capture is the default rather than an act of virtue.",
)
_doc(
    "lessons",
    "require_regression_test",
    "Refuse to close a bug without a named regression test. This is the rule that stops the same bug shipping twice.",
)


@dataclass
class SessionConfig:
    """Prompt/session logging for recovery and from-scratch reconstruction."""

    log_prompts: bool = True
    redact_patterns: list[str] = field(
        default_factory=lambda: [
            # `key: value` and `key=value`.
            r"(?i)(api[-_ ]?key|token|secret|password|passwd|pwd)\s*[:=]\s*\S+",
            # `Authorization: Bearer <token>` — a SPACE, not a colon, after the scheme.
            # The colon-or-equals pattern above does not match it, so bearer tokens were
            # written to the committed log in full.
            r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{12,}",
            r"(?i)\b(gh[pousr]_[A-Za-z0-9]{16,})\b",
            r"(?i)\b(sk-[A-Za-z0-9_-]{16,})\b",
            r"(?i)\b(xox[abprs]-[A-Za-z0-9-]{10,})\b",
            r"\bAKIA[0-9A-Z]{16}\b",
            # The WHOLE PEM block, not just its header. Matching the header alone left
            # the base64 key body — the actual secret — in the log.
            r"(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            r"(?s)-----BEGIN OPENSSH PRIVATE KEY-----.*?-----END OPENSSH PRIVATE KEY-----",
        ]
    )
    redact_extra: list[str] = field(default_factory=list)
    brief_max_tokens: int = 1200
    brief_lesson_count: int = 4
    replay_verify_diffs: bool = True
    #: The progress block `complete` adds: on | phase | off (see services/progress_line).
    progress_after_complete: str = "on"
    #: Characters of transcript the PreCompact hook keeps as a session note (0 = off).
    compaction_digest_chars: int = 2000


_doc(
    "session",
    "compaction_digest_chars",
    "Before Claude Code compacts the context, the PreCompact hook (`ddflow hooks install --claude`) records the last turns of the transcript -- the operator's words and the agent's answers, no tool traffic, secrets redacted -- as a session note of at most this many characters, and the SessionStart hook hands it back after compaction. The hook gets no summary from Claude Code, so this digest is the record of what the session was doing. 0 turns it off.",
)
_doc(
    "session",
    "progress_after_complete",
    "After every completion, a few lines on where the work stands: tasks, bugs (fixed, open, high) and phases done of total with percentages, the item's phase, and the next ready items. on (default) | phase (only the phase line and next) | off. The agent relays it to the operator; turn it off with `ddflow config session.progress_after_complete off`, or ask the agent to.",
)
_doc(
    "session",
    "log_prompts",
    "Record every operator prompt verbatim as an event. This is what makes 'rebuild the project from the logs alone' possible; turning it off forfeits that.",
)
_doc(
    "session",
    "redact_patterns",
    "Regexes applied to every logged prompt and note before it touches disk. The log is committed, so an unredacted secret is a leaked secret.",
)
_doc(
    "session",
    "redact_extra",
    "More redaction regexes, applied IN ADDITION to redact_patterns. Setting redact_patterns replaces the built-in secret patterns, so a project that copies them to add one of its own freezes them; add yours here instead -- e.g. private-network addresses, so an operator prompt naming a LAN host is masked before it reaches the committed log.",
)
_doc(
    "session",
    "brief_max_tokens",
    "Ceiling on the session-start brief. The brief replaces reading the rulebooks and the lessons file; it must stay small or it defeats itself.",
)
_doc(
    "session",
    "brief_lesson_count",
    "How many task-relevant lessons the brief carries. Retrieved by relevance to the active task text, not by recency.",
)
_doc(
    "session",
    "replay_verify_diffs",
    "During `ddflow replay --verify`, check that each recorded commit still exists and its diff still applies. Catches a log that has drifted from the tree it claims to describe.",
)


@dataclass
class ScheduleConfig:
    """Dependency resolution and parallel fan-out."""

    max_parallel_tasks: int = 4
    ready_policy: str = "deps_and_lease"  # deps_and_lease | deps_only
    cycle_policy: str = "error"  # error | warn
    unknown_dep_policy: str = "block"  # block | warn
    empty_phase: str = "note"  # note | problem | off
    bugs_first: bool = True
    #: `name=capacity` for each physical resource items may declare.
    resources: list[str] = field(default_factory=list)
    #: `name=path` for sibling repositories whose items `needs` may name as `name:ID`.
    repos: list[str] = field(default_factory=list)


_doc(
    "schedule",
    "repos",
    'Sibling repositories a dependency may point into, as `name=path` (relative to this repository\'s root): ["run_nemo_run=../run_nemo_run"]. Then `needs = ["run_nemo_run:132.D"]` waits for item 132.D THERE to be done. `ddflow external sync` reads their logs and records what it observed in this one, so readiness is decided from a dated fact and never by reaching into another repository mid-decision. An unobserved external dependency is unmet.',
)
_doc(
    "schedule",
    "resources",
    'Capacities of the physical resources work may declare with `--resources`, as `name=capacity`: ["gpu=8", "vllm-fleet=1"]. A claim is refused (exit 3) when the live leases\' declared use of a resource plus its own would exceed the capacity -- counted across EVERY holder, because a GPU does not care which agent is using it. A resource named nowhere here has capacity 1: exclusive. Globs keep two agents out of one file; this keeps two agents from both starting an 8-GPU run on an 8-GPU box.',
)


_doc(
    "schedule",
    "empty_phase",
    "How to report an OPEN phase with no task under it — work in the queue that `ddflow next` can never offer. 'note' (default) mentions it, 'problem' fails `doctor`, 'off' stays silent. Configurable because a project that files phases before breaking them down lives in this state on purpose, while one that does not has found a planning gap. A phase whose tasks are all FINISHED while the phase stays open is always a problem and is not covered by this knob: it is not a workflow style, it is a queue held open by an item nobody can act on.",
)
_doc(
    "schedule",
    "bugs_first",
    "Offer bug fixes before features (default true). A task is a bug fix when it carries one of `[flow] bugfix_tags` or `hotfix_tags`, or an OPEN bug record names it (`ddflow bug found --item`). Priority still orders bugs among themselves and features among themselves, and the parallelism cap hands its slots to bugs first -- so a standing bug is fixed before more features are built on it. `false` orders by priority alone.",
)
_doc(
    "schedule",
    "max_parallel_tasks",
    "How many items may be in flight at once, counted across the whole queue. Every live lease counts, worktree or not -- a review task occupies an agent just as a coding task does. Distinct from worktree.max_parallel, which caps only the leases that made a tree.",
)
_doc(
    "schedule",
    "ready_policy",
    "'deps_and_lease' (default) hides items another agent already leased; 'deps_only' shows them, for a human planning view.",
)
_doc(
    "schedule",
    "cycle_policy",
    "What to do when the dependency graph has a cycle. 'error' refuses to schedule, naming the cycle; 'warn' schedules the rest.",
)
_doc(
    "schedule",
    "unknown_dep_policy",
    "A dependency on an id that does not exist. 'block' treats it as unmet so typos surface loudly; 'warn' ignores it. Blocking is the safe default.",
)


@dataclass
class BugsConfig:
    """What recording a bug does to the queue, `[bugs]`."""

    #: `bug found` files the fix task (`fix-<bug>`) in the queue, so the bug is an item in
    #: the DAG: offered first (`[schedule] bugs_first`), holding its files against features,
    #: closed by the task's completion with a regression test. `bug found --no-task` for a
    #: bug fixed in the commit that found it.
    file_task: bool = True
    #: The standing phase a fix task goes under when the bug names no item, or an item whose
    #: phase is finished. Created on first use.
    phase: str = "bugs"


_doc(
    "bugs",
    "file_task",
    "Whether `bug found` files the bug's fix task in the queue (default true): `fix-<bug id>`, tagged as a bug fix, under the phase of the item the bug names (or `[bugs] phase`), carrying that item's globs unless `--globs` says otherwise, and linked to the bug. `bugs_first` offers it before features and a feature on the same files waits behind it, so bugs do not pile up under new work. `complete <fix task>` refuses while the bug is open and `--regression-test` closes it; `bug found --no-task` files no task (a bug fixed in the same commit); `bug file-tasks` files one for every open bug that has none. When the item named is itself an OPEN bug-fix task, that task is the fix and nothing new is filed. `false`: bugs are flat records, as before.",
)
_doc(
    "bugs",
    "phase",
    "The standing phase (default `bugs`) a fix task is filed under when the bug names no item, or names one whose phase is already finished. Made on first use, with the title `Bugs`.",
)


@dataclass
class MemoryConfig:
    """Operational memory: short facts about this machine and repository, `[memory]`."""

    max_chars: int = 280
    brief_items: int = 12


_doc(
    "memory",
    "max_chars",
    "Longest memory `ddflow memory add` accepts. A memory is ONE operational fact ('this box has 8 H200s'); something longer is a lesson or a journal entry, and a store of paragraphs is one nobody reads at session start. 280 is the OptMem record width the source projects used.",
)
_doc(
    "memory",
    "brief_items",
    "How many live memories `ddflow brief` shows, newest first. They are what an agent must know before touching anything on this machine, so they sit near the top of the brief; the rest are one `ddflow memory list` or `recall` away. 0 leaves them out of the brief.",
)


#: The record kinds an add is checked for, and that are offered as candidates. Session
#: prompts and notes are not records anyone duplicates (D-no-duplicates).
DEDUPE_KINDS = ("bug", "task", "phase", "lesson", "decision", "research", "memory")
DEDUPE_ON_MATCH = ("ask", "warn", "off")


@dataclass
class DedupeConfig:
    """Duplicate detection at add time, `[dedupe]` (decision D-no-duplicates)."""

    # "ask": every surface can answer one now -- --new / --extends / --duplicate-of /
    # --related on the CLI (a prompt on a terminal), `relation` over MCP. It was shipped
    # as "warn" while none could (hotfix B-dedupe-default-warn).
    on_match: str = "ask"  # ask | warn | off
    show_floor: float = 0.35
    ask_threshold: float = 0.55
    max_candidates: int = 3
    min_words: int = 8
    kinds: list[str] = field(default_factory=lambda: list(DEDUPE_KINDS))


_doc(
    "dedupe",
    "on_match",
    "What an add does when it looks like an existing record. 'ask' (default): it is refused until answered new / extends X / duplicate of X / related X -- --new / --extends ID / --duplicate-of ID / --related ID on the CLI (a prompt on a terminal, exit 3 with the ready commands for a script), `relation` over MCP. 'warn': the candidates are printed and the add goes ahead. 'off': no check. No score can tell a duplicate from a different bug in the same function (research R-dedupe-matchers), which is why the default asks rather than decides.",
)
_doc(
    "dedupe",
    "show_floor",
    "Cosine similarity (0-1) at which an existing record is listed beside an add, without asking. 0.35 from the research: below it, related records are rare and listing them is noise.",
)
_doc(
    "dedupe",
    "ask_threshold",
    "Cosine similarity (0-1) at which an add must be answered before it proceeds (under on_match = 'ask'). 0.55 from the research: on ddflow's labelled records about half of real duplicates score above it and over three quarters of what does is a duplicate or related record. Identical text, or text naming an existing id, asks whatever the score.",
)
_doc(
    "dedupe",
    "max_candidates",
    "Most similar records shown for one add (records whose id the new text names are shown as well). The research found the existing record in the top 3 for 95% of real duplicates.",
)
_doc(
    "dedupe",
    "min_words",
    "Distinct content words a record needs before a score alone makes an add ask. A two-word title shares most of its words with something; asking on it is noise. Shorter records still list candidates.",
)
_doc(
    "dedupe",
    "kinds",
    "Record kinds checked on add, and offered as candidates -- across kinds, so a new bug is shown the open task that fixes it. Default: bug, task, phase, lesson, decision, research, memory.",
)


@dataclass
class ImportConfig:
    """Adopting ddflow on a project that already has history: `[importer]`.

    Named for the module rather than the command because `import` is a keyword and a
    section called `[import_]` would be a TOML wart the operator has to remember.
    """

    max_tasks: int = 200
    preview_rows: int = 8
    #: Where each source family is read from. Empty = the built-in locations; set = those
    #: paths INSTEAD (see `importer.sources_from`).
    todo_globs: list[str] = field(default_factory=list)
    lesson_globs: list[str] = field(default_factory=list)
    lesson_summary_globs: list[str] = field(default_factory=list)
    decision_globs: list[str] = field(default_factory=list)
    research_globs: list[str] = field(default_factory=list)
    journal_globs: list[str] = field(default_factory=list)
    memory_globs: list[str] = field(default_factory=list)
    archive_globs: list[str] = field(default_factory=list)


_doc(
    "importer",
    "max_tasks",
    "Refuse to propose more tasks than this in one import. An import writes events into a log that is committed to git, and five thousand of them is not recoverable by anything short of editing history; one real repository yielded 4,799. Raise it deliberately once you have looked at what it would write.",
)
for _family, _what in (
    ("todo", "todo checklists (phases and tasks)"),
    ("lesson", "the lessons corpus"),
    ("lesson_summary", "a hand-written lessons summary (a GENERATED one is always skipped)"),
    ("decision", "architectural decision records, one file each"),
    ("research", "the research log"),
    ("journal", "the engineering journal"),
    ("memory", "an OptMem-style `#N date text` memory store"),
):
    _doc(
        "importer",
        f"{_family}_globs",
        f"Paths `ddflow import` reads {_what} from. Empty uses the built-in locations; "
        f"a list REPLACES them rather than adding to them, because the same filename can "
        f"mean opposite things in two projects -- one repository's docs/LOG.md is its whole "
        f"journal, another's is a generated index of it.",
    )

_doc(
    "importer",
    "archive_globs",
    "Todo files that are an ARCHIVE: their open boxes are history until someone names the section. They import as BLOCKED, one phase per section, and `ddflow unblock <phase>` releases a whole section at once. For a long plan file most of whose unticked boxes are notes and filed findings inside sections that shipped long ago -- no classifier over that prose is trustworthy, because whether a box is work is a property of what the operator intends. Matched against repo-relative paths; the files must also be read by `todo_globs` (or its defaults).",
)

_doc(
    "importer",
    "preview_rows",
    "How many items of each kind the import proposal prints before summarising the rest. The whole list is always in the --json output; this only caps the human-readable preview.",
)


@dataclass
class CompanionsConfig:
    """Detecting the MCP servers that serve this project's gates: `[companions]`."""

    probe_cache_ttl_s: int = 300


_doc(
    "companions",
    "probe_cache_ttl_s",
    "How long a companion detection result stays good, in seconds. Each probe shells out and an npx-based one takes seconds on a cold cache, so a session-start hook that listed companions paid that every time. A NEGATIVE result is cached like a positive one; an INCONCLUSIVE one never is, because 'could not tell' is a transient fact and caching it would make a blip stick for the whole window. 0 disables the cache.",
)


@dataclass
class ReinstructConfig:
    """Re-stating the rules after a compaction — the one channel that survives it.

    `initialize` delivers the instruction block ONCE. After a context compaction the model
    may retain none of it, and MCP has no server->client context-injection primitive: the
    three that exist are `roots/list`, `sampling/createMessage` and `elicitation/create`,
    and none of them injects anything. A footer on tool results is the only place the
    server is guaranteed to be heard again, because an agent driving ddflow calls tools
    continuously.

    ON by default, and cadenced rather than cheap-by-accident. An opt-in feature nobody
    enables does not solve the problem it was filed for; a footer on all 63 tools would be
    trained out inside a session and would cost tokens on every call. So it speaks at most
    once every `every_calls` calls AND `every_seconds` seconds, and only when there is
    something specific to say — see `services/obligations.py`.
    """

    enabled: bool = True
    every_calls: int = 12
    every_seconds: int = 240
    max_items: int = 3


@dataclass
class CadenceConfig:
    """Periodic whole-repo passes that a per-task gate structurally cannot do."""

    integration_tests_every_tasks: int = 5
    architecture_review_every_phases: int = 2
    mutation_tests_every_phases: int = 3
    dedupe_sweep_every_tasks: int = 4
    lessons_pass_every_phases: int = 4
    max_missed: int = 1
    #: `name=days` for passes that are due by the CALENDAR, not by completions.
    every_days: list[str] = field(default_factory=list)


_doc(
    "gates",
    "rate_min_runs",
    "How many DECISIVE runs a gate needs before its failure rate is judged. One failure out of one run is 100% and means nothing, so a low value turns a new gate's first red into a finding — which is the crying-wolf failure this check exists to prevent. A skipped gate is not a run.",
)
_doc(
    "gates",
    "rate_max_fail",
    "Failure rate (0.0-1.0) at which a gate is reported as failing on nearly everything. 'A gate that fails on everything is worse than no gate: it trains the next reader to skip it.' At or above this, the gate is flaky or measuring a moving target and re-running it will not converge -- the remedy is to repair the gate, not the work.",
)
_doc(
    "cadence",
    "every_days",
    'Passes that fall due by the calendar rather than by completed work, as `name=days`: ["bug_hunt=7", "dedupe_sweep=7"]. A name equal to a count-based pass (dedupe_sweep, integration_tests, ...) REPLACES it. `ddflow cadence` reports one due when `cadence --ran <name>` has not been recorded within that many days -- or ever, so a weekly pass that has never run is due now rather than silently never. For a rule like \'a bug hunt every week\' that otherwise lives only in prose, which is where it stops happening.',
)
_doc(
    "cadence",
    "max_missed",
    "How many scheduled runs a cadence may have skipped before `doctor` reports it. 1 (default) means 'being due is not a finding -- never firing is'. A cadence scheduled repeatedly that has fired zero times is the failure this measures: on the project ddflow was extracted from, three wakeups were scheduled, none fired, and the 12-hour stall was only noticed because an undesignated mechanism did the work instead.",
)
_doc(
    "reinstruct",
    "enabled",
    "Whether tool results may carry a short footer naming what this project has left undone. The instruction block is delivered once at connect; after a context compaction nothing else re-states it, and MCP has no primitive for injecting context. Set false to silence it entirely.",
)
_doc(
    "reinstruct",
    "every_calls",
    "Minimum tool calls between two footers. A footer on every call is a banner readers learn to skip.",
)
_doc(
    "reinstruct",
    "every_seconds",
    "Minimum seconds between two footers. BOTH this and every_calls must be satisfied, so a burst of calls does not produce a burst of footers.",
)
_doc(
    "reinstruct",
    "max_items",
    "How many outstanding obligations a footer names. Longer than this and it is scrolled past rather than read.",
)
_doc(
    "cadence",
    "integration_tests_every_tasks",
    "Run the integration suite after this many completed tasks. Unit gates are per-task and cannot see cross-task interaction regressions.",
)
_doc(
    "cadence",
    "architecture_review_every_phases",
    "How often to run a whole-repo architecture/complexity review. Catches structural drift no per-diff reviewer can see.",
)
_doc(
    "cadence",
    "mutation_tests_every_phases",
    "How often to mutation-test the suite. A green suite that survives mutation is measuring patience, not coverage.",
)
_doc(
    "cadence",
    "dedupe_sweep_every_tasks",
    "How often to run the cross-file duplication sweep. Per-task dedupe sees only its own diff; drift between two copies needs a repo-wide comparison.",
)
_doc(
    "cadence",
    "lessons_pass_every_phases",
    "How often to consider a lessons compression pass, in addition to the growth trigger.",
)


@dataclass
class RulesConfig:
    """Project rules schema and storage configuration.

    Rules are project-specific patterns, guidelines, or constraints encoded in the
    queue. Configurable limits and allowed values for rules.
    """

    max_rules: int = 200
    max_size_bytes: int = 50000
    tags_allowed: list[str] = field(default_factory=list)  # empty = any
    scopes_allowed: list[str] = field(default_factory=lambda: ["project", "phase", "task"])


_doc(
    "rules",
    "max_rules",
    "Maximum number of rules a project may define. Prevents sprawl; enforced on rule creation.",
)
_doc(
    "rules",
    "max_size_bytes",
    "Maximum size in bytes for a single rule's content. Prevents rules from becoming unwieldy.",
)
_doc(
    "rules",
    "tags_allowed",
    "Whitelist of allowed tag values for rules. Empty (default) means any tag is allowed. Set to enforce a controlled vocabulary.",
)
_doc(
    "rules",
    "scopes_allowed",
    "Which scopes a rule may declare. Defaults to project, phase, task. Can be restricted to a subset.",
)


@dataclass
class CiConfig:
    """The CI gate: the checks that must pass on the merge result before a task is done."""

    command: str = ""
    base: str = ""
    timeout_s: int = 3600
    #: Health of the base after a merge lands: off | fast (lint, format, security: the
    #: pre-commit set without the test hooks) | full (the whole command).
    on_merge: str = "fast"


_doc(
    "ci",
    "command",
    "The command the `ci` gate and `ddflow ci run` execute in a scratch worktree of the branch merged with the base. Empty (default): the project's own pre-push stage, `pre-commit run --hook-stage pre-push --all-files`, when .pre-commit-config.yaml exists; with neither, the gate is UNAVAILABLE (never a pass). Set it to run something else, e.g. `uv run ruff check . && uv run pytest -q`.",
)
_doc(
    "ci",
    "base",
    "The branch merged into the task's branch before the checks run, so an interaction with what the base has become is tested (a parallel merge's effect). Empty (default): the repository's default branch.",
)
_doc(
    "ci",
    "timeout_s",
    "Seconds the CI command may run before the gate records UNAVAILABLE. Default 3600: the pre-push set includes the whole test suite.",
)
_doc(
    "ci",
    "on_merge",
    "Health check of the base right after a merge lands: off | fast (default: the project's pre-commit set without the test hooks, `SKIP=tests,scenarios`; an explicit [ci].command runs as written) | full (the whole command). The result is recorded as `ci.result`; each failing check files one bug and fix task while the bug is open. A project with no CI command is left alone.",
)


@dataclass
class PromptsConfig:
    """Paths to prompt templates that replace the shipped ones.

    Empty means "use the project's `.ddflow/prompts/<name>.md` if present, else the
    packaged default". Set a path here to point somewhere else entirely -- a shared
    prompts repository, for instance.
    """

    review_system: str = ""
    review_user: str = ""
    gate_instruction: str = ""
    session_brief_header: str = ""
    mcp_instructions: str = ""


_doc(
    "prompts",
    "mcp_instructions",
    "Path to the instruction block the MCP server hands the agent on connect — the workflow, the reporting duties and the companion tools it should use. This is the file to edit to change how the project works. `ddflow prompts eject mcp_instructions` writes an editable copy into .ddflow/prompts/.",
)
_doc(
    "prompts",
    "review_system",
    "Path to the reviewer's system prompt template, replacing the shipped one. `ddflow prompts eject` writes an editable copy into .ddflow/prompts/ to start from.",
)
_doc(
    "prompts",
    "review_user",
    "Path to the per-chunk review message template. Variables: intent, context, diff, fence (the backtick fence that safely holds the diff), chunk_index, chunk_total.",
)
_doc(
    "prompts",
    "gate_instruction",
    "Path to the template rendered when an agent asks what a gate requires. Variables: gate, item.",
)
_doc("prompts", "session_brief_header", "Path to the header template for `ddflow brief`.")


@dataclass
class LogConfig:
    """Reading the append-only log — the cost every state-reading call pays."""

    reuse_parsed: bool = True
    max_cached_events: int = 100_000
    #: Commit ddflow's own event shards after complete and release (Bcd3512c891).
    commit_events: bool = True


_doc(
    "log",
    "reuse_parsed",
    "Re-use already-parsed events instead of re-parsing the whole log on every read. Sound because the log is APPEND-ONLY: a line that has been parsed can never change, so only the appended tail is new. Measured at 20k events: JSON parsing is 97ms of a 115ms read (84%), so this is where the time is. Set false to always re-read from scratch — slower, and the only reason to want it is a shard being rewritten in place under a running process, which `ddflow doctor` reports as a content-address mismatch anyway.",
)
_doc(
    "log",
    "commit_events",
    "After complete and release, commit the event shards (`.ddflow/events/*.jsonl`, nothing else) in the primary checkout, so a clone or a pull always gets the whole log. Hooks run as usual; a refusal never fails the operation and `ddflow doctor` lists shards left uncommitted. Nothing is pushed. false leaves committing the log to you.",
)
_doc(
    "log",
    "max_cached_events",
    "Memory ceiling for reuse_parsed, in events. Measured at ~736 bytes per parsed event, so the 100,000 default holds ~74 MB in a long-lived MCP server. Above the ceiling the cache is not used and reads cost what they always did — graceful, not a failure. A project big enough to hit this wants an on-disk state snapshot rather than a bigger process.",
)


#: What `[upgrade].skew` accepts (decision D-upgrade-skew-guard).
UPGRADE_SKEW_POLICIES = ("refuse", "warn", "off")


@dataclass
class UpgradeConfig:
    """Upgrading ddflow in an onboarded project: the version stamp's skew guard."""

    skew: str = "refuse"


_doc(
    "upgrade",
    "skew",
    'What happens when a ddflow OLDER than the one that last worked on this project\'s log (the highest `ddflow.seen` stamp) is asked to WRITE. `refuse` (default): the write is refused with exit 3 and the message `Upgrade ddflow-mcp to >= X`; reads always work; an agent that cannot upgrade asks the user and, only if the user insists, reruns with `--allow-older-version --reason "..."` (CLI) or the `allow_older_version` argument (MCP), which records a `skew.overridden` event for THAT session and marks its events as written by an older version. `warn`: write anyway and say so on stderr. `off`: no check. The older version still stamps itself, so the log records that it wrote. Only a ddflow that ships this guard can refuse: releases before it cannot.',
)


#: The tool tiers `[mcp].tools` accepts. Kept here, not imported from the MCP surface, so
#: config validation does not depend on the surface above it; `tests/test_mcp_tool_tiers.py`
#: asserts the two lists agree.
MCP_TOOL_TIERS = ("core", "standard", "all")


@dataclass
class McpConfig:
    """The MCP server's own knobs. A START-TIME choice: read once when a connection starts."""

    tools: str = "all"


_doc(
    "mcp",
    "tools",
    "Which tools `tools/list` advertises: `core` (the ~30 tools of the daily loop: brief, next, claim, gates, complete, merge, recall, bugs, lessons, decisions, sessions, setup, help), `standard` (core plus the commonly used rest) or `all` (default, every tool). A tool outside the tier is NOT removed: it stays callable by name, and `ddflow_help` and the connection instructions name what the tier hides. Read once at server start, so change it and restart the server; `listChanged` stays false. Set it to cut the ~90 KB tool list a client without deferred tool search pays in context every session (core is under 40 KB). A newer release's tier in a config file is tolerated (see below).",
)


#: What `[export].refresh` accepts: when a selected document regenerates by itself
#: (`services/export/refresh.py`). `off` never writes.
EXPORT_REFRESH_MODES = ("off", "merge", "phase_close", "docs_gate")

#: Where each document kind is written when `[export.<doc>].path` does not say. A copy of
#: each kind's `DocKind.default_target`, kept here because `core` (the lease scheduler,
#: which registers every selected target as a shared path) may not import `services`;
#: tests/test_export_ops.py asserts the two tables agree.
EXPORT_DEFAULT_TARGETS: dict[str, str] = {
    "bugs": "BUGS.md",
    "changelog": "CHANGELOG.md",
    "decisions": "DECISIONS.md",
    "roadmap": "ROADMAP.md",
    "rules": "RULES.md",
    "sessions": "SESSION.md",
    "status": "STATUS.md",
    "worklog": "LOG.md",
}


@dataclass
class ExportConfig:
    """`ddflow export`: which documents are kept, where, and how (decisions D-export*)."""

    documents: list[str] = field(default_factory=list)
    redact: bool = True
    max_bytes: int = 60_000
    refresh: str = "off"
    #: The per-document tables, `[export.<doc>]`: path, mode, template, filters, refresh,
    #: redact. Filled from those tables by `Config._apply`; this field is only the store.
    tables: dict[str, dict[str, Any]] = field(default_factory=dict)

    def table(self, doc: str) -> dict[str, Any]:
        return self.tables.get(doc, {})

    def targets(self) -> list[tuple[str, str, str]]:
        """`(doc, repo-relative path, mode)` of every selected document, selection order.
        A document with no table and no default target (an unknown kind) has none."""
        out = []
        for doc in self.documents:
            t = self.table(doc)
            path = str(t.get("path") or EXPORT_DEFAULT_TARGETS.get(doc, ""))
            if path:
                out.append((doc, path, str(t.get("mode") or "whole")))
        return out


_doc(
    "export",
    "documents",
    "The documents `ddflow export --all` writes and keeps (roadmap, bugs, status, worklog, sessions, decisions, rules, changelog). EMPTY by default: nothing is generated unless selected. `ddflow export` lists every kind with its state; any kind can still be printed or written once on demand whether or not it is listed here. Each selected target is a shared path for leases (no claim is needed to regenerate it).",
)
_doc(
    "export",
    "redact",
    "Strip private addresses and credentials from exported documents (they are public-repo files at the repo root). ON by default: the rendered body is redacted before it is digested, so the header digest and `export --check` cover the redacted text, and the header says `redacted=N`. Secrets, private addresses and hosts, home paths, emails and the machine hostname become [REDACTED:<kind>]; the project's own name is kept. [export.<doc>].redact overrides one document.",
)
_doc(
    "export",
    "max_bytes",
    "Size cap for a document printed to stdout or returned over MCP, in bytes; a cut document ends in an explicit [truncated: N more] footer. 0 = no cap. A file written by --update or --out is never capped. `--max-bytes` overrides per call.",
)
_doc(
    "export",
    "refresh",
    "When selected documents regenerate by themselves: off | merge | phase_close | docs_gate. `merge` regenerates into the item's merge, `phase_close` at phase completion, `docs_gate` at the phase docs gate; `off` never writes. Per document: [export.<doc>].refresh wins.",
)
_doc(
    "export",
    "tables",
    "The per-document tables as one map; write them as [export.<doc>] tables instead, with keys path (repo-relative target), mode (whole | region | append), template (path of a Jinja2 template), filters ({since, limit, status, phase, session, tag}), refresh and redact. Unknown keys in an [export.<doc>] table are skipped with a warning (and ignored in this map form), so a newer release's keys do not stop an older checkout.",
)

#: Keys an `[export.<doc>]` table understands.
EXPORT_TABLE_KEYS = frozenset({"path", "mode", "template", "filters", "refresh", "redact"})
EXPORT_MODES = ("whole", "region", "append")


def _export_table_problem(doc: str, t: Any) -> str:
    """Why `[export.<doc>]` is wrong, or ""."""
    if not isinstance(t, dict):
        return f"[export.{doc}] must be a table"
    for k in ("path", "mode", "template", "refresh"):
        if k in t and not isinstance(t[k], str):
            return f"[export.{doc}].{k} must be a string"
    if "mode" in t and t["mode"] not in EXPORT_MODES:
        return f"[export.{doc}].mode must be one of {', '.join(EXPORT_MODES)}"
    if "refresh" in t and t["refresh"] not in EXPORT_REFRESH_MODES:
        return f"[export.{doc}].refresh must be one of {', '.join(EXPORT_REFRESH_MODES)}"
    if "redact" in t and not isinstance(t["redact"], bool):
        return f"[export.{doc}].redact must be true or false"
    if "filters" in t and not (
        isinstance(t["filters"], dict)
        and all(
            isinstance(k, str)
            and (isinstance(v, str) or (isinstance(v, int) and not isinstance(v, bool)))
            for k, v in t["filters"].items()
        )
    ):
        return f"[export.{doc}].filters must be a table of strings and integers"
    return ""


@dataclass
class LoopsConfig:
    """Runtime loop detection — work that repeats instead of finishing."""

    max_claims_per_item: int = 3
    max_gate_flaps: int = 4
    max_reopens: int = 2
    max_repeated_failures: int = 3
    max_duplicate_items: int = 2
    no_progress_window: int = 60
    on_detect: str = "warn"  # warn | block


_doc(
    "loops",
    "max_claims_per_item",
    "How many times an item may be claimed and given up WITHOUT completing before it is reported as thrashing. Expiries (crash recovery) are excluded — only deliberate release/re-claim cycles count, because a crash is a different problem with a different remedy.",
)
_doc(
    "loops",
    "max_gate_flaps",
    "How many times a gate's verdict may flip between passed and failed on one item before it is reported as flapping. A gate that cannot decide is flaky or measuring a moving target; re-running it will not converge.",
)
_doc(
    "loops",
    "max_repeated_failures",
    'How many CONSECUTIVE failed runs of one gate with the SAME output digest on one item are reported as `repeated_failure` (default 3; 0 turns the detector off). A pass, a different output, or a failure with no recorded digest ends the streak; the reviewer gates (rubber_duck, critic) are never counted. It warns in `doctor`, `ddflow loops` and the item\'s brief; with `on_detect = "block"` it also refuses `claim`, and refuses a `gate run` of that gate while the work is byte-for-byte what last failed. The digest is over the raw output, so a gate that prints timings never repeats -- a deterministic summary line is what makes it comparable.',
)
_doc(
    "loops",
    "max_reopens",
    "How many times an item may be COMPLETED before that is reported as work that will not stay done — usually a sign the acceptance criteria are not written in the item, so each pass finishes something different.",
)
_doc(
    "loops",
    "max_duplicate_items",
    "How many live items may declare exactly the same file globs before that is reported. Two items writing one file cannot run in parallel; whether one re-describes the other is a question for their titles and bodies, not their globs.",
)
_doc(
    "loops",
    "no_progress_window",
    "How many recent events with NO completion, gate pass or merge count as a stalled queue. Measured in events, not minutes, because an agent that is thinking produces no events and waiting is not looping.",
)
_doc(
    "loops",
    "on_detect",
    "'warn' reports loops in `doctor` and `next` and lets work continue; 'block' additionally makes `ddflow claim` REFUSE an item that is already looping, which is the only thing that actually stops an agent spinning on it.",
)


@dataclass
class ReviewConfig:
    """The review-round budget (decision D-review-budget): a shipped default for every
    project, so a review loop is bounded without anyone writing a config line."""

    max_rounds: int = 2
    on_exceed: str = "refuse"  # refuse | warn
    delta_default: bool = True


_doc(
    "review",
    "max_rounds",
    'How many FULL cross-family review rounds one gate (rubber_duck, critic) may have on one item (default 2; 0 = unlimited). A round is a review of the item\'s whole diff; later rounds each find fewer defects than the one before, so after the cap the way forward is `ddflow review <id> --gate G --delta` (a recheck of ONLY what changed since the reviewed head) and `ddflow review triage` (refute or confirm each remaining finding with a probe) -- both are always allowed, as is a re-review of named chunks (--chunk). `--force --reason "..."` runs one more full round and records why. Change it for the project (`ddflow config --set review.max_rounds 3`), for this machine (add --local), per run (DDFLOW_REVIEW_MAX_ROUNDS), or over MCP with `ddflow_configure` (the operator is told when an agent does).',
)
_doc(
    "review",
    "on_exceed",
    "What a full round beyond [review].max_rounds does: 'refuse' (default; exit 3, naming --delta, `review triage`, --force --reason and how to change the cap) or 'warn' (run it and say the budget is spent). A delta recheck and triage are never refused either way.",
)
_doc(
    "review",
    "delta_default",
    "Whether `ddflow review <id> --gate G` on a gate that already has a recorded review rechecks ONLY the commits since the head that review covered (default true) instead of the whole diff again. The delta's findings and coverage are merged into the gate's record (earlier findings keep their triage when byte-identical), it is not a full round, and the output says 'delta review of N commits since <sha>' so it is never mistaken for a full pass. `--full` forces a full round (counted against review.max_rounds); a branch that was rebased since (the reviewed head is no ancestor) and, for the automatic delta, a prior review that was partial fall back to a full round and say why; a review that never reached a reviewer records no reviewed head, so the next delta starts from the last real one. false = every review is a full round, the behaviour before this knob. Change it for the project (`ddflow config review.delta_default false`), for this machine (add --local), per run (DDFLOW_REVIEW_DELTA_DEFAULT=0), or over MCP with `ddflow_configure` (the operator is told when an agent does).",
)


@dataclass
class EnforceConfig:
    """Mechanical enforcement — the layer that does not rely on the agent agreeing."""

    commit_without_lease: str = "warn"  # block | warn | off
    install_hooks_on_setup: bool = True
    require_item_trailer: bool = False
    item_trailer_keys: list[str] = field(default_factory=lambda: ["Item"])
    forbidden_trailers: list[str] = field(default_factory=list)
    trailer_waivers: dict[str, list[str]] = field(default_factory=dict)
    generated_views: str = "block"  # block | warn | off
    stale_docs: str = "warn"  # block | warn | off
    environment_commits: str = "block"  # block | warn | off
    doc_globs: list[str] = field(default_factory=lambda: ["**/*.md", "**/*.rst", "**/*.adoc"])
    doc_exclude: list[str] = field(
        default_factory=lambda: [
            "**/CHANGELOG*", "**/HISTORY*", "**/NEWS*", ".ddflow/**", "docs/ddflow/**",
        ]
    )  # fmt: skip
    stale_rules: str = "block"  # block | warn | off
    readme_with_code: str = "warn"  # block | warn | off
    readme_code_globs: list[str] = field(default_factory=lambda: ["ddflow/**"])
    readme_files: list[str] = field(default_factory=lambda: ["README.md"])
    behind: str = "warn"  # block | warn | off
    max_behind: int = 50


_doc(
    "enforce",
    "commit_without_lease",
    "What the pre-commit hook does when a commit touches paths no live lease of yours covers. 'block' refuses (the only real enforcement ddflow has), 'warn' prints and allows, 'off' disables. Default 'warn' so adoption never breaks an existing repo on day one; switch to 'block' once the queue is populated.",
)
_doc(
    "enforce",
    "install_hooks_on_setup",
    "Install the pre-commit hook during `ddflow adopt`. The hook is what makes the workflow enforced rather than merely described; disable only if your project manages hooks centrally.",
)
_doc(
    "enforce",
    "require_item_trailer",
    "Require every commit to carry an `Item: <id>` git trailer (or another key from item_trailer_keys) whose value is the id of an item in the queue -- a phase or task in any state but removed -- or a key from trailer_waivers carrying one of its words; checked by the commit-msg hook `ddflow hooks install` adds. A mistyped id is refused, naming it and the nearest real ids; a queue the hook cannot read is exit 2 (could not run), never a pass. Makes commits reconcilable against the queue by `git log --format='%(trailers:key=Item)'` instead of by parsing prose. Off by default because it is noisy on a repo with non-agent contributors.",
)
_doc(
    "enforce",
    "item_trailer_keys",
    'Trailer keys that satisfy require_item_trailer; any one of them will do, and each must name an item in the queue unless trailer_waivers gives its key a vocabulary. A project that has written `Phase: <id>` (or `Phase-ships: none` for a commit that ships no item) in every commit for months keeps its convention: set ["Phase", "Phase-ships"] and declare Phase-ships in trailer_waivers. Matched case-insensitively, as git\'s `%(trailers:key=...)` does; every accepted trailer on a commit must be valid, not just one. Checked by the commit-msg hook on the message being committed; merge commits are exempt.',
)
_doc(
    "enforce",
    "forbidden_trailers",
    'Trailer keys the commit-msg hook REFUSES, e.g. ["Co-'
    + "Authored-By\"] for a project that never credits a tool in its history. Case-insensitive; any line starting with `<key>:` counts, not only git's final-paragraph trailers, and merge commits are NOT exempt. Enforced by git's commit-msg hook, so it holds for every agent and every route that runs git hooks (`git commit -F`, the editor, merges), which a harness-side hook reading only the command text cannot see; `--no-verify` and plumbing skip it, as they skip every hook. Empty by default.",
)
_doc(
    "enforce",
    "trailer_waivers",
    'Trailer keys that mark a commit shipping NO item, each with the only words its value may take: `{ "Phase-ships" = ["none", "filing", "recon", "evidence", "followup"] }`. A trailer whose key is here AND in item_trailer_keys passes only with one of its words (`Phase-ships: bogus` is refused, listing them); every other item_trailer_keys trailer must carry an item id. A key here satisfies require_item_trailer whether or not item_trailer_keys also lists it. Empty by default: every accepted key names an item. Set it with the TOML inline table, `ddflow config --set enforce.trailer_waivers \'{ "Phase-ships" = ["none"] }\'`; JSON is the environment\'s form only: DDFLOW_ENFORCE_TRAILER_WAIVERS=\'{"Phase-ships": ["none"]}\'.',
)
_doc(
    "enforce",
    "generated_views",
    "What the pre-commit hook does when a STAGED view generated by `ddflow render` differs from what the log regenerates now -- hand-edited, or stale because the queue moved after rendering. 'block' refuses, 'warn' prints and allows, 'off' disables. Default 'block': it fires only on a commit that includes a view, and the remedy is one command (`ddflow render`, then re-stage).",
)
_doc(
    "enforce",
    "environment_commits",
    "What the pre-commit hook does with a commit made directly on a branch listed in [flow].environments (GitLab flow's upstream-first): 'block' (default) refuses it and names the remedy (work on a branch, then `ddflow promote status`), 'warn' prints and allows, 'off' disables. Merge and squash commits are never refused: that is how a promotion lands. Commits that stage only ddflow's own files are exempt.",
)
_doc(
    "enforce",
    "stale_docs",
    "What the pre-commit hook does when the commit removes or renames an identifier (snake_case, camelCase, --flag), a file, or a `name = value` default, and a doc file still names it on a line the commit does not touch. 'block' refuses, 'warn' prints and allows, 'off' disables. Default 'warn': the check is a heuristic over identifier-SHAPED tokens, and a false refusal on day one teaches --no-verify; switch to 'block' once its reports have been trustworthy here.",
)
_doc(
    "enforce",
    "doc_globs",
    "Which tracked files are documentation for stale_docs, in git's glob pathspec syntax (`*` stops at `/`, `**/` is any depth). A matching file is searched for stale mentions, and its own removed lines are never taken as code removals. `*.txt` is deliberately NOT a default: CMakeLists.txt and requirements.txt are code, and calling them docs hid every name they removed; add a project's own text docs by path.",
)
_doc(
    "enforce",
    "doc_exclude",
    "Documentation that legitimately names removed things and is never reported by stale_docs: changelogs and history, ddflow's own generated views and log. Add a project's backlog or decision records here -- a page whose job is to remember the old name.",
)
_doc(
    "enforce",
    "stale_rules",
    "What the pre-commit hook does when the branch the work merges into (the item's recorded base, else the default branch) changed a rulebook since this branch forked and the change is not merged in: AGENTS.md, CLAUDE.md, CLAUDE.local.md, .ddflow/config.toml, the driver docs under docs/ddflow/drivers/, or any agent's native rules file. A session loads its rules once, so the edit is silently ignored here until merged. 'block' refuses and says `git merge <base>` then re-read the named files, 'warn' prints and allows, 'off' disables. Default 'block': it is precise, and the commit concluding that merge is never refused. The SessionStart hook reports the same drift but only informs.",
)
_doc(
    "enforce",
    "readme_with_code",
    "What `complete`, `gate status` and `brief` do about a TASK whose diff changes a path in readme_code_globs but none of readme_files, with no 'docs' outcome recorded for it (`gate skip <id> docs --reason ...`, or `gate record <id> docs --outcome passed --evidence ...` naming the section changed). 'warn' reports it (a `complete` warning, a line in `gate status` and in the item's `brief`), 'block' makes `complete` refuse, 'off' disables. Test files (a tests/, test/, spec/, specs/ or __tests__/ directory; test_*.*, *_test.*, *_spec.*, *.test.*, *.spec.*, conftest.py) and documentation (a docs/ or doc/ directory; .md, .rst, .adoc, .txt files) never count as code, and ddflow's own event-log commits (`.ddflow/**`) are not in the default readme_code_globs. When git cannot say what the task changed, `complete` says the check could not run (a warning, never a blocker). Default 'warn': a README is the user's, and the report names the one-line remedy.",
)
_doc(
    "enforce",
    "readme_code_globs",
    "Paths whose change is user-visible and so should reach the README (readme_with_code), in git's glob pathspec syntax. Default `ddflow/**`, ddflow's own package; set a project's own source directories. Docs and `.ddflow/**` are not listed and so are exempt; a test or documentation file inside a listed path is exempt too.",
)
_doc(
    "enforce",
    "readme_files",
    "The files that count as updating the README for readme_with_code, as paths from the repository root (`docs/README.md` is not `README.md`). Default README.md.",
)
_doc(
    "enforce",
    "behind",
    "What the pre-commit hook does when the branch is more than max_behind commits behind the branch its work merges into. 'block' refuses, 'warn' prints and allows, 'off' disables. Default 'warn': being behind is not by itself wrong, but drift compounds (a branch 98 commits behind had to be hand-ported). When git cannot tell (unborn HEAD, unresolvable base) either policy warns with the reason and never blocks.",
)
_doc(
    "enforce",
    "max_behind",
    "The commit count past which `behind` fires; at or below it the check is silent. Must be >= 1 -- 0 or a negative number is refused as invalid rather than read as 'off', which is the `behind` knob's job.",
)


#: Model-name substring -> pretraining family, used to answer the one question the
#: review stack rests on: "is this reviewer independent of the author?"
#:
#: One map, in one module. There were two — this one and a richer `FAMILY_HINTS` in
#: `reviewer.py` — with different entries AND different answers for an unrecognised
#: name, which is the duplicate-then-drift class that has already cost this package
#: three bugs. The reviewer config's own `family` field is how a project classifies a
#: model this map does not know.
FAMILY_HINTS: dict[str, str] = {
    "qwen": "alibaba",
    "claude": "anthropic",
    "gpt": "openai",
    "o1": "openai",
    "o3": "openai",
    "codex": "openai",
    "gemini": "google",
    "gemma": "google",
    "llama": "meta",
    "mistral": "mistral",
    "mixtral": "mistral",
    "deepseek": "deepseek",
    "grok": "xai",
    "phi": "microsoft",
    "command": "cohere",
    "yi-": "01ai",
    "glm": "zhipu",
    "nemotron": "nvidia",
    "granite": "ibm",
    "kimi": "moonshot",
    "minimax": "minimax",
    "ernie": "baidu",
}


def family_for(model: str, families: dict[str, str] | None = None) -> str:
    """Pretraining family for a model name, or ``""`` when it is not recognised.

    The empty string is deliberate. Both former implementations returned a non-empty
    stand-in for an unknown model — one the literal name, one the string "unknown" —
    and both therefore compared unequal to every real family, so an unclassified
    reviewer *established* independence. Two unrecognised names may well be the same
    family; the honest answer is "cannot tell", and a check built to refuse unverified
    independence must not be satisfied by the absence of information.
    """
    low = (model or "").lower()
    for needle, fam in (families if families is not None else FAMILY_HINTS).items():
        if needle in low:
            return fam
    return ""


def router_set(model: str, routers: dict[str, list[str]]) -> list[str] | None:
    """The family SET a router model draws on, or ``None`` when ``model`` is no router.

    A router (Copilot's HydraFusion) is chosen like a model but sends each task to
    models from several providers, so it has no one family to compare a reviewer with.
    Mapping it to a single family in `[agent].families` is the trap: a reviewer from one
    of the OTHER families it drew on would then pass as independent of work that family
    helped write. ``[]`` -- a known router whose members nobody has filled in -- is
    returned as such, never as ``None``: "a router, set unknown" must refuse, and
    falling through to `family_for` would refuse with the wrong remedy.

    Matched like `family_for`, by case-blind substring, and checked BEFORE it: a router
    name may contain a family needle and must not be taken for that one family. But
    where `family_for` may stop at the first match, this takes the UNION of every
    matching entry: first-match-wins on key order let `hydra = ["anthropic"]` hide
    `hydrafusion = ["openai", ...]`, and an openai reviewer then passed as independent
    of work openai wrote (B15af2d6420). Any matching entry left empty keeps the whole
    answer "set unknown" -- another entry's members are not the full set.
    """
    low = (model or "").lower()
    found: set[str] | None = None
    for needle, members in routers.items():
        if not (needle and needle.lower() in low):
            continue
        these = {m.strip().lower() for m in members if m.strip()}
        if not these:
            return []
        found = (found or set()) | these
    return None if found is None else sorted(found)


@dataclass
class AgentConfig:
    """How ddflow talks to whichever agent is driving it."""

    id: str = ""  # "" = derive from hostname+pid
    reviewer_family_must_differ: bool = True
    families: dict[str, str] = field(default_factory=lambda: dict(FAMILY_HINTS))
    # HydraFusion ships with NO members: GitHub publishes no fixed roster (research
    # R-hydrafusion-families), and a guessed set that misses a provider passes that
    # provider's reviewer as independent.
    routers: dict[str, list[str]] = field(default_factory=lambda: {"hydrafusion": []})


_doc(
    "agent",
    "id",
    "Stable identity for this agent process, used to shard the event log so concurrent agents never write the same file. Empty auto-derives host-pid.",
)
_doc(
    "agent",
    "reviewer_family_must_differ",
    "Require at least one reviewer from a different pretraining family than the author. A same-family reviewer shares the author's blind spots, so its agreement is not independent evidence.",
)
_doc(
    "agent",
    "families",
    "Model-name substring to pretraining-family map, used to enforce the rule above. Extend it as new families appear; an unknown model establishes nothing -- an unrecognised author is refused, an unrecognised reviewer is not counted as different.",
)
_doc(
    "agent",
    "routers",
    'Model-name substring to the SET of families a router author draws on -- a model that routes each task across providers, such as Copilot\'s HydraFusion. A reviewer is independent of a router only when its family is outside the whole set; every entry whose name matches adds its families, and one left empty makes the set unknown. Checked before `families`. Default {hydrafusion = []}: GitHub publishes no fixed roster, so the set is empty and `complete --model hydrafusion` refuses until you list the families your plan routes to, e.g. routers = { hydrafusion = ["anthropic", "openai", "google"] }. From the env, JSON only.',
)


@dataclass
class Config:
    lease: LeaseConfig = field(default_factory=LeaseConfig)
    worktree: WorktreeConfig = field(default_factory=WorktreeConfig)
    flow: FlowConfig = field(default_factory=FlowConfig)
    gates: GatesConfig = field(default_factory=GatesConfig)
    lessons: LessonsConfig = field(default_factory=LessonsConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    bugs: BugsConfig = field(default_factory=BugsConfig)
    importer: ImportConfig = field(default_factory=ImportConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    dedupe: DedupeConfig = field(default_factory=DedupeConfig)
    companions: CompanionsConfig = field(default_factory=CompanionsConfig)
    cadence: CadenceConfig = field(default_factory=CadenceConfig)
    reinstruct: ReinstructConfig = field(default_factory=ReinstructConfig)
    enforce: EnforceConfig = field(default_factory=EnforceConfig)
    loops: LoopsConfig = field(default_factory=LoopsConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)
    log: LogConfig = field(default_factory=LogConfig)
    upgrade: UpgradeConfig = field(default_factory=UpgradeConfig)
    mcp: McpConfig = field(default_factory=McpConfig)
    ci: CiConfig = field(default_factory=CiConfig)
    prompts: PromptsConfig = field(default_factory=PromptsConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    rules: RulesConfig = field(default_factory=RulesConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)

    #: where each knob's final value came from -- "default" | "file" | "local" | "env"
    sources: dict[str, str] = field(default_factory=dict, repr=False)
    #: Keys a config FILE carried that this code does not know -- "sec.knob", or "[sec]"
    #: for a whole section. Skipped, not fatal: see `_apply`.
    unknown_knobs: list[str] = field(default_factory=list, repr=False)

    # -- loading ------------------------------------------------------------------
    @classmethod
    def load(cls, root: Path | None = None, *, env: dict[str, str] | None = None) -> Config:
        cfg = cls()
        env = os.environ if env is None else env
        for sec in cfg._sections():
            for f in fields(getattr(cfg, sec)):
                cfg.sources[f"{sec}.{f.name}"] = "default"

        if root is not None:
            path = Path(root) / ".ddflow" / "config.toml"
            if path.is_file():
                data = tomllib.loads(path.read_text("utf-8"))
                # Lenient only when the code is ANOTHER tree's: there the file may be
                # newer than the code. In the tree the code came from, the two are one
                # commit, so an unknown key can only be a typo -- and skipping it (with
                # just a warning) was how 81a52e3 let a typo in the primary stand.
                cfg._apply(data, "file", lenient=not _is_code_tree(Path(root)))
            # The MACHINE-LOCAL layer, read last: .ddflow/local/ is git-ignored, so what
            # belongs to whoever runs this checkout -- their services, their machine's
            # sizing -- overrides the committed, generic config without ever reaching git.
            local = Path(root) / ".ddflow" / "local" / "config.toml"
            if local.is_file():
                cfg._apply(tomllib.loads(local.read_text("utf-8")), "local")

        envdata: dict[str, dict[str, Any]] = {}
        for sec in cfg._sections():
            for f in fields(getattr(cfg, sec)):
                key = f"DDFLOW_{sec.upper()}_{f.name.upper()}"
                if key in env:
                    envdata.setdefault(sec, {})[f.name] = env[key]
        if envdata:
            cfg._apply(envdata, "env")
        if root is not None:
            _warn_unknown(cfg.unknown_knobs, Path(root))
        return cfg

    @classmethod
    def check(cls, data: dict[str, Any]) -> None:
        """Would this TOML load? Raises `ValueError` naming the first thing wrong.

        The semantic half of validation, which the write paths did not have. They
        parsed the merged text for SYNTAX and then validated the config already on
        DISK -- so `[gatez]`, or a knob nobody has ever heard of, sailed through and
        was written, and every later command failed to load the file. A writer that
        validates the state it is replacing has checked nothing.

        Applied to a throwaway instance so a rejected fragment cannot leave a
        half-updated Config behind.
        """
        cls()._apply(data, "check")

    def _sections(self) -> list[str]:
        return [f.name for f in fields(self) if f.name not in ("sources", "unknown_knobs")]

    #: Top-level TOML tables that are NOT config sections and must not be treated as
    #: typos. They are consumed by other loaders: `[gate.*]` by gates.load_gates,
    #: `[[reviewer]]` by reviewer.load_reviewers.
    #: Tables in `.ddflow/config.toml` that belong to another module, so this loader
    #: passes over them rather than rejecting them as unknown sections. Each has its own
    #: reader with its own dataclass, and each of those raises on a field it does not
    #: know — the ignorance here is about the TABLE, never about its contents.
    #:
    #: `companion` was missing, which is why companions could only be configured in
    #: their own file: putting a `[[companion]]` block in the obvious place made the
    #: whole config unreadable. The list and the readers must stay in step, and
    #: `tests/test_roborev_findings.py` asserts they do.
    _FOREIGN_TABLES = frozenset({"gate", "reviewer", "companion", "macro"})

    def _apply(self, data: dict[str, Any], source: str, *, lenient: bool | None = None) -> None:
        # A key a config FILE carries that this code does not know is recorded and
        # skipped, never fatal. Several checkouts of one repository run different
        # versions of ddflow -- a worktree whose branch predates a knob runs its own,
        # older code against the primary's newer config -- and raising here refused every
        # command and every commit until the branch merged main, for a reason unrelated
        # to the work (bugs Bcfc0d22a09, B9cb7dd1c3b). A typo is still loud: `load`
        # names every entry on stderr, `doctor` reports each as a problem, the WRITE
        # paths (source "check") still refuse an unknown key, and `load` passes
        # lenient=False where the file and the code are the same tree's.
        if lenient is None:
            lenient = source in ("file", "local")
        for sec, values in data.items():
            if sec in self._FOREIGN_TABLES:
                continue
            if sec not in self._sections() and lenient:
                self.unknown_knobs.append(f"[{sec}]")
                continue
            if sec not in self._sections():
                # A typo'd section used to be skipped in silence -- so `[leases]` for
                # `[lease]` left every knob at its default while the operator believed
                # the file was in effect. That is the silent-knob-drop class, in the
                # module written to prevent it.
                near = [
                    s for s in self._sections() if s.startswith(sec[:3]) or sec.startswith(s[:3])
                ]
                raise ValueError(
                    f"unknown config section [{sec}]."
                    + (f" Did you mean [{near[0]}]?" if near else "")
                    + f" Known sections: {', '.join(sorted(self._sections()))}"
                )
            if not isinstance(values, dict):
                raise ValueError(
                    f"[{sec}] must be a table, got {type(values).__name__}. "
                    f"(Did you write [[{sec}]] instead of [{sec}]?)"
                )
            target = getattr(self, sec)
            known = {f.name: f for f in fields(target)}
            knobs_only = (
                self._apply_export_tables(values, lenient, source) if sec == "export" else values
            )
            for knob, raw in knobs_only.items():
                if knob not in known and lenient:
                    self.unknown_knobs.append(f"{sec}.{knob}")
                    continue
                if knob not in known:
                    raise ValueError(f"unknown knob '{sec}.{knob}'. Known: {sorted(known)}")
                value = _coerce(raw, known[knob].type)
                check = _KNOB_CHECKS.get(f"{sec}.{knob}")
                if check and (why := check(value)):
                    if lenient and f"{sec}.{knob}" in _TOLERANT_VALUES:
                        # A value this code does not know, in a file that may be NEWER
                        # than the code (a later release's tier): recorded like an unknown
                        # knob, so no command stops loading config -- and an enum knob
                        # takes its STRICTEST value, not its default, so a typo in a
                        # tightened setting fails closed (D-enum-fallback-strict).
                        if f"{sec}.{knob}" not in KNOB_STRICTEST:
                            self.unknown_knobs.append(f"{sec}.{knob} = {value!r}")
                            continue
                        bad, value = value, strictest(f"{sec}.{knob}")
                        self.unknown_knobs.append(
                            f"{sec}.{knob} = {bad!r} (not a value this ddflow knows; "
                            f"in effect: {value!r}, the strictest)"
                        )
                    else:
                        raise ValueError(f"invalid {sec}.{knob} = {value!r}: {why}")
                setattr(target, knob, value)
                self.sources[f"{sec}.{knob}"] = source

    def _apply_export_tables(
        self, values: dict[str, Any], lenient: bool, source: str
    ) -> dict[str, Any]:
        """Move the `[export.<doc>]` sub-tables into `export.tables`; return the plain knobs.

        A dict value under a name that is not an `[export]` knob is a document's table.
        Unknown keys inside one are skipped with a warning in a file (a newer release's
        key) and refused when written (`config --set`), like unknown knobs elsewhere.
        """
        knobs = {f.name for f in fields(self.export)}
        plain: dict[str, Any] = {}
        for k, v in values.items():
            if k in knobs or not isinstance(v, dict):
                plain[k] = v
                continue
            clean = {}
            for key, val in v.items():
                if key not in EXPORT_TABLE_KEYS:
                    if lenient:
                        self.unknown_knobs.append(f"export.{k}.{key}")
                        continue
                    raise ValueError(
                        f"unknown key '{key}' in [export.{k}]. Known: {sorted(EXPORT_TABLE_KEYS)}"
                    )
                clean[key] = val
            if why := _export_table_problem(k, clean):
                if lenient:  # a value a newer release defines: skipped, never fatal
                    self.unknown_knobs.append(f"export.{k} ({why})")
                    continue
                raise ValueError(f"invalid [export.{k}]: {why}")
            self.export.tables[k] = {**self.export.tables.get(k, {}), **clean}
            self.sources["export.tables"] = source
        return plain

    def as_dict(self) -> dict[str, Any]:
        out = dataclasses.asdict(self)
        out.pop("sources", None)
        return out

    def explain(self) -> list[tuple[str, Any, str, str]]:
        """(key, value, source, doc) for every knob -- what `config --explain` prints."""
        rows = []
        for sec in self._sections():
            for f in fields(getattr(self, sec)):
                key = f"{sec}.{f.name}"
                rows.append(
                    (
                        key,
                        getattr(getattr(self, sec), f.name),
                        self.sources.get(key, "default"),
                        KNOB_DOCS.get(key, ""),
                    )
                )
        return rows


#: The checkout this code was imported from: `<tree>/ddflow/config.py` -> `<tree>`. For an
#: installed ddflow it is site-packages, which is no project's root. The same answer as
#: `infra.paths.package_parent()`, recomputed because `config` is the bottom layer and may
#: import nothing (tests/test_layering.py); it sits directly under `ddflow/`, so the
#: level count cannot drift the way the layered modules' did.
_CODE_TREE = Path(__file__).resolve().parents[1]


def _is_code_tree(root: Path) -> bool:
    """Is `root` the very tree this code runs from -- not merely a parent of it?

    Equality, not a prefix: a harness tree NESTED under the primary
    (`.claude/worktrees/x`) has the primary as a prefix, and it is exactly the checkout
    whose older code meets the primary's newer config (bug B9cb7dd1c3b).
    """
    try:
        return root.resolve() == _CODE_TREE
    except OSError:
        return False


#: (root, key) already warned about in this process. `Config.load` runs several times per
#: command -- the CLI context, the store, the hook's checks -- and the same warning five
#: times over reads as five problems.
_WARNED: set[tuple[str, str]] = set()


def _warn_unknown(keys: list[str], root: Path) -> None:
    """Say, on stderr, which config keys this code skipped.

    Skipping without a word is the silent-knob-drop class: 81a52e3 made an unknown key
    load-and-skip so an older tree keeps working, but only `doctor` mentioned it, so
    every other command ran with the knob dropped and nobody was told. stderr, so
    `--json` output and MCP replies stay parseable.
    """
    new = [k for k in keys if (str(root), k) not in _WARNED]
    if not new:
        return
    _WARNED.update((str(root), k) for k in new)
    print(
        f"ddflow: warning: {root / '.ddflow'}/config.toml or local/config.toml sets "
        f"{', '.join(new)}, which this ddflow ({_CODE_TREE}) does not know; skipped (an "
        "unknown value of an enum knob takes the knob's strictest value, named above). The "
        "config is newer than this code: merge main into this tree (or, if it is a "
        "typo, fix it; `ddflow doctor` lists each).",
        file=sys.stderr,
    )


def _waivers_problem(v: Any) -> str:
    """`[enforce].trailer_waivers`: key -> a non-empty list of non-empty words. An empty
    list would refuse every use of its key while reading like a waiver."""
    shape = 'must be a table of trailer key -> list of words, e.g. { "Phase-ships" = ["none"] }'
    if not isinstance(v, dict):
        return shape
    for key, words in v.items():
        if not key or ":" in key or any(c.isspace() for c in key):
            # `"Phase-ships "` or `"Phase ships"` could never match a trailer git
            # parses (its token holds no whitespace): an inert waiver.
            return f"{key!r}: a trailer key must be non-empty, with no whitespace and no ':'"
        if not isinstance(words, list):
            return f"{key!r}: {shape}"
        if not words:
            return f"{key!r} has no words, which would refuse every use of the key; list them"
        if not all(isinstance(w, str) and w.strip() == w and w for w in words):
            return f"{key!r}: every word must be a non-empty string with no surrounding spaces"
    return ""


def _unit_interval(v: Any) -> str:
    """A similarity score bound: a number in [0, 1]. An int is fine (TOML `1`)."""
    ok = isinstance(v, int | float) and not isinstance(v, bool) and 0.0 <= v <= 1.0
    return "" if ok else "must be a number between 0 and 1"


#: Every ENUM knob and the values it may hold (bug Beea0744a7b). Each entry gets its check
#: in `_KNOB_CHECKS` from here, so a knob whose choices lived only in a comment
#: (`# block | warn | off`) can no longer take a typo that quietly behaves as some other
#: value. A new enum knob is declared here, not in a comment;
#: `tests/test_config_enum_knobs.py` finds any comment or knob doc listing `a | b` that
#: this table does not cover.
_BLOCK_WARN_OFF = ("block", "warn", "off")
PROGRESS_MODES = ("on", "phase", "off")
CI_ON_MERGE_MODES = ("off", "fast", "full")
KNOB_CHOICES: dict[str, tuple[str, ...]] = {
    "lease.reclaim_policy": ("report", "auto"),
    "worktree.merge_strategy": ("no-ff", "ff-only", "squash"),
    "flow.model": FLOW_MODELS,
    "flow.integration": FLOW_INTEGRATIONS,
    "flow.forge": FLOW_FORGES,
    "flow.claims": FLOW_CLAIMS,
    "flow.pr_merge": FLOW_PR_MERGE,
    "flow.on_changes_requested": FLOW_ON_CHANGES,
    "flow.port_strategy": FLOW_PORT_STRATEGIES,
    "gates.enforce_order": ("warn", "block", "off"),
    "lessons.search_backend": ("fts5", "like"),
    "session.progress_after_complete": PROGRESS_MODES,
    "schedule.ready_policy": ("deps_and_lease", "deps_only"),
    "schedule.cycle_policy": ("error", "warn"),
    "schedule.unknown_dep_policy": ("block", "warn"),
    "schedule.empty_phase": ("note", "problem", "off"),
    "dedupe.on_match": DEDUPE_ON_MATCH,
    "enforce.commit_without_lease": _BLOCK_WARN_OFF,
    "enforce.generated_views": _BLOCK_WARN_OFF,
    "enforce.stale_docs": _BLOCK_WARN_OFF,
    "enforce.environment_commits": _BLOCK_WARN_OFF,
    "enforce.stale_rules": _BLOCK_WARN_OFF,
    "enforce.readme_with_code": _BLOCK_WARN_OFF,
    "enforce.behind": _BLOCK_WARN_OFF,
    "loops.on_detect": ("warn", "block"),
    "review.on_exceed": ("refuse", "warn"),
    "upgrade.skew": UPGRADE_SKEW_POLICIES,
    "mcp.tools": MCP_TOOL_TIERS,
    "ci.on_merge": CI_ON_MERGE_MODES,
    "export.refresh": EXPORT_REFRESH_MODES,
}

#: The value each enum knob takes when a config FILE gives it one this code does not know
#: (decision D-enum-fallback-strict, superseding the fall-back-to-default part of
#: D9b8061fd38): its STRICTEST allowed value, so a typo in a deliberately tightened
#: setting can only make ddflow more careful, never quietly loosen it. For a knob with
#: no safety dimension the "strictest" is the value that does the most checking or
#: changes least; the reason is appended to each knob's doc below.
#: `tests/test_config_enum_knobs.py` requires an entry for every KNOB_CHOICES key.
KNOB_STRICTEST: dict[str, tuple[str, str]] = {
    "lease.reclaim_policy": ("report", "never steals a lease, so a crashed agent's work survives"),
    "worktree.merge_strategy": ("no-ff", "keeps every commit and a merge commit; rewrites nothing"),
    "flow.model": ("trunk", "no safety dimension; the plain model, which moves no branches"),
    "flow.integration": ("pr", "a merge waits for approval on the forge, not landing locally"),
    "flow.forge": ("auto", "no safety dimension; reads the forge from the remote URL"),
    "flow.claims": ("remote", "one clone wins a claim; an unreachable remote refuses it"),
    "flow.pr_merge": ("human", "ddflow never merges; a person does"),
    "flow.on_changes_requested": ("block", "the item is parked for a person"),
    "flow.port_strategy": ("forward-merge", "no safety dimension; the least bookkeeping"),
    "gates.enforce_order": ("block", "a gate recorded out of order is refused"),
    "lessons.search_backend": ("like", "no safety dimension; works on every SQLite build"),
    "session.progress_after_complete": ("on", "no safety dimension; reports the most"),
    "schedule.ready_policy": ("deps_and_lease", "an item another agent leased is not offered"),
    "schedule.cycle_policy": ("error", "a dependency cycle refuses scheduling"),
    "schedule.unknown_dep_policy": ("block", "a dependency on an unknown id stays unmet"),
    "schedule.empty_phase": ("problem", "an open phase with no task fails doctor"),
    "dedupe.on_match": ("ask", "a likely duplicate is refused until answered"),
    "enforce.commit_without_lease": ("block", "the hook refuses"),
    "enforce.generated_views": ("block", "the hook refuses"),
    "enforce.stale_docs": ("block", "the hook refuses"),
    "enforce.environment_commits": ("block", "the hook refuses"),
    "enforce.stale_rules": ("block", "the hook refuses"),
    "enforce.readme_with_code": ("block", "complete refuses"),
    "enforce.behind": ("block", "the hook refuses"),
    "loops.on_detect": ("block", "claim refuses an item that is looping"),
    "review.on_exceed": ("refuse", "a round past the budget is refused"),
    "upgrade.skew": ("refuse", "an older ddflow's write is refused"),
    "mcp.tools": ("all", "no safety dimension; every tool advertised, as without the knob"),
    "ci.on_merge": ("full", "the whole CI command runs after a merge"),
    "export.refresh": ("off", "no safety dimension; ddflow writes no document by itself"),
}

for _key, (_value, _why) in KNOB_STRICTEST.items():
    KNOB_DOCS[_key] = (
        f"{KNOB_DOCS[_key]} An unrecognised value in a config file is warned about, reported "
        f"by `doctor` and falls back to '{_value}', the strictest ({_why}); `config --set`, "
        "`ddflow_configure` and the environment refuse it."
    )
del _key, _value, _why


def strictest(key: str) -> str:
    """The value enum knob `key` takes when a config file gives it an unknown one."""
    return KNOB_STRICTEST[key][0]


#: Knobs whose VALUE set can grow in a later release (every enum, and `export.tables`, whose
#: sub-tables carry enums of their own), so a config FILE carrying a value this version
#: does not know is tolerated with a warning rather than refused -- an enum knob then
#: takes its strictest value (`KNOB_STRICTEST`); the write paths (`config --set`,
#: `ddflow_configure`) and the environment still refuse it.
_TOLERANT_VALUES = frozenset({*KNOB_CHOICES, "export.tables"})


def _one_of(allowed: tuple[str, ...]) -> Callable[[Any], str]:
    """The check for an enum knob: "" for a declared value, else the list it must be in."""
    return lambda v: "" if v in allowed else f"must be one of {', '.join(allowed)}"


def _export_tables_problem(v: Any) -> str:
    """`[export].tables` as one map: each value is a valid `[export.<doc>]` table (the
    same value rules the sub-table form gets; a key a newer release adds is tolerated)."""
    if not isinstance(v, dict):
        return "must be a table of [export.<doc>] tables"
    for doc, t in v.items():  # unknown keys are tolerated, as in a file's [export.<doc>]
        if why := _export_table_problem(str(doc), t):
            return why
    return ""


#: Knobs whose TYPE is not the whole contract: "" means valid, else why not. Checked on
#: load and by `Config.check`, so `config set` refuses the value instead of writing it.
#: `max_behind = 0` read as "never warn" would be a switch hidden in a threshold -- the
#: silent-knob-drop class -- when `behind = "off"` already says it plainly.
_VALUE_CHECKS: dict[str, Callable[[Any], str]] = {
    "export.tables": _export_tables_problem,
    "export.max_bytes": lambda v: (
        "" if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else "must be an integer >= 0"
    ),
    "export.documents": lambda v: (
        "" if isinstance(v, list) and all(isinstance(x, str) for x in v) else "must be a list of document names"
    ),
    # TOML arrives typed and `_coerce` passes it through untouched, so a string where a
    # list belongs (`hydrafusion = "openai"`) would iterate as letters: a set of nonsense
    # families that matches no reviewer, and so clears every one.
    "agent.routers": lambda v: (
        ""
        if isinstance(v, dict)
        and all(
            isinstance(m, list) and all(isinstance(x, str) for x in m) for m in v.values()
        )
        else 'must be a table of lists of family names, e.g. { hydrafusion = ["openai"] }'
    ),
    "enforce.max_behind": lambda v: (
        "" if isinstance(v, int) and not isinstance(v, bool) and v >= 1
        else 'must be an integer >= 1; to disable the check set [enforce].behind = "off"'
    ),
    "enforce.trailer_waivers": _waivers_problem,
    "dedupe.show_floor": _unit_interval,
    "dedupe.ask_threshold": _unit_interval,
    "dedupe.max_candidates": lambda v: (
        "" if isinstance(v, int) and not isinstance(v, bool) and v >= 1
        else "must be an integer >= 1; to stop the check set [dedupe].on_match = \"off\""
    ),
    "dedupe.min_words": lambda v: (
        "" if isinstance(v, int) and not isinstance(v, bool) and v >= 0
        else "must be an integer >= 0"
    ),
    "dedupe.kinds": lambda v: (
        "" if isinstance(v, list) and v and all(k in DEDUPE_KINDS for k in v)
        else f"must be a non-empty list drawn from {', '.join(DEDUPE_KINDS)}; "
        'to stop the check set [dedupe].on_match = "off"'
    ),
}  # fmt: skip

#: Every check: one derived from each KNOB_CHOICES entry, and the hand-written ones above.
#: The two never share a key (`tests/test_config_enum_knobs.py` asserts it), so neither can
#: silently shadow the other.
_KNOB_CHECKS: dict[str, Callable[[Any], str]] = {
    **{key: _one_of(allowed) for key, allowed in KNOB_CHOICES.items()},
    **_VALUE_CHECKS,
}


def csv_list(raw: str | None) -> list[str]:
    """`"a, b ,c"` -> `["a", "b", "c"]`. Empty entries dropped, whitespace stripped.

    One implementation. `cli._csv` and this module's list coercion were the same
    expression written twice, in the two places a user's comma-separated string enters
    the system — the CLI's `--needs a,b` and the env var `DDFLOW_GATES_REQUIRED=a,b`.
    Two parsers for one notation is two answers to "is `a,,b` two items or three".
    """
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def _maybe_json(raw: str, want: type) -> Any:
    """Parse ``raw`` as JSON if it looks like JSON of the wanted type, else None.

    Gives env vars a way to express values containing commas, without breaking the
    plain comma-separated form that is pleasanter for simple cases.
    """
    text = raw.strip()
    opener = "[" if want is list else "{"
    if not text.startswith(opener):
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"looks like JSON but does not parse: {exc}") from exc
    if not isinstance(parsed, want):
        raise ValueError(f"expected a JSON {want.__name__}, got {type(parsed).__name__}")
    return parsed


def _coerce(raw: Any, typ: Any) -> Any:
    """Coerce a TOML/env scalar into the field's declared type.

    Env vars arrive as strings; TOML arrives typed. ``list[str]`` from the env is
    comma-separated. A bad bool is an error rather than a silent False -- a silently
    dropped knob is the failure class this whole module is arranged against.
    """
    ts = typ if isinstance(typ, str) else getattr(typ, "__name__", str(typ))
    if not isinstance(raw, str):
        return raw
    if _outer_is_dict(ts):
        return _coerce_dict(raw, ts)
    if "bool" in ts:
        low = raw.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off"):
            return False
        raise ValueError(f"not a boolean: {raw!r}")
    if "int" in ts:
        return int(raw)
    if "float" in ts:
        return float(raw)
    if "list" in ts:
        # JSON first: comma-splitting tears any element that CONTAINS a comma, and the
        # default redaction patterns do -- `{16,}` is a regex quantifier. The split
        # produced two invalid regexes, so secrets stopped being redacted while the
        # config still looked set.
        parsed = _maybe_json(raw, list)
        if parsed is not None:
            # Refused, never `str()`-cast: `[null]` loaded as ["None"] (B7506c1124a).
            # Only where the elements are declared strings; a list of anything else
            # (`list[dict[str, str]]`) is returned as JSON gave it.
            if not _is_exactly(ts, "list[str]"):
                return parsed
            if bad := [x for x in parsed if not isinstance(x, str)]:
                raise ValueError(
                    f"expected a JSON list of strings; got {json.dumps(bad[0])} in {raw!r}"
                )
            return parsed
        if "," not in raw:
            return [raw.strip()] if raw.strip() else []
        return csv_list(raw)
    return raw


def _is_exactly(ts: str, want: str) -> bool:
    """Is the type spelling ``want`` itself, optionally wrapped as Optional or ``| None``?

    Not a substring test: `list[list[str]]` and `dict[str, dict[str, str]]` CONTAIN
    `list[str]` and `dict[str, str]`, and their elements are not strings (rubber duck,
    critic).
    """
    t = ts.replace(" ", "")
    w = want.replace(" ", "")
    return t in (w, f"Optional[{w}]", f"{w}|None", f"None|{w}")


def _outer_is_dict(ts: str) -> bool:
    """Is the OUTERMOST container in this type spelling a dict?

    `dict[str, list[str]]` and `list[dict[str, str]]` both contain both words, so
    neither a substring test nor branch order alone can tell them apart; `dict`,
    `<class 'dict'>` and `Optional[dict[...]]` do not start with "dict". Whichever
    word comes first is the outer container.
    """
    d, lst = ts.find("dict"), ts.find("list")
    return d >= 0 and (lst < 0 or d < lst)


def _coerce_dict(raw: str, ts: str) -> dict[str, Any]:
    """A dict knob from its env string: JSON, or `k=v,k=v` for string values.

    Checked by the declared type BEFORE `_coerce`'s list branch: `dict[str, list[str]]`
    contains "list", and was comma-split into a list of strings. A list-valued map has
    no comma notation that would not tear its words, so it takes JSON only.
    """
    if not raw.strip():
        return {}  # "" is an empty map, as it is an empty list for a list knob
    lists = (
        f'expected a JSON object of lists of strings, e.g. {{"Phase-ships": ["none"]}}; got {raw!r}'
    )
    parsed = _maybe_json(raw, dict)
    if parsed is not None:
        if "list" not in ts:
            # Refused, never `str()`-cast: `{"core": null}` loaded as {"core": "None"},
            # a family nobody declared, in the map reviewer independence reads
            # (B7506c1124a). Keys are strings already: JSON object keys always are.
            # Only where the values are declared strings (critic): a map of anything
            # else is returned as JSON gave it.
            if not _is_exactly(ts, "dict[str, str]"):
                return dict(parsed)
            if bad := [k for k, v in parsed.items() if not isinstance(v, str)]:
                raise ValueError(
                    f"expected a JSON object of strings; {bad[0]!r} is "
                    f"{json.dumps(parsed[bad[0]])} in {raw!r}"
                )
            return dict(parsed)
        # Refused HERE, not left to a per-knob check, and never `str()`-cast: a string
        # where the type says a list, or `null` where it says a word (read as "None"),
        # passed through as valid-looking strings (critic).
        if not all(
            isinstance(v, list) and all(isinstance(x, str) for x in v) for v in parsed.values()
        ):
            raise ValueError(lists)
        return {str(k): list(v) for k, v in parsed.items()}
    if "list" in ts:
        raise ValueError(lists)
    pairs = [p for p in raw.split(",") if p.strip()]
    bad = [p for p in pairs if "=" not in p]
    if bad:
        # Silently dropping a malformed pair weakens whatever reads the map -- for
        # `agent.families` that is the reviewer-independence check itself.
        raise ValueError(
            f"malformed dict entry {bad[0]!r}: expected key=value. "
            f"Use JSON for values containing commas or '='."
        )
    return dict(p.split("=", 1) for p in pairs)
