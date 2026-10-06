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
import functools
import json
import os
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

# Every [section] dataclass lives in `config_sections/`; each name is re-exported from here.
from .config_sections._docs import KNOB_DOCS, _doc  # noqa: F401
from .config_sections.agent import (  # noqa: F401
    FAMILY_HINTS,
    AgentConfig,
    family_for,
    router_set,
)
from .config_sections.bugs import (
    BugsConfig,
)
from .config_sections.cadence import (
    CadenceConfig,
)
from .config_sections.ci import (
    CiConfig,
)
from .config_sections.companions import (
    CompanionsConfig,
)
from .config_sections.dedupe import (
    DEDUPE_KINDS,
    DEDUPE_ON_MATCH,
    DedupeConfig,
)
from .config_sections.enforce import (
    EnforceConfig,
)
from .config_sections.export import (  # noqa: F401
    EXPORT_DEFAULT_TARGETS,
    EXPORT_MODES,
    EXPORT_REFRESH_MODES,
    EXPORT_TABLE_KEYS,
    ExportConfig,
    _export_table_problem,
)
from .config_sections.flow import (
    FLOW_CLAIMS,
    FLOW_FORGES,
    FLOW_INTEGRATIONS,
    FLOW_MODELS,
    FLOW_ON_CHANGES,
    FLOW_PORT_STRATEGIES,
    FLOW_PR_MERGE,
    FlowConfig,
)
from .config_sections.gates import (
    GatesConfig,
)
from .config_sections.ids import (  # noqa: F401
    ID_KINDS,
    ID_PREFIXES,
    ID_TOKENS,
    IdsConfig,
    id_problem,
    id_template_problem,
)
from .config_sections.imports import (
    ImportConfig,
)
from .config_sections.lease import (
    LeaseConfig,
)
from .config_sections.lessons import (
    LessonsConfig,
)
from .config_sections.log import (
    LogConfig,
)
from .config_sections.loops import (
    LoopsConfig,
)
from .config_sections.mcp import (
    MCP_TOOL_TIERS,
    McpConfig,
)
from .config_sections.memory import (
    MemoryConfig,
)
from .config_sections.prompts import (
    PromptsConfig,
)
from .config_sections.reinstruct import (
    ReinstructConfig,
)
from .config_sections.review import (
    ReviewConfig,
)
from .config_sections.rules import (
    RulesConfig,
)
from .config_sections.schedule import (  # noqa: F401
    FLOW_SIGNALS,
    SIGNAL_MARKS,
    ScheduleConfig,
    _number,
    _signals_problem,
    default_signals,
    merge_signals,
)
from .config_sections.session import (
    SessionConfig,
)
from .config_sections.upgrade import (
    UPGRADE_SKEW_POLICIES,
    UpgradeConfig,
)
from .config_sections.worktree import (
    WorktreeConfig,
)


class InvalidValue(ValueError):
    """A KNOWN knob given a value it cannot take. The write paths refuse it (exit 3,
    naming the key); an unknown key or section stays a plain ValueError (exit 1)."""


# Judged across sections, so it lives with `Config` rather than in `[schedule]`'s module.
def parallel_range_problems(cfg: Config) -> list[tuple[str, str]]:
    """`(key, why)` for each way the auto range is inconsistent: it must hold
    `max_parallel_min <= max_parallel_tasks <= max_parallel_max`. Judged on the merged
    configuration, and only in auto: in fixed mode the number is the number. The
    controller clamps an inconsistent range (`flowcontrol.Params.bounds`) rather than
    failing, so a project adopted with a larger explicit start keeps working."""
    s = cfg.schedule
    if s.parallel != "auto":
        return []
    out = []
    if s.max_parallel_min > s.max_parallel_tasks:
        out.append(
            (
                "schedule.max_parallel_min",
                f"the floor ({s.max_parallel_min}) is above the start value "
                f"schedule.max_parallel_tasks ({s.max_parallel_tasks})",
            )
        )
    if s.max_parallel_tasks > s.max_parallel_max:
        out.append(
            (
                "schedule.max_parallel_tasks",
                f"the start value ({s.max_parallel_tasks}) is above the ceiling "
                f"schedule.max_parallel_max ({s.max_parallel_max})",
            )
        )
    return out


