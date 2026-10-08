"""Scheduled jobs: their definitions, from every source, validated (decision D-sched-no-daemon).

A job is defined in one of three places, and every surface shows the MERGE:

* the event log (`schedule.defined` / `updated` / `removed`, folded into
  `State.schedules`) -- what `api.schedule` writes;
* `.ddflow/schedules/<id>.toml` -- one job per file, reviewed in git like any config;
* `[cadence]` -- the five count-based passes and `every_days`, which keep working
  exactly as before and are SHOWN as jobs (`count_due` still decides when they are due).

A later source in that list is shadowed by an earlier one with the same id: a recorded
definition beats a file, a file beats the `[cadence]` mapping. A removed job hides the
same id in the sources below it, which is what removing it means.

Due state and runs are not here: `cadence.ran` stays the run record, and when a job is
due is computed from the log by the due evaluator.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..config import Config
from ..core import clock
from ..core import fieldcheck as FC
from ..core import schedule as CS
from ..core.model import SCHEDULE_FIELDS, Schedule, State
from .tomldir import load_toml_dir

#: Where a project keeps its job files.
SCHEDULES_DIR = Path(".ddflow") / "schedules"
#: A job id is a plain TOML bare key (D-plain-keys): it names a file and may become a key.
ID_RE = re.compile(r"[A-Za-z0-9_-]+")
CADENCE_KEYS = ("every_days", "every_tasks", "every_phases")
MODES = ("report", "fix")
MISSED = ("skip", "once")
BUDGET_KEYS = ("max_items", "max_bugs", "max_turns")
#: The count-based `[cadence]` passes: name -> (knob, cadence key). The ONE table; `count_due`,
#: the job view and `rates.cadence_rates` all read it through `count_passes`.
COUNT_PASSES: tuple[tuple[str, str, str], ...] = (
    ("integration_tests", "integration_tests_every_tasks", "every_tasks"),
    ("dedupe_sweep", "dedupe_sweep_every_tasks", "every_tasks"),
    ("architecture_review", "architecture_review_every_phases", "every_phases"),
    ("mutation_tests", "mutation_tests_every_phases", "every_phases"),
    ("lessons_pass", "lessons_pass_every_phases", "every_phases"),
)
#: What a count pass counts, by its cadence key.
COUNT_UNITS = {"every_tasks": "tasks", "every_phases": "phases"}
#: `Defined.source` of a recorded definition and of a `[cadence]` pass; a file's is
#: "file:<path>".
SOURCE_LOG, SOURCE_CADENCE = "log", "cadence"


@dataclass
class Defined:
    """One job as the merge sees it: the definition, where it came from, and the
    lower-precedence sources with the same id that it shadows."""

    job: Schedule
    source: str
    shadows: list[str] = field(default_factory=list)

    def row(self) -> dict[str, Any]:
        d = asdict(self.job)
        d["source"] = self.source
        if self.shadows:
            d["shadows"] = list(self.shadows)
        return d


@dataclass
class Definitions:
    jobs: dict[str, Defined] = field(default_factory=dict)
    #: Problems that keep a source or a job out of `jobs`, or make the graph unusable:
    #: a malformed file, an unknown field, a needs cycle, a needs naming no job.
    errors: list[str] = field(default_factory=list)


# -- the count evaluator ---------------------------------------------------------------------


def done_counts(st: State) -> tuple[int, int]:
    """(completed tasks, completed phases): the work a count pass measures."""
    done = [i.kind for i in st.items.values() if i.state == "done"]
    return done.count("task"), done.count("phase")


@dataclass(frozen=True)
class CountPass:
    """A count pass as the evaluator reads it: its knob's value and what it counts."""

    name: str
    every: int
    unit: str  # "tasks" | "phases"
    count: int  # completed work of that unit


def count_every(cfg: Config) -> dict[str, int]:
    """Each count pass's period, from its `[cadence]` knob. Spelt out so the knobs stay
    readable by the dead-knob scan (`getattr(c, knob)` hid them from it)."""
    c = cfg.cadence
    return {
        "integration_tests": c.integration_tests_every_tasks,
        "dedupe_sweep": c.dedupe_sweep_every_tasks,
        "architecture_review": c.architecture_review_every_phases,
        "mutation_tests": c.mutation_tests_every_phases,
        "lessons_pass": c.lessons_pass_every_phases,
    }


