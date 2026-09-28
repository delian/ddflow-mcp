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
import tomllib
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
    reclaim_policy: str = "report"  # report | auto


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
    "reclaim_policy",
    "'report' (default) never steals an expired lease — it names the worktree so a human can rescue in-flight work. 'auto' reclaims it. 'report' exists because a killed agent leaves FINISHED, uncommitted work behind more often than it leaves garbage.",
)


@dataclass
class WorktreeConfig:
    """Git worktree isolation for parallel agents."""

    enabled: bool = True
    root: str = "../.ddflow-worktrees"
    branch_prefix: str = "ddflow/"
    base_ref: str = ""  # "" = the repo's default branch, auto-detected
    merge_strategy: str = "no-ff"  # no-ff | ff-only | squash
    remove_on_merge: bool = True
    max_parallel: int = 4
    sync_before_start: bool = True
    adopt_existing: bool = True


_doc(
    "worktree",
    "enabled",
    "Whether tasks run in isolated git worktrees. Turn off only for a single-agent, single-task project; parallel agents in one tree destroy each other's work.",
)
_doc(
    "worktree",
    "root",
    "Where worktrees are created, relative to the repo root. Kept OUTSIDE the repo by default so the agent's own file globs and test collection never see sibling worktrees.",
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


@dataclass
class FlowConfig:
    """Branching model, pull-request integration and version tags (RESEARCH R16)."""

    model: str = "trunk"  # trunk | gitflow
    integration: str = "merge"  # merge | pr
    forge: str = "auto"  # auto | github | gitlab
    remote: str = "origin"
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
    "Let `ddflow next` run `pr sync` first when items wait in REVIEW, so merged work completes and requested changes come back without anyone remembering to ask. Costs one forge call per open request; a forge that cannot be reached is reported, never treated as 'nothing changed'.",
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
    "Ordered gate ids every PHASE passes through. 'tasks' is the fan-out point where member tasks run (in parallel where dependencies allow).",
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
    brief_max_tokens: int = 1200
    brief_lesson_count: int = 4
    replay_verify_diffs: bool = True


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
    "Path to the per-chunk review message template. Variables: intent, context, diff, chunk_index, chunk_total.",
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


_doc(
    "log",
    "reuse_parsed",
    "Re-use already-parsed events instead of re-parsing the whole log on every read. Sound because the log is APPEND-ONLY: a line that has been parsed can never change, so only the appended tail is new. Measured at 20k events: JSON parsing is 97ms of a 115ms read (84%), so this is where the time is. Set false to always re-read from scratch — slower, and the only reason to want it is a shard being rewritten in place under a running process, which `ddflow doctor` reports as a content-address mismatch anyway.",
)
_doc(
    "log",
    "max_cached_events",
    "Memory ceiling for reuse_parsed, in events. Measured at ~736 bytes per parsed event, so the 100,000 default holds ~74 MB in a long-lived MCP server. Above the ceiling the cache is not used and reads cost what they always did — graceful, not a failure. A project big enough to hit this wants an on-disk state snapshot rather than a bigger process.",
)


@dataclass
class LoopsConfig:
    """Runtime loop detection — work that repeats instead of finishing."""

    max_claims_per_item: int = 3
    max_gate_flaps: int = 4
    max_reopens: int = 2
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
class EnforceConfig:
    """Mechanical enforcement — the layer that does not rely on the agent agreeing."""

    commit_without_lease: str = "warn"  # block | warn | off
    install_hooks_on_setup: bool = True
    require_item_trailer: bool = False
    item_trailer_keys: list[str] = field(default_factory=lambda: ["Item"])
    generated_views: str = "block"  # block | warn | off
    stale_docs: str = "warn"  # block | warn | off
    doc_globs: list[str] = field(default_factory=lambda: ["**/*.md", "**/*.rst", "**/*.adoc"])
    doc_exclude: list[str] = field(
        default_factory=lambda: [
            "**/CHANGELOG*", "**/HISTORY*", "**/NEWS*", ".ddflow/**", "docs/ddflow/**",
        ]
    )  # fmt: skip


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
    "Require every commit to carry an `Item: <id>` git trailer (or another key from item_trailer_keys), checked by the commit-msg hook `ddflow hooks install` adds. Makes commits reconcilable against the queue by `git log --format='%(trailers:key=Item)'` instead of by parsing prose. Off by default because it is noisy on a repo with non-agent contributors.",
)
_doc(
    "enforce",
    "item_trailer_keys",
    'Trailer keys that satisfy require_item_trailer; any one of them will do. A project that has written `Phase: <id>` (or `Phase-ships: none` for a commit that ships no item) in every commit for months keeps its convention: set ["Phase", "Phase-ships"]. Checked by the commit-msg hook on the message being committed; merge commits are exempt.',
)
_doc(
    "enforce",
    "generated_views",
    "What the pre-commit hook does when a STAGED view generated by `ddflow render` differs from what the log regenerates now -- hand-edited, or stale because the queue moved after rendering. 'block' refuses, 'warn' prints and allows, 'off' disables. Default 'block': it fires only on a commit that includes a view, and the remedy is one command (`ddflow render`, then re-stage).",
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


@dataclass
class AgentConfig:
    """How ddflow talks to whichever agent is driving it."""

    id: str = ""  # "" = derive from hostname+pid
    reviewer_family_must_differ: bool = True
    families: dict[str, str] = field(default_factory=lambda: dict(FAMILY_HINTS))


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
    "Model-name substring to pretraining-family map, used to enforce the rule above. Extend it as new families appear; an unknown model is treated as its own family.",
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
    importer: ImportConfig = field(default_factory=ImportConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    companions: CompanionsConfig = field(default_factory=CompanionsConfig)
    cadence: CadenceConfig = field(default_factory=CadenceConfig)
    reinstruct: ReinstructConfig = field(default_factory=ReinstructConfig)
    enforce: EnforceConfig = field(default_factory=EnforceConfig)
    loops: LoopsConfig = field(default_factory=LoopsConfig)
    log: LogConfig = field(default_factory=LogConfig)
    prompts: PromptsConfig = field(default_factory=PromptsConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)

    #: where each knob's final value came from -- "default" | "file" | "env"
    sources: dict[str, str] = field(default_factory=dict, repr=False)

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
                cfg._apply(data, "file")

        envdata: dict[str, dict[str, Any]] = {}
        for sec in cfg._sections():
            for f in fields(getattr(cfg, sec)):
                key = f"DDFLOW_{sec.upper()}_{f.name.upper()}"
                if key in env:
                    envdata.setdefault(sec, {})[f.name] = env[key]
        if envdata:
            cfg._apply(envdata, "env")
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
        return [f.name for f in fields(self) if f.name != "sources"]

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

    def _apply(self, data: dict[str, Any], source: str) -> None:
        for sec, values in data.items():
            if sec in self._FOREIGN_TABLES:
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
            for knob, raw in values.items():
                if knob not in known:
                    raise ValueError(f"unknown knob '{sec}.{knob}'. Known: {sorted(known)}")
                setattr(target, knob, _coerce(raw, known[knob].type))
                self.sources[f"{sec}.{knob}"] = source

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
            return [str(x) for x in parsed]
        if "," not in raw:
            return [raw.strip()] if raw.strip() else []
        return csv_list(raw)
    if "dict" in ts:
        parsed = _maybe_json(raw, dict)
        if parsed is not None:
            return {str(k): str(v) for k, v in parsed.items()}
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
    return raw