#: `Config` fields that are bookkeeping, not `[section]`s.
_NOT_SECTIONS = ("sources", "unknown_knobs", "_fallback_notes", "_bad_values")


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
    ids: IdsConfig = field(default_factory=IdsConfig)

    #: where each knob's final value came from -- "default" | "file" | "local" | "env",
    #: or "<layer> (strictest fallback)" for an enum value a file layer had wrong
    sources: dict[str, str] = field(default_factory=dict, repr=False)
    #: What a config FILE carried that this code does not know: a key -- "sec.knob", or
    #: "[sec]" for a whole section -- skipped, not fatal; or an enum value, whose note says
    #: the strictest value was APPLIED instead, or which later layer overrode it
    #: (`fallback_entries` tells the two kinds apart). See `_apply`.
    unknown_knobs: list[str] = field(default_factory=list, repr=False)

    #: For each enum knob that fell back to its strictest value, the (index in
    #: unknown_knobs, "key = 'bad'") of each note, so a later layer's value rewrites the
    #: note from its parts, never by parsing text that holds the user's own value. A
    #: field, so `dataclasses.replace` carries it alongside `unknown_knobs` -- both
    #: shallowly, like `sources`: the copy shares them with the original.
    _fallback_notes: dict[str, list[tuple[int, str]]] = field(
        default_factory=dict, repr=False, compare=False
    )
    #: Indices in unknown_knobs of the OTHER entries for a known key with a value this
    #: code does not know (skipped, not replaced): `invalid_value_indices` reads them, so
    #: doctor never calls such a key unknown (Bf3566bbacd). Shared shallowly, as above.
    _bad_values: list[int] = field(default_factory=list, repr=False, compare=False)

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
            _warn_unknown(cfg.unknown_knobs, Path(root), cfg.fallback_entries())
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
        return [f.name for f in fields(self) if f.name not in _NOT_SECTIONS]

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
                value = _coerce_knob(sec, knob, raw, known[knob].type)
                if f"{sec}.{knob}" == "schedule.signals" and isinstance(value, dict):
                    # merged over the layers below, mark by mark (`merge_signals`)
                    value = merge_signals(getattr(target, knob), value)
                check = _KNOB_CHECKS.get(f"{sec}.{knob}")
                if check and (why := check(value)):
                    if lenient and f"{sec}.{knob}" in _TOLERANT_VALUES:
                        # A value this code does not know, in a file that may be NEWER
                        # than the code (a later release's tier): recorded like an unknown
                        # knob, so no command stops loading config -- and an enum knob
                        # takes its STRICTEST value, not its default, so a typo in a
                        # tightened setting fails closed (D-enum-fallback-strict).
                        if f"{sec}.{knob}" not in KNOB_STRICTEST:
                            self._bad_values.append(len(self.unknown_knobs))
                            self.unknown_knobs.append(f"{sec}.{knob} = {value!r}")
                            continue
                        bad, value = value, strictest(f"{sec}.{knob}")
                        # an earlier layer's note names what THIS layer wrote, not the
                        # fallback; each bad value stays its own (true) report
                        self._forget_fallback(
                            f"{sec}.{knob}",
                            f"the {source} value {bad!r}, itself unknown: "
                            f"{strictest(f'{sec}.{knob}')!r} is in effect",
                        )
                        head = f"{sec}.{knob} = {bad!r}"
                        self._fallback_notes.setdefault(f"{sec}.{knob}", []).append(
                            (len(self.unknown_knobs), head)
                        )
                        self.unknown_knobs.append(
                            f"{head} (not a value this ddflow knows; "
                            f"in effect: {value!r}, the strictest)"
                        )
                        setattr(target, knob, value)
                        self.sources[f"{sec}.{knob}"] = f"{source} (strictest fallback)"
                        continue
                    raise InvalidValue(f"invalid {sec}.{knob} = {value!r}: {why}")
                self._forget_fallback(f"{sec}.{knob}", f"the {source} value {value!r}")
                setattr(target, knob, value)
                self.sources[f"{sec}.{knob}"] = source

    def fallback_entries(self) -> set[str]:
        """The `unknown_knobs` entries that are strictest-fallback notes -- a value applied
        (the strictest, or a later layer's override, as the note says), never a skipped
        key -- read from the bookkeeping, never from the notes' text."""
        return {self.unknown_knobs[i] for notes in self._fallback_notes.values() for i, _ in notes}

    def invalid_value_indices(self) -> set[int]:
        """Which `unknown_knobs` entries are for a KNOWN key whose value is not known --
        the strictest-fallback notes and the skipped values -- by index, from the
        bookkeeping: never from the entries' text, which holds the user's own spelling
        (an unknown key may be spelled exactly like a value's note)."""
        return {i for notes in self._fallback_notes.values() for i, _ in notes} | set(
            self._bad_values
        )

    def _forget_fallback(self, key: str, by: str) -> None:
        """A later layer set `key`: an earlier layer's strictest-fallback note must stop
        claiming its value is in effect, or doctor reports `block` while `warn` runs.
        `by` names what overrode it -- and, when that is itself unknown, what runs."""
        for i, head in self._fallback_notes.get(key, []):
            self.unknown_knobs[i] = f"{head} (not a value this ddflow knows; overridden by {by})"

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
                    self._bad_values.append(len(self.unknown_knobs))
                    self.unknown_knobs.append(f"export.{k} ({why})")
                    continue
                raise ValueError(f"invalid [export.{k}]: {why}")
            self.export.tables[k] = {**self.export.tables.get(k, {}), **clean}
            self.sources["export.tables"] = source
        return plain

    def as_dict(self) -> dict[str, Any]:
        out = dataclasses.asdict(self)
        out.pop("sources", None)
        out.pop("_fallback_notes", None)
        out.pop("_bad_values", None)
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