def count_passes(st: State, cfg: Config) -> list[CountPass]:
    """Every count-based `[cadence]` pass with its period and the completions so far."""
    tasks, phases = done_counts(st)
    out = []
    for name, knob, key in COUNT_PASSES:
        unit = COUNT_UNITS[key]
        out.append(
            CountPass(name, getattr(cfg.cadence, knob), unit, tasks if unit == "tasks" else phases)
        )
    return out


def count_unit(name: str) -> str:
    """What the count pass `name` counts, "tasks" or "phases"; "" for a name that is not one."""
    return next((COUNT_UNITS[key] for n, _knob, key in COUNT_PASSES if n == name), "")


def count_at_last_run(st: State, name: str) -> int:
    """The completion count the LAST `cadence.ran` of `name` recorded. A malformed or
    absent `result` reads as 0 -- treating an unparseable record as "it never ran" errs
    toward reporting (bug Bb3a7d65b95: `ddflow cadence` raised on one)."""
    runs = st.cadences.get(name, [])
    try:
        return int(runs[-1].get("result", "0") or 0) if runs else 0
    except (TypeError, ValueError):
        return 0


def period_days(value: Any) -> float | None:
    """A calendar period in days, or None when ``value`` is not one: a number (not a bool)
    that is finite and above zero. The ONE test, for a `[cadence] every_days` entry and a
    schedule's `cadence.every_days` alike -- not `value <= 0`: nan and inf (also "1e309")
    pass that, and a period no elapsed time reaches is a pass that silently never falls
    due (bug B1c68fe5e9c)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


def calendar(cfg) -> dict[str, float]:
    """`[cadence] every_days` as name -> days. Raises ValueError naming the knob for an
    entry that is not `name=<positive number>`: one typo raised a bare float() error,
    and an entry without `=` was DROPPED -- a weekly pass never reported due, and
    nothing said the knob was ignored (roborev 830)."""
    out: dict[str, float] = {}
    for spec in cfg.cadence.every_days:
        name, sep, days = spec.partition("=")
        try:
            number: float = float(days) if sep and name.strip() else 0.0
        except ValueError:
            number = 0.0
        value = period_days(number)
        if value is None:
            raise ValueError(
                f"[cadence] every_days entry {spec!r} is not `name=days` with a positive "
                f'number of days (e.g. "bug_hunt=7")'
            )
        out[name.strip()] = value
    return out


# -- the calendar evaluator ------------------------------------------------------------------


def last_run_epoch(st: State, name: str) -> float:
    """When ``name`` last ran, 0.0 if it never did. The NEWEST run by its own timestamp, not
    the last in fold order: the log is ordered by Lamport clock, and two machines' runs can
    fold older-last (rubber-duck). An unreadable stamp reads as never."""
    return max(
        (clock.epoch(r["at"], naive="local") for r in st.cadences.get(name, [])), default=0.0
    )


def calendar_due(
    st: State, days: Mapping[str, float], now: float | None = None
) -> list[dict[str, Any]]:
    """Calendar passes (name -> period in days) not recorded as run within their period --
    or ever, so a weekly pass that has never run is due now rather than silently never."""
    now = time.time() if now is None else now
    due = []
    for name, period in days.items():
        last = last_run_epoch(st, name)
        age_days = (now - last) / 86400 if last else None
        if age_days is None or age_days >= period:
            due.append(
                {
                    "cadence": name,
                    "since": "never"
                    if age_days is None
                    else clock.fmt_age(now - last, "days", places=1),
                    "every": period,
                    "unit": "days",
                }
            )
    return due


# -- one definition ------------------------------------------------------------------------


_is_int = FC.is_int
_str_list = FC.str_list
_text = FC.text
_one_of = FC.one_of
_flag = FC.flag


def check_id(jid: str) -> str:
    """Why `jid` cannot name a job, or ''."""
    if not isinstance(jid, str) or not ID_RE.fullmatch(jid):
        return (
            f"job id {jid!r} must be letters, digits, '_' and '-' only (a plain TOML key, "
            f"D-plain-keys)"
        )
    return ""


def _needs(v: Any, errors: list[str]) -> list[str]:
    out = _str_list("needs", v, errors)
    errors += [f"needs: {bad}" for bad in map(check_id, out) if bad]
    return out


def _group(v: Any, errors: list[str]) -> Any:
    if not isinstance(v, str) or (v and not ID_RE.fullmatch(v)):
        errors.append(f"concurrency_group must be letters, digits, '_' or '-', got {v!r}")
        return None
    return v


def _jitter(v: Any, errors: list[str]) -> Any:
    if not _is_int(v) or v < 0:
        errors.append(f"jitter must be whole minutes, 0 or more, got {v!r}")
        return None
    return v


def normalize(spec: dict[str, Any], *, partial: bool = False) -> tuple[dict[str, Any], list[str]]:
    """(the fields of `spec`, typed; every problem with them). An unknown field is an
    error, not ignored: a misspelt `scope_glob` silently widening a job to the whole
    repository is the failure to avoid. `partial` checks only the fields present (an
    update); otherwise `cadence` is required."""
    errors: list[str] = []
    unknown = sorted(set(spec) - set(SCHEDULE_FIELDS))
    if unknown:
        errors.append(
            f"unknown field(s) {', '.join(unknown)}: a job has {', '.join(SCHEDULE_FIELDS)}"
        )
    if "cadence" not in spec and not partial:
        errors.append(f"cadence is required: one of {', '.join(CADENCE_KEYS)}")
    out = {name: _CHECKS[name](spec[name], errors) for name in SCHEDULE_FIELDS if name in spec}
    return out, errors


def _cadence(v: Any, errors: list[str]) -> dict[str, Any]:
    if not isinstance(v, dict) or len(v) != 1 or next(iter(v)) not in CADENCE_KEYS:
        errors.append(f"cadence must be exactly one of {', '.join(CADENCE_KEYS)}, got {v!r}")
        return {}
    key, n = next(iter(v.items()))
    if key == "every_days":
        if period_days(n) is None:
            errors.append(f"cadence.every_days must be a number of days above 0, got {n!r}")
            return {}
        return {key: n}
    if not _is_int(n) or n < 1:
        errors.append(f"cadence.{key} must be a whole number, 1 or more, got {n!r}")
        return {}
    return {key: n}


def _budget(v: Any, errors: list[str]) -> dict[str, int]:
    if not isinstance(v, dict):
        errors.append(f"budget must be a table of {', '.join(BUDGET_KEYS)}, got {v!r}")
        return {}
    out: dict[str, int] = {}
    for k, n in v.items():
        if k not in BUDGET_KEYS:
            errors.append(f"budget.{k} is not a budget: one of {', '.join(BUDGET_KEYS)}")
        elif not _is_int(n) or n < 0:
            errors.append(f"budget.{k} must be a whole number, 0 (no cap) or more, got {n!r}")
        else:
            out[k] = n
    return out


#: One checker per field: (value, errors) -> the typed value; it appends what is wrong.
_CHECKS = {
    "title": _text("title", "a string"),
    "cadence": _cadence,
    "needs": _needs,
    "scope_globs": lambda v, e: _str_list("scope_globs", v, e),
    "concurrency_group": _group,
    "prompt": _text("prompt", "a template id"),
    "mode": _one_of("mode", MODES),
    "budget": _budget,
    "escalate": _flag("escalate"),
    "missed": _one_of("missed", MISSED),
    "jitter": _jitter,
    "enabled": _flag("enabled"),
    "tags": lambda v, e: _str_list("tags", v, e),
}


def build(jid: str, spec: dict[str, Any]) -> tuple[Schedule | None, list[str]]:
    """A whole definition from `spec`, or None and why not."""
    errors = [e for e in [check_id(jid)] if e]
    fields, more = normalize(spec)
    errors += more
    if not errors and jid in fields.get("needs", []):
        errors.append(f"{jid} cannot need itself")
    if errors:
        return None, errors
    return Schedule(id=jid, **fields), []


# -- the sources ---------------------------------------------------------------------------


def from_cadence(cfg: Config) -> tuple[list[Schedule], list[str]]:
    """The `[cadence]` passes as jobs, read exactly as `ddflow cadence` reads them. A count
    pass set to 0 is shown disabled (`count_due` never fires it). An `every_days` entry
    REPLACES the count pass of its name, as `count_due` is told to -- but one malformed
    entry makes `ddflow cadence` refuse the whole list and leaves every count pass in
    force (`phase_overdue`), so no calendar job is shown then and the entry is reported."""
    every = count_every(cfg)
    errors: list[str] = []
    try:
        days = calendar(cfg)
    except ValueError as exc:
        errors.append(str(exc))
        days = {}
    count = [
        Schedule(
            id=name,
            title=f"{name} ([cadence].{knob})",
            cadence={unit: every[name]},
            enabled=every[name] > 0,
        )
        for name, knob, unit in COUNT_PASSES
        if name not in days
    ]
    cal = [
        Schedule(id=name, title=f"{name} ([cadence].every_days)", cadence={"every_days": n})
        for name, n in days.items()
    ]
    return count + cal, errors


def load_files(repo: Path) -> tuple[list[tuple[Schedule, str]], list[str]]:
    """Every `.ddflow/schedules/*.toml`, one job per file, id = the file name. A file
    that does not parse or validate is reported and left out; it never stops the rest."""
    loaded, errors = load_toml_dir(
        repo,
        SCHEDULES_DIR,
        build,
        lambda jid: f"id {jid!r} differs from the file name; a job file is <id>.toml",
    )
    return [(job, f"file:{rel}") for _, job, rel in loaded], errors


def definitions(repo: Path, cfg: Config, st: State) -> Definitions:
    """Every job, merged across the three sources, and every problem found doing it."""
    out = Definitions()
    layers: list[list[tuple[Schedule, str]]] = []
    logged = [(j, SOURCE_LOG) for j in st.schedules.values()]
    files, errs = load_files(repo)
    out.errors += errs
    cad, errs = from_cadence(cfg)
    out.errors += errs
    layers = [logged, files, [(j, SOURCE_CADENCE) for j in cad]]
    hidden: set[str] = set()
    for layer in layers:
        for job, source in layer:
            if job.id in out.jobs:
                out.jobs[job.id].shadows.append(source)
            elif job.id in hidden:
                continue
            elif job.removed:
                hidden.add(job.id)
            else:
                out.jobs[job.id] = Defined(job, source)
    out.errors += graph_errors({i: d.job for i, d in out.jobs.items()})
    return out


# -- the graph and the conflicts -----------------------------------------------------------


def graph_errors(jobs: dict[str, Schedule]) -> list[str]:
    """A `needs` naming no job, and every needs cycle (`core.schedule.find_cycles`)."""
    errors = [
        f"{j.id} needs {n}, which is not a job"
        for j in sorted(jobs.values(), key=lambda j: j.id)
        for n in j.needs
        if n not in jobs
    ]
    errors += [
        "needs cycle: " + " -> ".join(c)
        for c in CS.find_cycles(jobs, edges=lambda j: list(j.needs))  # type: ignore[arg-type]
    ]
    return errors


def concerns(error: str, jid: str) -> bool:
    """Is `error` (one of `Definitions.errors` / `graph_errors`) about job `jid`? By
    position, never by substring: `api` must not own `api-v2`'s problems."""
    if error.startswith(f"{jid} needs ") or f"{SCHEDULES_DIR.as_posix()}/{jid}.toml: " in error:
        return True
    if error.startswith("needs cycle: "):
        return jid in error.removeprefix("needs cycle: ").split(" -> ")
    return False


