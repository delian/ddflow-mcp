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
import json
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
    require_outcome: bool = True
    enforce_order: str = "warn"
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
class ImportConfig:
    """Adopting Orchard on a project that already has history: `[importer]`.

    Named for the module rather than the command because `import` is a keyword and a
    section called `[import_]` would be a TOML wart the operator has to remember.
    """

    max_tasks: int = 200
    preview_rows: int = 8


_doc(
    "importer",
    "max_tasks",
    "Refuse to propose more tasks than this in one import. An import writes events into a log that is committed to git, and five thousand of them is not recoverable by anything short of editing history; one real repository yielded 4,799. Raise it deliberately once you have looked at what it would write.",
)
_doc(
    "importer",
    "preview_rows",
    "How many items of each kind the import proposal prints before summarising the rest. The whole list is always in the --json output; this only caps the human-readable preview.",
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
class PromptsConfig:
    """Paths to prompt templates that replace the shipped ones.

    Empty means "use the project's `.orchard/prompts/<name>.md` if present, else the
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
    "Path to the instruction block the MCP server hands the agent on connect — the workflow, the reporting duties and the companion tools it should use. This is the file to edit to change how the project works. `orchard prompts eject mcp_instructions` writes an editable copy into .orchard/prompts/.",
)
_doc(
    "prompts",
    "review_system",
    "Path to the reviewer's system prompt template, replacing the shipped one. `orchard prompts eject` writes an editable copy into .orchard/prompts/ to start from.",
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
_doc("prompts", "session_brief_header", "Path to the header template for `orchard brief`.")


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
    "How many live items may declare exactly the same file globs before that is reported. Two items writing one file cannot run in parallel anyway, and one is usually a re-description of the other.",
)
_doc(
    "loops",
    "no_progress_window",
    "How many recent events with NO completion, gate pass or merge count as a stalled queue. Measured in events, not minutes, because an agent that is thinking produces no events and waiting is not looping.",
)
_doc(
    "loops",
    "on_detect",
    "'warn' reports loops in `doctor` and `next` and lets work continue; 'block' additionally makes `orchard claim` REFUSE an item that is already looping, which is the only thing that actually stops an agent spinning on it.",
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
    """How Orchard talks to whichever agent is driving it."""

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
    gates: GatesConfig = field(default_factory=GatesConfig)
    lessons: LessonsConfig = field(default_factory=LessonsConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    importer: ImportConfig = field(default_factory=ImportConfig)
    cadence: CadenceConfig = field(default_factory=CadenceConfig)
    enforce: EnforceConfig = field(default_factory=EnforceConfig)
    loops: LoopsConfig = field(default_factory=LoopsConfig)
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
    #: Tables in `.orchard/config.toml` that belong to another module, so this loader
    #: passes over them rather than rejecting them as unknown sections. Each has its own
    #: reader with its own dataclass, and each of those raises on a field it does not
    #: know — the ignorance here is about the TABLE, never about its contents.
    #:
    #: `companion` was missing, which is why companions could only be configured in
    #: their own file: putting a `[[companion]]` block in the obvious place made the
    #: whole config unreadable. The list and the readers must stay in step, and
    #: `tests/test_roborev_findings.py` asserts they do.
    _FOREIGN_TABLES = frozenset({"gate", "reviewer", "companion"})

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
    the system — the CLI's `--needs a,b` and the env var `ORCHARD_GATES_REQUIRED=a,b`.
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