def _warn_unknown(
    keys: list[str], root: Path, fallbacks: set[str] | frozenset[str] = frozenset()
) -> None:
    """Say, on stderr, which config keys this code skipped, and which unknown enum values
    it replaced (each note names the value in effect: the strictest, or a later layer's).

    Skipping without a word is the silent-knob-drop class: 81a52e3 made an unknown key
    load-and-skip so an older tree keeps working, but only `doctor` mentioned it, so
    every other command ran with the knob dropped and nobody was told. stderr, so
    `--json` output and MCP replies stay parseable.
    """
    new = [k for k in keys if (str(root), k) not in _WARNED]
    if not new:
        return
    _WARNED.update((str(root), k) for k in new)
    # An enum knob's unknown value is not skipped: it is APPLIED as the knob's strictest
    # value (D-enum-fallback-strict), and saying "skipped" would read as "no effect".
    fell_back = [k for k in new if k in fallbacks]
    skipped = [k for k in new if k not in fallbacks]
    what = []
    if skipped:
        what.append(f"{', '.join(skipped)}, which this ddflow does not know; skipped")
    if fell_back:
        what.append(f"{', '.join(fell_back)}; each such knob takes the value its note names")
    print(
        f"ddflow: warning: {root / '.ddflow'}/config.toml or local/config.toml sets "
        f"{'. It sets '.join(what)}. (This ddflow: {_CODE_TREE}.) The config is newer "
        "than this code: merge main into this tree (or, if it is a typo, fix it; "
        "`ddflow doctor` lists each).",
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
    "schedule.parallel": ("auto", "fixed"),
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
#: setting makes ddflow more careful. For a knob with no safety dimension the "strictest"
#: is the value that does the most checking or changes least; the reason is appended to
#: each knob's doc below. One rule outranks strictness: never a value that acts outside
#: this clone (`KNOB_OUTWARD`; D-fallback-no-remote). Where the two conflict -- `pr`
#: waits for approval but pushes, `remote` claims exclusively but writes remote refs --
#: the fallback stays local, and a typo there loosens approval or exclusivity until fixed.
#: `tests/test_config_enum_knobs.py` requires an entry for every KNOB_CHOICES key here
#: and in `KNOB_OUTWARD`, and that no fallback is an outward value.
KNOB_STRICTEST: dict[str, tuple[str, str]] = {
    "lease.reclaim_policy": ("report", "never steals a lease, so a crashed agent's work survives"),
    "worktree.merge_strategy": ("no-ff", "keeps every commit and a merge commit; rewrites nothing"),
    "flow.model": ("trunk", "no safety dimension; the plain model, which moves no branches"),
    # D-fallback-no-remote: a typo never makes ddflow push, open a pull request or write
    # remote refs; outward behaviour happens only when someone sets it correctly.
    "flow.integration": ("merge", "a typo never pushes or opens a pull request; lands locally"),
    "flow.forge": ("auto", "no safety dimension; reads the forge from the remote URL"),
    "flow.claims": (
        "local",
        "a typo never writes claim refs to the remote; claims stay in this clone",
    ),
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
    "schedule.parallel": (
        "fixed",
        "no safety dimension; changes least -- max_parallel_tasks is the number, nothing adapts",
    ),
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

#: Each enum knob's values that make ddflow act OUTSIDE this clone: a push, a pull
#: request, remote claim refs, a merge on the forge. No `KNOB_STRICTEST` fallback may be
#: one (D-fallback-no-remote): outward behaviour happens only when someone sets it
#: correctly, on purpose. Written out for EVERY KNOB_CHOICES key, empty when none, and
#: the test requires the two key sets to match: a new knob fails it until its author
#: answers the question here (a seeded default would answer it for them).
KNOB_OUTWARD: dict[str, frozenset[str]] = {
    "lease.reclaim_policy": frozenset(),
    "worktree.merge_strategy": frozenset(),
    "flow.model": frozenset(),
    "flow.integration": frozenset({"pr"}),  # pushes the branch, opens a pull request
    "flow.forge": frozenset(),
    "flow.claims": frozenset({"remote"}),  # writes refs/ddflow/claims/<id> on the remote
    "flow.pr_merge": frozenset({"on_approval", "auto"}),  # a merge on the forge
    "flow.on_changes_requested": frozenset(),
    "flow.port_strategy": frozenset(),
    "gates.enforce_order": frozenset(),
    "lessons.search_backend": frozenset(),
    "session.progress_after_complete": frozenset(),
    "schedule.ready_policy": frozenset(),
    "schedule.cycle_policy": frozenset(),
    "schedule.unknown_dep_policy": frozenset(),
    "schedule.empty_phase": frozenset(),
    "schedule.parallel": frozenset(),
    "dedupe.on_match": frozenset(),
    "enforce.commit_without_lease": frozenset(),
    "enforce.generated_views": frozenset(),
    "enforce.stale_docs": frozenset(),
    "enforce.environment_commits": frozenset(),
    "enforce.stale_rules": frozenset(),
    "enforce.readme_with_code": frozenset(),
    "enforce.behind": frozenset(),
    "loops.on_detect": frozenset(),
    "review.on_exceed": frozenset(),
    "upgrade.skew": frozenset(),
    "mcp.tools": frozenset(),
    "ci.on_merge": frozenset(),
    "export.refresh": frozenset(),
}

for _key, (_value, _why) in KNOB_STRICTEST.items():
    KNOB_DOCS[_key] = (
        f"{KNOB_DOCS[_key]} An unrecognised value in a config file is warned about, reported "
        f"by `doctor` and falls back to '{_value}', the strictest ({_why}); `config --set`, "
        "`ddflow_configure` and the environment refuse it, and so does loading ddflow's own "
        "source tree, where the file and the code are one commit."
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
_TOLERANT_VALUES = frozenset(
    {*KNOB_CHOICES, "export.tables", "bugs.phase", *(f"ids.{k}" for k in ID_KINDS)}
)


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


def _int_at_least(n: int) -> Callable[[Any], str]:
    return lambda v: (
        ""
        if isinstance(v, int) and not isinstance(v, bool) and v >= n
        else f"must be an integer >= {n}"
    )


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
    # [ids] templates and the one fixed id (the bugs phase): ids are file names, branch
    # names and glob tokens (D-id-schemes-final). Tolerated in a file (the default stays,
    # doctor names it), refused by the write paths.
    **{f"ids.{k}": functools.partial(id_template_problem, k) for k in ID_KINDS},
    "bugs.phase": id_problem,
    "schedule.max_parallel_tasks": _int_at_least(1),
    "schedule.max_parallel_min": _int_at_least(1),
    "schedule.max_parallel_max": _int_at_least(1),
    "schedule.adapt_up_after_s": _int_at_least(0),
    "schedule.adapt_cooldown_s": _int_at_least(0),
    "schedule.signal_interval_s": _int_at_least(1),
    "schedule.signals": _signals_problem,
    "worktree.max_parallel": lambda v: (
        "" if isinstance(v, int) and not isinstance(v, bool) and v >= 0
        else "must be an integer >= 0 (0 = follow the schedule limit)"
    ),
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


def _coerce_knob(sec: str, knob: str, raw: Any, typ: Any) -> Any:
    """`_coerce` for a KNOWN knob: a value of the wrong type is an `InvalidValue` naming
    the key, refused like any other invalid value (exit 3 on the write paths)."""
    try:
        return _coerce(raw, typ)
    except InvalidValue:
        raise
    except ValueError as exc:
        raise InvalidValue(f"invalid {sec}.{knob} = {raw!r}: {exc}") from exc


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