def conflicts(a: Schedule, b: Schedule, shared: list[str] | None = None) -> list[str]:
    """Why `a` and `b` may not run at the same time: an explicit shared concurrency
    group, or scope globs that overlap (`core.schedule.conflicts`, shared globs exempt).
    Empty: they may. A job with no scope declares nothing, so it overlaps nothing; a job
    that reads the whole tree and writes nowhere has no business holding a lock."""
    why = []
    if a.concurrency_group and a.concurrency_group == b.concurrency_group:
        why.append(f"concurrency group {a.concurrency_group}")
    why += [
        f"scope {x} overlaps {y}" for x, y in CS.conflicts(a.scope_globs, b.scope_globs, shared)
    ]
    return why


def conflicts_of(defs: Definitions, jid: str, cfg: Config) -> list[dict[str, Any]]:
    """Every other ENABLED job `jid` may not run beside, and why."""
    me = defs.jobs.get(jid)
    if me is None:
        return []
    shared = CS.shared_globs(cfg)
    out = []
    for other_id, other in sorted(defs.jobs.items()):
        if other_id == jid or not other.job.enabled:
            continue
        why = conflicts(me.job, other.job, shared)
        if why:
            out.append({"id": other_id, "why": why})
    return out


def search(defs: Definitions, query: str) -> list[Defined]:
    """Jobs whose id, title, tags, prompt, needs, scope or group contain EVERY word of
    `query` (case-insensitive), by id. An empty query matches nothing."""
    words = query.lower().split()
    if not words:
        return []
    hits = []
    for jid, d in sorted(defs.jobs.items()):
        j = d.job
        hay = " ".join(
            [jid, j.title, j.prompt, j.concurrency_group, j.mode, *j.tags, *j.needs, *j.scope_globs]
        ).lower()
        if all(w in hay for w in words):
            hits.append(d)
    return hits
