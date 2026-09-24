"""Configuration — every significant constant is a documented, overridable knob.

Resolution order (last wins):

1. the dataclass defaults below,
2. ``<repo>/.orchard/config.toml``,
3. environment variables ``ORCHARD_<SECTION>_<KNOB>`` (upper-case, e.g.
   ``ORCHARD_LEASE_TTL_S=900``).

Nothing in Orchard reads a bare literal for a policy decision; if you find one, it is
a bug.  ``orchard config --explain`` prints every knob with its value, its source and
its docstring, which is the discoverability contract this module exists to keep.
"""

from __future__ import annotations

import dataclasses
import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------------------
# Knob documentation lives beside the knob, in this dict, keyed "section.knob".
# `orchard config --explain` renders it.  A knob with no entry here fails a ratchet test
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
    root: str = "../.orchard-worktrees"
    branch_prefix: str = "orchard/"
    base_ref: str = ""  # "" = the repo's default branch, auto-detected
    merge_strategy: str = "no-ff"  # no-ff | ff-only | squash
    remove_on_merge: bool = True
    max_parallel: int = 4
    sync_before_start: bool = True


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
    "Prefix for auto-created task branches, so `git branch --list 'orchard/*'` enumerates exactly the machine-managed ones.",
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
    "sync_before_start",
    "Merge the base branch into the task branch before work starts. Prevents the 98-commits-behind-and-unmergeable failure that motivated this knob.",
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
    "required",
    "Gates whose failure BLOCKS completion. Everything else records its outcome and lets the pipeline continue — advisory vs blocking is an explicit field, never a convention.",
)
_doc(
    "gates",
    "unavailable_is_failure",
    "If true, a gate that could not run (tool missing, endpoint down) blocks like a failure. Default false, but UNAVAILABLE is always recorded distinctly and NEVER as a pass — that distinction is the point.",
)
_doc(
    "gates",
    "allow_skip_with_reason",
    "Permit `orchard gate skip <id> --reason '...'`. The reason is mandatory and is recorded in the event log, so a skip is auditable rather than invisible.",
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
            r"(?i)(api[-_ ]?key|token|secret|password|bearer)\s*[:=]\s*\S+",
            r"(?i)\b(gh[pousr]_[A-Za-z0-9]{16,})\b",
            r"(?i)\b(sk-[A-Za-z0-9]{16,})\b",
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
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
    "During `orchard replay --verify`, check that each recorded commit still exists and its diff still applies. Catches a log that has drifted from the tree it claims to describe.",
)


@dataclass
class ScheduleConfig:
    """Dependency resolution and parallel fan-out."""

    max_parallel_tasks: int = 4
    ready_policy: str = "deps_and_lease"  # deps_and_lease | deps_only
    cycle_policy: str = "error"  # error | warn
    unknown_dep_policy: str = "block"  # block | warn


_doc(
    "schedule",
    "max_parallel_tasks",
    "How many tasks the scheduler will offer as simultaneously runnable. Mirrors worktree.max_parallel; the lower of the two wins.",
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
class CadenceConfig:
    """Periodic whole-repo passes that a per-task gate structurally cannot do."""

    integration_tests_every_tasks: int = 5
    architecture_review_every_phases: int = 2
    mutation_tests_every_phases: int = 3
    dedupe_sweep_every_tasks: int = 4
    lessons_pass_every_phases: int = 4


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
class EnforceConfig:
    """Mechanical enforcement — the layer that does not rely on the agent agreeing."""

    commit_without_lease: str = "warn"  # block | warn | off
    install_hooks_on_setup: bool = True
    require_item_trailer: bool = False


_doc(
    "enforce",
    "commit_without_lease",
    "What the pre-commit hook does when a commit touches paths no live lease of yours covers. 'block' refuses (the only real enforcement Orchard has), 'warn' prints and allows, 'off' disables. Default 'warn' so adoption never breaks an existing repo on day one; switch to 'block' once the queue is populated.",
)
_doc(
    "enforce",
    "install_hooks_on_setup",
    "Install the pre-commit hook during `orchard adopt`. The hook is what makes the workflow enforced rather than merely described; disable only if your project manages hooks centrally.",
)
_doc(
    "enforce",
    "require_item_trailer",
    "Require every commit to carry an `Item: <id>` git trailer. Makes commits reconcilable against the queue by `git log --format='%(trailers:key=Item)'` instead of by parsing prose. Off by default because it is noisy on a repo with non-agent contributors.",
)


@dataclass
class AgentConfig:
    """How Orchard talks to whichever agent is driving it."""

    id: str = ""  # "" = derive from hostname+pid
    reviewer_family_must_differ: bool = True
    families: dict[str, str] = field(
        default_factory=lambda: {
            "claude": "anthropic",
            "gpt": "openai",
            "codex": "openai",
            "gemini": "google",
            "llama": "meta",
            "deepseek": "deepseek",
            "qwen": "alibaba",
            "mistral": "mistral",
            "grok": "xai",
        }
    )


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
    gates: GatesConfig = field(default_factory=GatesConfig)
    lessons: LessonsConfig = field(default_factory=LessonsConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    cadence: CadenceConfig = field(default_factory=CadenceConfig)
    enforce: EnforceConfig = field(default_factory=EnforceConfig)
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
            path = Path(root) / ".orchard" / "config.toml"
            if path.is_file():
                data = tomllib.loads(path.read_text("utf-8"))
                cfg._apply(data, "file")

        envdata: dict[str, dict[str, Any]] = {}
        for sec in cfg._sections():
            for f in fields(getattr(cfg, sec)):
                key = f"ORCHARD_{sec.upper()}_{f.name.upper()}"
                if key in env:
                    envdata.setdefault(sec, {})[f.name] = env[key]
        if envdata:
            cfg._apply(envdata, "env")
        return cfg

    def _sections(self) -> list[str]:
        return [f.name for f in fields(self) if f.name != "sources"]

    def _apply(self, data: dict[str, Any], source: str) -> None:
        for sec, values in data.items():
            if sec not in self._sections() or not isinstance(values, dict):
                continue
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
        return [p.strip() for p in raw.split(",") if p.strip()]
    if "dict" in ts:
        return dict(p.split("=", 1) for p in raw.split(",") if "=" in p)
    return raw
