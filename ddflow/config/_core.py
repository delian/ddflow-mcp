"""The loader: `Config` and how a section/knob becomes its final value."""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from ..config_sections._docs import KNOB_DOCS, RENAMED
from ..config_sections._layers import layer_path, read_toml
from ..config_sections.agent import AgentConfig
from ..config_sections.approvals import ApprovalsConfig
from ..config_sections.bugs import BugsConfig
from ..config_sections.cadence import CadenceConfig
from ..config_sections.chore import ChoreConfig
from ..config_sections.ci import CiConfig
from ..config_sections.companions import CompanionsConfig
from ..config_sections.dedupe import DedupeConfig
from ..config_sections.digest import DigestConfig
from ..config_sections.docs import DocsConfig
from ..config_sections.enforce import EnforceConfig
from ..config_sections.evaluation import EvaluationConfig
from ..config_sections.export import ExportConfig, _export_table_problem, export_table_keys
from ..config_sections.flow import FlowConfig
from ..config_sections.gates import GatesConfig
from ..config_sections.govern import GovernConfig
from ..config_sections.grants import GrantsConfig
from ..config_sections.handoff import HandoffConfig
from ..config_sections.health import HealthConfig
from ..config_sections.hooks import HooksConfig
from ..config_sections.ids import IdsConfig
from ..config_sections.imports import ImportConfig
from ..config_sections.lanes import LanesConfig
from ..config_sections.lease import LeaseConfig
from ..config_sections.lessons import LessonsConfig
from ..config_sections.locks import LocksConfig
from ..config_sections.log import LogConfig
from ..config_sections.loops import LoopsConfig
from ..config_sections.mcp import McpConfig
from ..config_sections.memory import MemoryConfig
from ..config_sections.merge_queue import MergeQueueConfig
from ..config_sections.metrics import MetricsConfig
from ..config_sections.output import OutputConfig
from ..config_sections.pins import PinsConfig
from ..config_sections.plan import PlanConfig
from ..config_sections.prompts import PromptsConfig
from ..config_sections.quality import QualityConfig
from ..config_sections.quota import QuotaConfig
from ..config_sections.rag import RagConfig
from ..config_sections.reinstruct import ReinstructConfig
from ..config_sections.release import ReleaseConfig
from ..config_sections.review import ReviewConfig
from ..config_sections.rules import RulesConfig
from ..config_sections.schedule import SIGNALS, ScheduleConfig, merge_signals
from ..config_sections.self_assessment import SelfAssessmentConfig
from ..config_sections.sensitivity import SensitivityConfig
from ..config_sections.session import SessionConfig
from ..config_sections.swarm import SwarmConfig
from ..config_sections.triggers import TriggersConfig
from ..config_sections.upgrade import UpgradeConfig
from ..config_sections.waves import WavesConfig
from ..config_sections.worktree import WorktreeConfig
from ._coerce import _coerce_layer_knob
from ._compat import _migrate_renamed
from ._decls import _KNOB_CHECKS, _MEMBER_FILTERS, _STRICT_FALLBACK, _TOLERANT_VALUES, strictest
from ._errors import InvalidValue
from ._tree import _is_code_tree
from ._warn import _warn_unknown


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


#: The config FORMAT this code reads and writes: the optional top-level `format = N` of
#: `.ddflow/config.toml` (D-compat 2). Raised only when a section's SHAPE changes in a way
#: an older ddflow could not edit without losing something -- an added knob never raises it.
#: A file with a higher `format` is read (what this version knows is applied) but not
#: written (`format_problem`: exit 3, upgrade advice). A file with none is format 1.
CONFIG_FORMAT = 1

#: `Config` fields that are bookkeeping, not `[section]`s.
_NOT_SECTIONS = (
    "sources",
    "unknown_knobs",
    "ignored_members",
    "file_format",
    "_fallback_notes",
    "_bad_values",
    "root",
)


def _merge_budget_table(key: str, below: Any, value: Any) -> Any:
    """`[session.context_budget]` merges over the layers below key by key (per agent type); any
    other knob's value is returned as it is."""
    if key == "session.context_budget" and isinstance(value, dict):
        return {**below, **value}
    return value


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
    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)
    sensitivity: SensitivityConfig = field(default_factory=SensitivityConfig)
    companions: CompanionsConfig = field(default_factory=CompanionsConfig)
    cadence: CadenceConfig = field(default_factory=CadenceConfig)
    reinstruct: ReinstructConfig = field(default_factory=ReinstructConfig)
    enforce: EnforceConfig = field(default_factory=EnforceConfig)
    loops: LoopsConfig = field(default_factory=LoopsConfig)
    locks: LocksConfig = field(default_factory=LocksConfig)
    lanes: LanesConfig = field(default_factory=LanesConfig)
    health: HealthConfig = field(default_factory=HealthConfig)
    govern: GovernConfig = field(default_factory=GovernConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)
    swarm: SwarmConfig = field(default_factory=SwarmConfig)
    quota: QuotaConfig = field(default_factory=QuotaConfig)
    log: LogConfig = field(default_factory=LogConfig)
    upgrade: UpgradeConfig = field(default_factory=UpgradeConfig)
    pins: PinsConfig = field(default_factory=PinsConfig)
    plan: PlanConfig = field(default_factory=PlanConfig)
    quality: QualityConfig = field(default_factory=QualityConfig)
    waves: WavesConfig = field(default_factory=WavesConfig)
    merge_queue: MergeQueueConfig = field(default_factory=MergeQueueConfig)
    chore: ChoreConfig = field(default_factory=ChoreConfig)
    digest: DigestConfig = field(default_factory=DigestConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    grants: GrantsConfig = field(default_factory=GrantsConfig)
    approvals: ApprovalsConfig = field(default_factory=ApprovalsConfig)
    handoff: HandoffConfig = field(default_factory=HandoffConfig)
    hooks: HooksConfig = field(default_factory=HooksConfig)
    release: ReleaseConfig = field(default_factory=ReleaseConfig)
    mcp: McpConfig = field(default_factory=McpConfig)
    ci: CiConfig = field(default_factory=CiConfig)
    prompts: PromptsConfig = field(default_factory=PromptsConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    rules: RulesConfig = field(default_factory=RulesConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    ids: IdsConfig = field(default_factory=IdsConfig)
    triggers: TriggersConfig = field(default_factory=TriggersConfig)
    rag: RagConfig = field(default_factory=RagConfig)
    docs: DocsConfig = field(default_factory=DocsConfig)
    self_assessment: SelfAssessmentConfig = field(default_factory=SelfAssessmentConfig)

    #: where each knob's final value came from -- "default" | "file" | "local" | "env",
    #: or "<layer> (strictest fallback)" for an enum value a file layer had wrong
    sources: dict[str, str] = field(default_factory=dict, repr=False)
    #: What a config FILE carried that this code does not know: a key -- "sec.knob", or
    #: "[sec]" for a whole section -- skipped, not fatal; or an enum value, whose note says
    #: the strictest value was APPLIED instead, or which later layer overrode it
    #: (`fallback_entries` tells the two kinds apart). See `_apply`.
    unknown_knobs: list[str] = field(default_factory=list, repr=False)
    #: Members of a checked list or table knob that a config FILE carried and this code does
    #: not know (a newer release's `[dedupe].kinds` entry): ignored, the rest applied. One
    #: note each, for stderr and `doctor`; never a failure (D-compat 2).
    ignored_members: list[str] = field(default_factory=list, repr=False)
    #: The `format = N` a file layer declared (0: none did); `CONFIG_FORMAT` is what this
    #: code reads. A newer one is reported by `doctor` and refuses writes.
    file_format: int = field(default=0, repr=False)
    #: The project root this config was loaded for (None when loaded without one): where code
    #: that holds only a config finds the project's files, e.g. its document types.
    root: Path | None = field(default=None, repr=False, compare=False)

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
        cfg.root = Path(root) if root is not None else None
        env = os.environ if env is None else env
        for sec in cfg._sections():
            for f in fields(getattr(cfg, sec)):
                cfg.sources[f"{sec}.{f.name}"] = "default"

        if root is not None:
            path = layer_path(root, "file")
            if path.is_file():
                data = read_toml(path)
                # Lenient only when the code is ANOTHER tree's: there the file may be
                # newer than the code. In the tree the code came from, the two are one
                # commit, so an unknown key can only be a typo -- and skipping it (with
                # just a warning) was how 81a52e3 let a typo in the primary stand.
                cfg._apply(data, "file", lenient=not _is_code_tree(Path(root)))
            # The MACHINE-LOCAL layer, read last: .ddflow/local/ is git-ignored, so what
            # belongs to whoever runs this checkout -- their services, their machine's
            # sizing -- overrides the committed, generic config without ever reaching git.
            local = layer_path(root, "local")
            if local.is_file():
                cfg._apply(read_toml(local), "local")

        envdata: dict[str, dict[str, Any]] = {}
        for old in RENAMED:  # an old key's variable still works (D-compat); the new one wins
            osec, _, oknob = old.partition(".")
            if (key := f"DDFLOW_{osec.upper()}_{oknob.upper()}") in env:
                envdata.setdefault(osec, {})[oknob] = env[key]
        for sec in cfg._sections():
            for f in fields(getattr(cfg, sec)):
                key = f"DDFLOW_{sec.upper()}_{f.name.upper()}"
                if key in env:
                    envdata.setdefault(sec, {})[f.name] = env[key]
        if envdata:
            cfg._apply(envdata, "env")
        if root is not None:
            _warn_unknown(
                cfg.unknown_knobs, Path(root), cfg.fallback_entries(), cfg.ignored_members
            )
        return cfg

    @classmethod
    def check(cls, data: dict[str, Any], *, written: Iterable[str] | None = None) -> None:
        """Would this TOML load? Raises `ValueError` naming the first thing wrong.

        The semantic half of validation, which the write paths did not have. They
        parsed the merged text for SYNTAX and then validated the config already on
        DISK -- so `[gatez]`, or a knob nobody has ever heard of, sailed through and
        was written, and every later command failed to load the file. A writer that
        validates the state it is replacing has checked nothing.

        ``written`` names what the caller is WRITING (dotted keys such as
        ``lease.ttl_s``, or ``export.roadmap.mode``): those are judged strictly, an
        unknown one a typo. Everything else in ``data`` is the file's existing content,
        and is judged as `load` judges a file that may be newer than this code: a section,
        knob or enum value this version does not know passes through (the writer leaves it
        byte-identical), and a checked list's unknown member is ignored (D-compat 2). Without
        ``written`` every key counts as written.

        Applied to a throwaway instance so a rejected fragment cannot leave a
        half-updated Config behind.
        """
        if isinstance(written, str):  # one key, not its characters
            written = (written,)
        wrote = None if written is None else frozenset(written)
        cls()._apply(data, "check", written=wrote)

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

    def _apply(
        self,
        data: dict[str, Any],
        source: str,
        *,
        lenient: bool | None = None,
        written: frozenset[str] | None = None,
    ) -> None:
        # A key a config FILE carries that this code does not know is recorded and
        # skipped, never fatal. Several checkouts of one repository run different
        # versions of ddflow -- a worktree whose branch predates a knob runs its own,
        # older code against the primary's newer config -- and raising here refused every
        # command and every commit until the branch merged main, for a reason unrelated
        # to the work (bugs Bcfc0d22a09, B9cb7dd1c3b). A typo is still loud: `load`
        # names every entry on stderr, `doctor` reports each as a problem, the WRITE
        # paths (source "check") still refuse an unknown key, and `load` passes
        # lenient=False where the file and the code are the same tree's.
        #
        # `written` (the write paths' validation only) says which keys the caller is
        # WRITING: those stay strict, and every other key is judged as a file layer's
        # are, so an older ddflow can change a setting it knows in a file that also holds
        # a newer release's (B-uni-compat-config, D-compat 2).
        if lenient is None:
            lenient = source in ("file", "local")

        def soft(key: str) -> bool:
            """May `key` be skipped or tolerated rather than refused?"""
            if written is None:
                return lenient
            return key not in written and not any(w.startswith(key + ".") for w in written)

        data = _migrate_renamed(data, source)
        data = self._take_format(data, source, lenient)
        for sec, values in data.items():
            if sec in self._FOREIGN_TABLES:
                continue
            if sec not in self._sections() and soft(sec):
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
                self._apply_export_tables(values, soft, source) if sec == "export" else values
            )
            for knob, raw in knobs_only.items():
                key = f"{sec}.{knob}"
                if knob not in known and soft(key):
                    self.unknown_knobs.append(key)
                    continue
                if knob not in known:
                    raise ValueError(f"unknown knob '{sec}.{knob}'. Known: {sorted(known)}")
                self._apply_knob(sec, knob, raw, source, soft(key))

    def _take_format(self, data: dict[str, Any], source: str, lenient: bool) -> dict[str, Any]:
        """``data`` without its top-level `format = N`, which is not a section. A file layer
        declares the format it is written in; a non-integer or non-positive one is refused
        (a typo), and a NEWER one is remembered (`file_format`) for `format_problem` and
        `doctor`: its content is still read as far as this version understands it."""
        if "format" not in data:
            return data
        raw = data["format"]
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            if not lenient:
                raise InvalidValue(f"invalid format = {raw!r}: must be an integer >= 1")
            self._bad_values.append(len(self.unknown_knobs))
            self.unknown_knobs.append(f"format = {raw!r}")
            return {k: v for k, v in data.items() if k != "format"}
        if source in ("file", "local", "check"):
            self.file_format = max(self.file_format, raw)
        return {k: v for k, v in data.items() if k != "format"}

    def _apply_knob(self, sec: str, knob: str, raw: Any, source: str, lenient: bool) -> None:
        """Coerce, check and set one KNOWN knob from ``source``; ``lenient`` says a value
        this code does not know is tolerated (a file layer, or a key being kept as found)."""
        target = getattr(self, sec)
        key = f"{sec}.{knob}"
        typ = next(f.type for f in fields(target) if f.name == knob)
        value = _coerce_layer_knob(sec, knob, raw, typ, lenient)
        written = value  # what THIS layer says, before any merge: what a note names
        if key == SIGNALS and isinstance(value, dict):
            # merged over the layers below, mark by mark (`merge_signals`)
            value = merge_signals(getattr(target, knob), value)
        value = _merge_budget_table(key, getattr(target, knob), value)
        ignored: list[str] = []
        if lenient and key in _MEMBER_FILTERS:
            value, ignored = _MEMBER_FILTERS[key](value)
            self.ignored_members.extend(f"{key}: {what}" for what in ignored)
        check = _KNOB_CHECKS.get(key)
        if ignored and not value:
            # Emptied by the filter: a check that accepts an empty table would let it
            # replace the layer below, so this is "nothing usable left" whatever the check says.
            self.ignored_members.append(f"{key}: nothing usable left; the value below stays")
            return
        if check and (why := check(value)):
            if ignored:
                # Nothing usable is left of a list a newer release wrote: the layer below
                # keeps its value, and the note says so.
                self.ignored_members.append(f"{key}: nothing usable left; the value below stays")
                return
            if lenient and key in _TOLERANT_VALUES:
                # A value this code does not know, in a file that may be NEWER
                # than the code (a later release's tier): recorded like an unknown
                # knob, so no command stops loading config -- and an enum knob then
                # takes its STRICTEST value, not its default, so a typo in a
                # tightened setting fails closed (D-enum-fallback-strict).
                if key not in _STRICT_FALLBACK:
                    self._bad_values.append(len(self.unknown_knobs))
                    self.unknown_knobs.append(f"{key} = {value!r}")
                    return
                bad, value = written, strictest(key, getattr(target, knob))
                # an earlier layer's note names what THIS layer wrote, not the
                # fallback; each bad value stays its own (true) report
                self._forget_fallback(
                    key, f"the {source} value {bad!r}, itself unknown: {value!r} is in effect"
                )
                why = f" [{why}]" if key == SIGNALS else ""  # a table: why
                head = f"{key} = {bad!r}{why}"
                self._fallback_notes.setdefault(key, []).append((len(self.unknown_knobs), head))
                self.unknown_knobs.append(
                    f"{head} (not a value this ddflow knows; in effect: {value!r}, the strictest)"
                )
                setattr(target, knob, value)
                self.sources[key] = f"{source} (strictest fallback)"
                return
            raise InvalidValue(f"invalid {key} = {value!r}: {why}")
        self._forget_fallback(key, f"the {source} value {value!r}")
        setattr(target, knob, value)
        self.sources[key] = source

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
        self, values: dict[str, Any], soft: Callable[[str], bool], source: str
    ) -> dict[str, Any]:
        """Move the `[export.<doc>]` sub-tables into `export.tables`; return the plain knobs.

        A dict value under a name that is not an `[export]` knob is a document's table.
        Unknown keys inside one are skipped with a warning in a file (a newer release's
        key) and refused when written (`config --set`), like unknown knobs elsewhere;
        ``soft(key)`` says which of the two, per key.
        """
        knobs = {f.name for f in fields(self.export)}
        plain: dict[str, Any] = {}
        for k, v in values.items():
            if k in knobs or not isinstance(v, dict):
                plain[k] = v
                continue
            clean = {}
            for key, val in v.items():
                if key not in export_table_keys(k):
                    if soft(f"export.{k}.{key}"):
                        self.unknown_knobs.append(f"export.{k}.{key}")
                        continue
                    raise ValueError(
                        f"unknown key '{key}' in [export.{k}]. Known: {sorted(export_table_keys(k))}"
                    )
                clean[key] = val
            if why := _export_table_problem(k, clean):
                if soft(f"export.{k}"):  # a value a newer release defines: skipped, never fatal
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
        out.pop("ignored_members", None)
        out.pop("file_format", None)
        out.pop("_fallback_notes", None)
        out.pop("_bad_values", None)
        out.pop("root", None)
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
