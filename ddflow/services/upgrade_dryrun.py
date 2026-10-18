"""The dry-run upgrade: rehearse upgrading a project on a throwaway clone.

Decisions D-dry-run-from-running-version and D-upgrade-on-mcp-connect, task
B-upgrade.12-dry-run. Before an operator upgrades a project, this proves on a COPY that going
from the ddflow they run now to the one on the tree is smooth. Nothing here touches the
committed state of the project it rehearses (a failed run may leave a git-ignored transcript
under its `.ddflow/local/`), the ddflow installed on the machine or an MCP server that is running:

- the project is only READ, by `git clone --local` of what is COMMITTED at one pinned commit
  (tried once more if it fails), and its log, snapshot tags and backups are compared before and
  after, so the report can say the rehearsal left no trace. Other agents work in the live
  project meanwhile (B-dryrun-flake): each shard is read once, into memory, checked by its
  size and mtime; a snapshot tag is the rehearsal's only if signed with its run id, a backup
  only if made while it ran for the versions it upgraded; a shard rewritten under the run is
  the result "live repo changed during rehearsal" (the run is repeated once, and if the
  project moved again the check is `unavailable`, never a pass);
- a run that fails, one that could not run (a clone or a build that failed; not an old ddflow
  that was never offered) or one whose live project moved writes its step table and every
  command's bounded stdout and stderr to `.ddflow/local/dryrun/<stamp>-<run id>.json` (git-ignored;
  `--diag-dir` or `DDFLOW_DRYRUN_DIAG_DIR` choose another; a directory that cannot be written
  falls back to a new one in the system temp directory) and names the file under the table;
  a run that passes writes nothing;
- every command runs in a scratch directory with a private HOME, no `DDFLOW_*` variables and no
  network (`DDFLOW_NO_UPDATE_CHECK`), against a clone;
- the NEW ddflow is this source tree, run as a subprocess with PYTHONPATH; the OLD one is a
  command the caller names (an installed release, or one built from the clone at the commit
  the installed ddflow came from) or, for tests, this same code declaring an older version.
  Without an old one the steps that need it report `unavailable`, never a pass.

The rehearsal, in the order a report lists it (the numbers are the task's):

1. clone the project at the commit the installed ddflow was built from;
2. read it with the new code (status, board, doctor, brief, history, replay, show) and digest
   the state it folds into;
3. `upgrade --plan`: every pending change, repair and migration; an item the operator would be
   asked to confirm is a FINDING (the preview names it);
4. `upgrade --apply --backup snapshot`: the digest of the user's records is unchanged, doctor is
   clean, a second apply is a no-op, `--restore` brings the files back and the shards keep
   every original byte;
5. the OLD ddflow reads the upgraded copy, and its write is refused with the remedy or lands
   without disturbing a byte;
6. the connect flow (D-upgrade-on-mcp-connect): the new MCP server over stdio on the old
   project proposes the upgrade, applies NOTHING until consent, then applies on consent;
7. two agents claim, work and release a task with the new version;
8. shards two agents wrote with the old version merge (`merge=union`) and fold identically;
9. the old log folds through one registered event upcaster (the machinery has never met a real
   bump), and an older reader skips the new payload version.

Every check is a row: pass, fail, or unavailable (it could not run). The exit is 0 only when
every row passes, 1 when any fails, 2 when none failed but one could not run.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import secrets
import shlex
import shutil
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from ..config import Config
from ..core import clock
from ..core import digest as D
from ..core import outcome as O
from ..core import upcasters as U
from ..core import version as V
from ..core.events import Event, is_older
from ..core.model import State, fold
from ..infra import fsio
from ..infra import git as G
from ..infra import proc as P
from ..infra.log import EventLog
from . import upgrade_start as US
from .backups import SIDECAR_SUFFIX, SNAPSHOT_PREFIX

PASS = "pass"
FAIL = "fail"
UNAVAILABLE = "unavailable"

#: What `State` keeps that an upgrade is ALLOWED to change: its own bookkeeping, and the
#: definitions a migration moves into the log. Everything else is the user's records.
UPGRADE_OWNED = frozenset(
    {
        "event_count",
        "last_lamport",
        "ddflow_versions",
        "skew_overrides",
        "upgrades",
        "upgrade_runs",
        "repairs_applied",
        "older_version_events",
        "capabilities",
        "skipped_kinds",
        "fold_problems",
        "defs",
        "links",
        "definitions",
    }
)

#: The line the rehearsal writes by hand inside a stamped block.
HAND_EDIT = "DRYRUN-HAND-EDIT: a line an operator wrote"

#: The event kinds an apply writes: any of them new in the log means something was applied.
APPLYING = frozenset({"upgrade.applied", "repair.applied"})

#: The paths a restore does not bring back: the log only grows, the rest is machine-local.
KEPT = (".ddflow/events/", ".ddflow/local/", ".ddflow/backups/", ".ddflow/worktrees/", ".git/")

#: Commands read-only enough to run on the copy of a project (a derived index may be rebuilt).
READS: tuple[tuple[str, ...], ...] = (
    ("status", "--json"),
    ("board",),
    ("doctor",),
    ("brief",),
    ("history",),
    ("replay",),
)

#: Seconds one ddflow command, git call or server session may take.
TIMEOUT_S = 300.0

#: Where a failed run's step table and command transcript go: `--diag-dir` (`Options.diag_dir`),
#: else this variable, else `.ddflow/local/dryrun/` in the project (git-ignored, machine-local).
DIAG_ENV = "DDFLOW_DRYRUN_DIAG_DIR"
#: The characters kept of a command's stdout or stderr (half from each end); a command that
#: succeeded keeps a tenth of that.
STREAM_BOUND = 6000
#: The most commands a transcript keeps (the last ones; a full rehearsal runs a few hundred).
MAX_COMMANDS = 1000
#: The name of the result when the live project changed in a way the rehearsal cannot tell from
#: its own doing: it is `unavailable`, never a pass and never a fail.
LIVE_CHANGED = "live repo changed during rehearsal"
#: Seconds the rehearsal's MCP sessions are given for the server's start check
#: (`[upgrade].start_timeout_s`, 10 by default): past it the handshake proposes nothing, and the
#: plan of a big log takes 7-11 s under load. The rehearsal tests the flow, not that speed.
START_TIMEOUT_S = 120
#: What the handshake says when the start check ran out of its time bound.
START_TIMED_OUT = "did not finish within"
#: Seconds for the session that must take the timeout path: too little for any plan.
FORCED_TIMEOUT_S = 0.05
#: The pause before the one retry of a clone that failed (a gc or a ref update in the live repo).
CLONE_RETRY_S = 0.5

#: Run as `python -c`: the current code declaring an OLDER release, for tests and for a
#: machine with no old release to hand. It is a simulation, and the report says so.
_SIMULATED_OLD = (
    "import sys, ddflow\n"
    "ddflow.__version__ = sys.argv[1]\n"
    "from ddflow.surfaces.cli import main\n"
    "raise SystemExit(main(sys.argv[2:]))\n"
)


@dataclass
class Check:
    step: int
    name: str
    status: str
    detail: str = ""


@dataclass
class Cmd:
    """A ddflow to run: its argv prefix, the PYTHONPATH it needs ("" for an installed one)
    and how the report names it."""

    argv: list[str]
    pythonpath: str = ""
    label: str = ""


@dataclass
class Options:
    source: Path
    work: Path
    from_commit: str = "HEAD"
    new: Cmd | None = None
    old: Cmd | None = None
    max_show: int = 25
    keep: bool = False
    agents: tuple[str, str] = ("dry-a", "dry-b")
    #: The most bytes a compact `tools/list` may take; 0 reports the size and judges nothing.
    tools_budget: int = 0
    #: Build the old ddflow from the clone (a venv under the scratch directory) when none is given.
    build_old: bool = False
    #: Whether an item the operator would be asked to confirm fails the dry run (the default:
    #: it is the surprise the rehearsal exists to find) or is only named in the preview.
    asked_fails: bool = True
    reason: str = "dry run: the operator would accept this"
    #: Where a failed run writes its transcript; wins over `DDFLOW_DRYRUN_DIAG_DIR`, which wins over
    #: the default `<source>/.ddflow/local/dryrun`.
    diag_dir: Path | None = None


@dataclass
class Report:
    source: str
    from_commit: str
    new_label: str
    old_label: str
    started: str = ""
    checks: list[Check] = field(default_factory=list)
    preview: str = ""
    facts: dict[str, Any] = field(default_factory=dict)
    #: The file a failed run's step table and command transcript were written to; "" when none.
    diagnostics: str = ""

    @property
    def exit_code(self) -> int:
        if any(c.status == FAIL for c in self.checks):
            return 1
        if any(c.status == UNAVAILABLE for c in self.checks) or not self.checks:
            return 2
        return 0

    def table(self) -> str:
        width = max([len(c.name) for c in self.checks] + [4])
        rows = [f"{'step':>4}  {'check':<{width}}  result       detail"]
        for c in self.checks:
            rows.append(f"{c.step:>4}  {c.name:<{width}}  {c.status.upper():<11}  {c.detail}")
        verdict = {0: "PASS", 1: "FAIL", 2: "COULD NOT RUN"}[self.exit_code]
        rows.append(f"dry run {verdict}: {len(self.checks)} check(s), from {self.from_commit[:10]}")
        if self.diagnostics:
            rows.append(f"diagnostics (every step's stdout and stderr): {self.diagnostics}")
        return "\n".join(rows)

    def markdown(self) -> str:
        lines = [
            "# Upgrade dry run",
            "",
            f"Generated {self.started}. Rehearsed on a clone of `{self.source}` at "
            f"`{self.from_commit[:12]}`; new ddflow: {self.new_label}; old ddflow: "
            f"{self.old_label or 'none'}. Nothing live was touched.",
            "",
            "| step | check | result | detail |",
            "|---|---|---|---|",
        ]
        if self.diagnostics:
            lines[2:2] = [f"Every command's output: `{self.diagnostics}`.", ""]
        for c in self.checks:
            lines.append(f"| {c.step} | {c.name} | {c.status} | {c.detail.replace('|', '/')} |")
        if self.preview:
            lines += ["", "## Upgrade preview", "", "```", self.preview.rstrip(), "```"]
        return "\n".join(lines) + "\n"

    def as_data(self) -> dict[str, Any]:
        return {
            "exit": self.exit_code,
            "source": self.source,
            "from": self.from_commit,
            "new": self.new_label,
            "old": self.old_label,
            "checks": [asdict(c) for c in self.checks],
            "facts": self.facts,
            "diagnostics": self.diagnostics,
        }


class CouldNotRun(Exception):
    """The rehearsal cannot start (no git, a bad commit, a work directory inside the project)."""


# -- helpers that read, never write --------------------------------------------------------


def source_root() -> str:
    """The directory holding the `ddflow` package that is running: the NEW code's PYTHONPATH."""
    return str(Path(__file__).resolve().parents[2])


def new_cmd() -> Cmd:
    return Cmd([sys.executable, "-m", "ddflow"], source_root(), "this source tree")


def simulated_old(version: str) -> Cmd:
    """The current code declaring ``version``: an older reader/writer for tests (a simulation)."""
    return Cmd(
        [sys.executable, "-c", _SIMULATED_OLD, version],
        source_root(),
        f"simulated {version} (this code, declaring that version)",
    )


def release_commit(source: Path, version: str) -> str:
    """The commit that released ``version``: its tag, else the `release X` commit; "" if none."""
    tag = G.run(source, "rev-parse", "--verify", "--quiet", f"v{version}^{{commit}}")
    if tag.ok and tag.out:
        return tag.out
    found = G.run(
        source, "log", "--format=%H", "--fixed-strings", f"--grep=release {version}", "-1"
    )
    return found.out if found.ok else ""


def previous_release(source: Path, current: str) -> str:
    """The newest `v*` tag below ``current``, as a commit; "" when there is none."""
    tags = G.run(source, "tag", "--list", "v*", "--sort=-v:refname")
    for tag in tags.out.splitlines() if tags.ok else []:
        version = tag.removeprefix("v")
        if is_older(version, current):
            done = G.run(source, "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}")
            return done.out if done.ok else ""
    return ""


@dataclass
class Live:
    """The parts of the LIVE project a rehearsal could disturb, as they were when read."""

    shards: dict[str, bytes]
    tags: list[str]
    backups: list[str]
    #: Who tagged each snapshot tag ("" for a lightweight one): the rehearsal signs its own.
    tag_owners: dict[str, str] = field(default_factory=dict)
    #: Shards that kept changing while they were read (a writer was at them).
    unstable: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Footprint:
    """What identifies the rehearsal's own traces in the live project: the run id its git
    commits and tags are signed with, the window it ran in, and the `<from>-to-<to>` of the
    backups its clones made."""

    run_id: str
    started: str
    ended: str
    versions: frozenset[str] = frozenset()

    @property
    def tagger(self) -> str:
        return f"dry-run-{self.run_id}"


def snapshot_live(source: Path) -> Live:
    """What to compare after the run. Other agents may be working in the live project, so its
    log may GROW meanwhile; what must not happen is that it changes under us, or that anything
    of the rehearsal (an agent named `dry-*`, a snapshot tag signed with its run id, a backup
    of its versions made while it ran) shows up in it. Each shard is read once, into memory."""
    shards, unstable = shard_bytes_checked(source)
    listed = G.run(
        source,
        "tag",
        "--list",
        "--format=%(refname:short)\t%(taggername)",
        "ddflow-upgrade-snapshot/*",
    )
    owners: dict[str, str] = {}
    for line in listed.out.splitlines() if listed.ok else []:
        name, _, tagger = line.partition("\t")
        owners[name] = tagger.strip()
    backups = Path(source) / ".ddflow" / "backups"
    return Live(
        shards,
        sorted(owners),
        sorted(p.name for p in backups.iterdir()) if backups.is_dir() else [],
        owners,
        unstable,
    )


def backup_versions(name: str) -> str:
    """`<from>-to-<to>` of a backup or snapshot name `<stamp>-<from>-to-<to>`."""
    return name.partition("-")[2].removesuffix(SIDECAR_SUFFIX)


def live_leaks(before: Live, after: Live, mine: Footprint | None = None) -> list[str]:
    """What of the REHEARSAL is certainly in the live project: a shard of an agent it ran as, a
    snapshot tag signed with its run id. Another agent's tag is not that (``mine`` None: no tag
    can be told to be the rehearsal's). A backup carries no run id: see `live_moved`."""
    out = [
        f"the live log has a shard of the rehearsal: {n}"
        for n in after.shards
        if n.startswith("dry-")
    ]
    if mine is not None:
        out += [
            f"a snapshot tag of the rehearsal appeared in the live project: {t}"
            for t in sorted(set(after.tags) - set(before.tags))
            if after.tag_owners.get(t) == mine.tagger
        ]
    return out


def live_moved(before: Live, after: Live, mine: Footprint | None = None) -> list[str]:
    """How the live project changed in a way that is not proof of a leak: a shard rewritten or
    removed, one still being written while it was read (before or after: a torn read of either
    could pass for a change), or a backup made while the rehearsal ran for the versions it
    upgraded. A backup carries no run id, so unlike a tag it cannot be told from another agent's
    upgrade of the same versions: it is "the project moved", never a pass and never a certain
    leak. `live_leaks` has what is certain."""
    out = [
        f"the live shard {n} changed under the run"
        for n in prefixes_kept(before.shards, after.shards)
    ]
    out += [
        f"the live shard {n} was still being written when read"
        for n in sorted({*before.unstable, *after.unstable})
    ]
    if mine is not None:
        out += [
            f"a backup appeared in the live project while the rehearsal ran, for its versions "
            f"(another agent's upgrade, or a leak): {b}"
            for b in sorted(set(after.backups) - set(before.backups))
            if mine.started <= b.partition("-")[0] <= mine.ended
            and backup_versions(b) in mine.versions
        ]
    return out


def folded_state(repo: Path) -> State:
    """What a project's log folds into with the running code, read as the project configures
    its log (`[log]`); non-strict, so an event this code does not know is counted, not fatal."""
    log = EventLog(repo, "dry-digest", log_cfg=Config.load(repo).log)
    return fold(log.read_all(), strict=False)


def state_digests(repo: Path) -> dict[str, str]:
    """One digest per part of the state the log folds into (`State` field by field), so a
    difference names where it is."""
    st = folded_state(repo)
    return {name: D.of_obj(value, size=8) for name, value in _fields(st).items()}


def _fields(st: State) -> dict[str, Any]:
    return json.loads(json.dumps(asdict(st), default=str))


def users_part(digests: dict[str, str]) -> dict[str, str]:
    """The digests of what is the user's: not the upgrade's own bookkeeping."""
    return {k: v for k, v in digests.items() if k not in UPGRADE_OWNED}


def tree_digest(repo: Path, kept: Iterable[str] = KEPT) -> str:
    """A digest of every file the working tree holds (tracked or not, ignored ones excluded)
    except those under ``kept``: what a restore must bring back byte for byte."""
    listed = G.paths(repo, "ls-files", "--cached", "--others", "--exclude-standard")
    rows: list[str] = []
    for name in sorted(listed or []):
        if name.startswith(tuple(kept)):
            continue
        path = Path(repo) / name
        if path.is_file():
            rows.append(f"{name} {D.hasher(path.read_bytes()).hexdigest()}")
    return D.of_obj(rows)


def read_stable(path: Path, attempts: int = 3) -> tuple[bytes | None, bool]:
    """``path`` read once into memory, checked by its size and mtime before and after (a writer
    at it makes them differ, and it is read again): (bytes, stable). None when it is gone."""
    data = b""
    for _ in range(attempts):
        try:
            first = path.stat()
            data = path.read_bytes()
            last = path.stat()
        except FileNotFoundError:
            return None, True
        if (first.st_size, first.st_mtime_ns) == (last.st_size, last.st_mtime_ns) and len(
            data
        ) == last.st_size:
            return data, True
    return data, False


def shard_bytes_checked(repo: Path) -> tuple[dict[str, bytes], list[str]]:
    """Every event shard of ``repo`` as bytes, and the names of those that never held still."""
    root = Path(repo) / ".ddflow" / "events"
    out: dict[str, bytes] = {}
    unstable: list[str] = []
    for p in sorted(root.glob("*.jsonl")) if root.is_dir() else []:
        data, stable = read_stable(p)
        if data is None:  # removed since the listing: another agent's doing, not a failure
            continue
        out[p.name] = data
        if not stable:
            unstable.append(p.name)
    return out, unstable


def shard_bytes(repo: Path) -> dict[str, bytes]:
    return shard_bytes_checked(repo)[0]


def prefixes_kept(before: dict[str, bytes], after: dict[str, bytes]) -> list[str]:
    """The shards whose original bytes are NOT the start of what is there now."""
    return [n for n, raw in before.items() if not after.get(n, b"").startswith(raw)]


def events_of(repo: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in shard_bytes(repo).values():
        for line in raw.decode("utf-8", "replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


@contextlib.contextmanager
def registered_upcaster(kind: str, step: U.Upcaster) -> Iterator[int]:
    """Register ``step`` as the upcaster from ``kind``'s current payload version to the next,
    for the length of the block, and restore the registries after: the one place a real
    bump of a payload can be rehearsed before a release has one. Yields the new version."""
    version = U.current_version(kind)
    before = (dict(U.PAYLOAD_VERSIONS), dict(U.UPCASTERS))
    U.PAYLOAD_VERSIONS[kind] = version + 1
    U.UPCASTERS[(kind, version)] = step
    try:
        yield version + 1
    finally:
        U.PAYLOAD_VERSIONS.clear()
        U.PAYLOAD_VERSIONS.update(before[0])
        U.UPCASTERS.clear()
        U.UPCASTERS.update(before[1])


def _records(st: State) -> dict[str, list[str]]:
    """The ids of the records a state holds, by kind of record: what an upcaster must keep."""
    return {
        name: sorted(getattr(st, name))
        for name in ("items", "bugs", "lessons", "research", "decisions", "memories", "sessions")
    }


def _stripped(data: Mapping[str, Any]) -> dict[str, Any]:
    """A payload without the keys an upcast adds (the version and the step's own)."""
    return {k: v for k, v in data.items() if k not in (U.VERSION_KEY, "origin")}


def _of(events: list[Event], kind: str) -> list[Event]:
    return [e for e in events if e.kind == kind]


def upcast_problems(repo: Path) -> tuple[str, list[str]]:
    """Fold a project's old log with ONE registered upcaster: `(the kind, what went wrong)`.

    The event-evolution machinery has never met a real bump (`core.upcasters` ships empty), so
    the first real upcaster would be its first production run. This is that run, on the log
    being rehearsed: the most common kind is bumped by a step that only adds a key, so the
    handlers (which ignore it) must fold the same records (ids per collection: a handler that
    keeps a whole payload keeps the added key too, which is not a defect), every event of the kind must go
    through the step with its id kept, and an older reader (no bump) must skip a payload
    stamped with the new version instead of guessing at it."""
    log = EventLog(repo, "dry-digest", log_cfg=Config.load(repo).log)
    events = log.read_all()
    if not events:
        return "", ["the log holds no events"]
    kinds = Counter(e.kind for e in events)
    kind, wanted = kinds.most_common(1)[0]
    ids = {e.id for e in events if e.kind == kind}
    seen: list[str] = []

    def step(k: str, data: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        seen.append(k)
        return k, {**data, "origin": "upcast by the dry run"}

    problems: list[str] = []
    before = _records(fold(events, strict=False))
    with registered_upcaster(kind, step) as version:
        try:
            state = fold(events, strict=True)
        except (ValueError, KeyError, TypeError) as exc:
            return kind, [f"the fold with an upcaster for {kind} failed: {exc}"]
        if len(seen) != wanted:
            problems.append(f"{len(seen)} of {wanted} {kind} events went through the upcaster")
        if _records(state) != before:
            problems.append("the records the log folds into changed with the upcaster")
        moved = [U.upcast(e) for e in events if e.kind == kind]
        if {e.id for e in moved} != ids:
            problems.append("an upcast event lost its id")
        if any(
            _stripped(m.data) != _stripped(e.data)
            for m, e in zip(moved, _of(events, kind), strict=True)
        ):
            problems.append("the upcaster changed more than the key it adds")
        newer = replace(next(e for e in events if e.kind == kind), data={U.VERSION_KEY: version})
    skipped = fold([newer], strict=False).skipped_kinds.get(f"{kind} (payload v{version})")
    if skipped != 1:
        problems.append(f"an older reader did not skip a {kind} payload of version {version}")
    return kind, problems


# -- the rehearsal --------------------------------------------------------------------------


class Rehearsal:
    """One dry run: the scratch directories, the commands, the checks collected so far."""

    def __init__(self, opt: Options) -> None:
        self.opt = opt
        self.new = opt.new or new_cmd()
        self.report = Report(
            str(opt.source), opt.from_commit, self.new.label or "", opt.old.label if opt.old else ""
        )
        self.home = opt.work / "home"
        self.pristine = opt.work / "pristine"
        #: The keys the plan said the operator would be asked to confirm (set by `step_plan`).
        self.confirms: list[str] = []
        #: Signs this rehearsal's git commits and tags, so its traces in the live project can
        #: be told from other agents' (a snapshot tag carries it as its tagger).
        self.run_id = secrets.token_hex(4)
        self.started_stamp = clock.compact_at()
        #: Every command the rehearsal ran, with its bounded output: the transcript a failure
        #: is explained from.
        self.commands: list[dict[str, Any]] = []
        #: `"decision D-x"`-style names of the records the plan said are CONTESTED (set by
        #: `step_plan`): the operator's to resolve, so doctor failing on them alone is expected.
        self.contested: set[str] = set()

    # recording

    def add(self, step: int, name: str, ok: bool | None, detail: str = "") -> bool:
        status = UNAVAILABLE if ok is None else (PASS if ok else FAIL)
        self.report.checks.append(Check(step, name, status, detail))
        return bool(ok)

    def unrunnable(self, name: str) -> None:
        """Note that a step could not run (as opposed to an old ddflow that was not offered):
        a run with one leaves its transcript."""
        self.report.facts.setdefault("could_not_run", []).append(name)

    def guard(self, step: int, name: str, fn: Callable[[], Any]) -> Any:
        """Run one step; whatever it raises becomes a failed row, never a crash."""
        try:
            return fn()
        except CouldNotRun as exc:
            self.unrunnable(name)
            self.add(step, name, None, str(exc))
        except Exception as exc:  # the report must come out whole
            self.add(step, name, False, f"{type(exc).__name__}: {exc}")
        return None

    # running commands

    def env(self, cmd: Cmd) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(self.home),
            "XDG_STATE_HOME": str(self.home / "state"),
            "XDG_CONFIG_HOME": str(self.home / "config"),
            "LC_ALL": "C.UTF-8",
            "NO_COLOR": "1",
            # `python -m ddflow` run in a clone of ddflow's OWN repository would otherwise
            # import the clone's package (the working directory comes first on sys.path), and
            # the "new" code would be the old one
            "PYTHONSAFEPATH": "1",
            "COLUMNS": "200",
            "DDFLOW_NO_UPDATE_CHECK": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "dry-run",
            "GIT_AUTHOR_EMAIL": "dry-run@example.invalid",
            "GIT_COMMITTER_NAME": f"dry-run-{self.run_id}",
            "GIT_COMMITTER_EMAIL": "dry-run@example.invalid",
        }
        if cmd.pythonpath:
            env["PYTHONPATH"] = cmd.pythonpath
        return env

    def ddflow(
        self, cmd: Cmd, repo: Path, *args: str, agent: str = "", stdin: str | None = None
    ) -> P.Captured:
        flags = ["--repo", str(repo), *(["--agent", agent] if agent else [])]
        return self.capture(
            [*cmd.argv, *flags, *args],
            timeout=TIMEOUT_S,
            cwd=repo,
            env=self.env(cmd),
            input=stdin,
        )

    def capture(self, argv: list[str], **kw: Any) -> P.Captured:
        """`proc.capture`, with the command and its (bounded) output kept in the transcript."""
        began = time.monotonic()
        done = P.capture(argv, **kw)
        self.note(
            shlex.join(str(a) for a in argv),
            kw.get("cwd"),
            done.returncode,
            done.stdout,
            done.stderr,
            time.monotonic() - began,
            str(done.error) if done.error else "",
        )
        return done

    def note(
        self,
        command: str,
        cwd: Any,
        code: int | None,
        out: str,
        err: str,
        seconds: float,
        error: str = "",
    ) -> None:
        # a command that worked keeps little; one that did not keeps what explains it
        limit = STREAM_BOUND if code != 0 else STREAM_BOUND // 10  # 600
        self.commands.append(
            {
                "run": self.run_id,
                "argv": command,
                "cwd": str(cwd or ""),
                "returncode": code,
                "error": error,
                "seconds": round(seconds, 2),
                "stdout": bounded(out, limit),
                "stderr": bounded(err, limit),
            }
        )
        del self.commands[:-MAX_COMMANDS]

    def git(self, repo: Path, *args: str) -> G.GitResult:
        began = time.monotonic()
        done = G.run(repo, *args, timeout=TIMEOUT_S, env=self.env(self.new))
        shown = f"git -C {shlex.quote(str(repo))} {shlex.join(args)}"
        self.note(shown, repo, done.code, done.out, done.err, time.monotonic() - began)
        return done

    def clone(self, name: str, src: Path | None = None) -> Path:
        """A new clone of the pristine copy (or of ``src``) on its own branch."""
        dest = self.opt.work / name
        done = self.git(
            self.opt.work, "clone", "--quiet", "--local", str(src or self.pristine), str(dest)
        )
        if not done.ok:
            raise CouldNotRun(f"git clone failed: {done.text()}")
        for key, value in (("user.name", "dry-run"), ("user.email", "dry-run@example.invalid")):
            self.git(dest, "config", key, value)
        self.git(dest, "config", "commit.gpgsign", "false")
        return dest

    def apply_args(self, backup: str, keys: Iterable[str] = ()) -> list[str]:
        """`upgrade --apply` with the confirmations the plan asked for (the rehearsal gives them:
        what the operator would be asked is judged by the plan step, not by stopping here)."""
        asked = [x for k in sorted({*self.confirms, *keys}) for x in ("--confirm", k)]
        return ["upgrade", "--apply", "--backup", backup, *asked, "--reason", self.opt.reason]

    def apply_in(self, repo: Path, backup: str, agent: str) -> P.Captured:
        """`upgrade --apply` in ``repo`` with the confirmations ITS plan asks for, plus the
        earlier plan's (the clone may differ: a hand edit, another history)."""
        planned = self.ddflow(self.new, repo, "upgrade", "--plan", "--json", agent=agent)
        keys = [_key(i) for i in _asked(planned)]
        return self.ddflow(self.new, repo, *self.apply_args(backup, keys), agent=agent)

    def doctor_ok(self, doctor: P.Captured) -> bool:
        """Did doctor pass, allowing for the contested records the plan already announced?"""
        return doctor.returncode == 0 or contested_only(doctor.stdout, self.contested)

    def commit_all(self, repo: Path, message: str) -> None:
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "--quiet", "--no-verify", "--allow-empty", "-m", message)

    def shards_of(self, repo: Path) -> dict[str, bytes]:
        return shard_bytes(repo)

    # the steps

    def run(self) -> Report:
        self.report.started = clock.now_iso(timespec="seconds")
        self.report.facts["new"] = self.report.new_label
        before = self.guard(
            1, "read the live project first", lambda: snapshot_live(self.opt.source)
        )
        if self.guard(1, "clone at the installed version's commit", self.step_clone):
            if self.opt.old is None and self.opt.build_old:
                self.guard(1, "build the old ddflow from the clone", self.step_build_old)
            self.guard(2, "read with the new code", self.step_read)
            self.guard(3, "upgrade --plan", self.step_plan)
            self.guard(4, "apply with a snapshot, again, restore", self.step_apply)
            self.guard(5, "old ddflow on the upgraded copy", self.step_old_reader)
            self.guard(
                6,
                "MCP connect: propose, wait for consent, apply",
                self.step_connect,
            )
            self.guard(7, "two agents, one task", self.step_agents)
            self.guard(8, "shards from the old version merge", self.step_shards)
            self.guard(9, "an event upcaster over the old log", self.step_upcaster)
        after = self.guard(
            10, "read the live project afterwards", lambda: snapshot_live(self.opt.source)
        )
        if before is not None and after is not None:
            self.judge_live(before, after)
        return self.report

    def footprint(self) -> Footprint:
        """What the rehearsal's own backups and snapshot tags (in the clones) are called."""
        versions: set[str] = set()
        for clone in sorted(self.opt.work.iterdir()) if self.opt.work.is_dir() else []:
            kept = clone / ".ddflow" / "backups"
            if kept.is_dir():
                versions |= {backup_versions(b.name) for b in kept.iterdir()}
            if (clone / ".git").exists():
                tags = G.run(clone, "tag", "--list", f"{SNAPSHOT_PREFIX}*")
                versions |= {
                    backup_versions(t.removeprefix(SNAPSHOT_PREFIX)) for t in tags.out.splitlines()
                }
        versions.discard("")
        return Footprint(self.run_id, self.started_stamp, clock.compact_at(), frozenset(versions))

    def judge_live(self, before: Live, after: Live) -> None:
        """Step 10. What of the rehearsal is in the live project fails; what only other agents
        did (their snapshot tags and backups, a shard rewritten or still being written) is
        `LIVE_CHANGED`: not a pass, and not the rehearsal's fault either, so `unavailable`."""
        name = "live project untouched"
        mine = self.footprint()
        leaks = live_leaks(before, after, mine)
        moved = live_moved(before, after, mine)
        if leaks:
            self.add(10, name, False, "; ".join([*leaks, *moved]))
            return
        if moved:
            self.report.facts["live_changed"] = moved
            self.add(
                10,
                LIVE_CHANGED,
                None,
                "; ".join(moved) + ": nothing of the rehearsal is in it, but the project moved "
                "under the run, so it cannot be said to be untouched",
            )
            return
        others = [f"snapshot tag {t}" for t in sorted(set(after.tags) - set(before.tags))] + [
            f"backup {b}" for b in sorted(set(after.backups) - set(before.backups))
        ]
        note = "; ".join(
            [
                "nothing of the live project changed"
                if after.shards == before.shards
                else "no shard of the rehearsal; its log only grew",
                *([f"made by others meanwhile and ignored: {', '.join(others)}"] if others else []),
            ]
        )
        self.add(10, name, True, note)

    def step_clone(self) -> bool:
        src, work = Path(self.opt.source).resolve(), Path(self.opt.work).resolve()
        if work == src or work.is_relative_to(src) or src.is_relative_to(work):
            raise CouldNotRun("the scratch directory must be outside the project")
        if not G.run(src, "rev-parse", "--git-dir").ok:
            raise CouldNotRun(f"{src} is not a git repository")
        commit = G.run(src, "rev-parse", "--verify", f"{self.opt.from_commit}^{{commit}}")
        if not commit.ok:
            raise CouldNotRun(f"no commit {self.opt.from_commit!r} in {src}")
        self.report.from_commit = commit.out
        if work.is_dir() and any(work.iterdir()):
            raise CouldNotRun(f"the scratch directory {work} is not empty: name a new one")
        work.mkdir(parents=True, exist_ok=True)
        self.home.mkdir(parents=True, exist_ok=True)
        # only what is COMMITTED is read, at the commit just resolved (not a moving HEAD); a
        # gc or ref update in the live repo can fail one clone, so it is tried once more
        args = ("clone", "--quiet", "--local", "--no-hardlinks", "--no-checkout", str(src))
        done = self.git(work, *args, str(self.pristine))
        if not done.ok:
            shutil.rmtree(self.pristine, ignore_errors=True)
            time.sleep(CLONE_RETRY_S)
            done = self.git(work, *args, str(self.pristine))
        if not done.ok:
            raise CouldNotRun(f"git clone failed twice: {done.text()}")
        checked = self.git(self.pristine, "checkout", "--quiet", "--detach", commit.out)
        if not checked.ok:
            raise CouldNotRun(f"git could not check out {commit.out[:10]}: {checked.text()}")
        self.git(self.pristine, "checkout", "--quiet", "-b", "dryrun")
        shards = shard_bytes(self.pristine)
        if not shards:
            return self.add(
                1,
                "clone at the installed version's commit",
                False,
                "the clone has no event log (is .ddflow/events committed?)",
            )
        st = folded_state(self.pristine)
        self.report.facts.update(
            events=st.event_count,
            stamps=sorted(st.ddflow_versions),
            highest=st.highest_version,
            shards=sorted(shards),
        )
        return self.add(
            1,
            "clone at the installed version's commit",
            True,
            f"{commit.out[:10]}: {st.event_count} events in {len(shards)} shard(s); stamps "
            f"{', '.join(sorted(st.ddflow_versions)) or 'none (predates stamps)'}",
        )

    def step_build_old(self) -> bool | None:
        """Install the clone, as it stands at the installed version's commit, in a venv of its
        own: the ddflow the operator runs now. Needs `uv` and the package index or its cache."""
        venv = self.opt.work / "old-venv"
        env = {
            **self.env(Cmd([])),
            "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", str(Path.home() / ".cache" / "uv")),
        }
        made = self.capture(["uv", "venv", "--quiet", str(venv)], timeout=TIMEOUT_S, env=env)
        if not made.ran or made.returncode != 0:
            self.unrunnable("build the old ddflow from the clone")
            return self.add(
                1,
                "build the old ddflow from the clone",
                None,
                f"uv venv failed: {(made.stderr or str(made.error)).strip()[-160:]}",
            )
        python = venv / "bin" / "python"
        built = self.capture(
            ["uv", "pip", "install", "--quiet", "--python", str(python), str(self.pristine)],
            timeout=TIMEOUT_S * 3,
            env=env,
        )
        if not built.ran or built.returncode != 0:
            self.unrunnable("build the old ddflow from the clone")
            return self.add(
                1,
                "build the old ddflow from the clone",
                None,
                f"install failed: {(built.stderr or str(built.error)).strip()[-160:]}",
            )
        self.opt.old = Cmd(
            [str(python), "-m", "ddflow"],
            "",
            f"built from the clone at {self.report.from_commit[:10]}",
        )
        self.report.old_label = self.opt.old.label
        return self.add(1, "build the old ddflow from the clone", True, self.opt.old.label)

    def step_read(self) -> bool:
        repo = self.clone("read")
        before = state_digests(repo)
        bad: list[str] = []
        for args in READS:
            done = self.ddflow(self.new, repo, *args, agent="dry-read")
            if not done.ran or done.returncode not in (0, 1, 2) or "Traceback" in done.stderr:
                bad.append(f"{' '.join(args)} -> {done.returncode} {done.stderr.strip()[-120:]}")
        st = folded_state(repo)
        for item in sorted(st.items)[: self.opt.max_show or None]:
            done = self.ddflow(self.new, repo, "show", item, agent="dry-read")
            if not done.ran or done.returncode not in (0, 1, 2) or "Traceback" in done.stderr:
                bad.append(f"show {item} -> {done.returncode}")
        after = state_digests(repo)
        moved = [k for k in users_part(before) if before[k] != after.get(k)]
        if moved:
            bad.append(f"reading changed {', '.join(moved)}")
        if st.skipped_kinds:
            bad.append(f"events the new code skipped: {st.skipped_kinds}")
        self.report.facts["digest"] = D.of_obj(users_part(before))
        return self.add(
            2,
            "read with the new code",
            not bad,
            "; ".join(bad)
            or f"{len(READS)} read commands and {min(len(st.items), self.opt.max_show or len(st.items))} item(s), state unchanged",
        )

    def step_plan(self) -> dict[str, Any]:
        repo = self.clone("plan")
        done = self.ddflow(self.new, repo, "upgrade", "--plan", "--json", agent="dry-plan")
        if not done.ran or done.returncode not in (0, 1):
            raise CouldNotRun(
                f"upgrade --plan exited {done.returncode}: {done.stderr.strip()[-200:]}"
            )
        plan = json.loads(done.stdout)
        text = self.ddflow(self.new, repo, "upgrade", "--plan", agent="dry-plan").stdout
        self.report.preview = text
        items = [i for v in plan["categories"].values() for i in v]
        asked = [i for i in items if i["action"] == "needs operator confirmation"]
        odd = [
            i for i in items if i["action"] not in ("agent", "note", "needs operator confirmation")
        ]
        detail = f"{plan['total']} item(s)"
        if asked:
            detail += "; the operator would be asked to confirm: " + ", ".join(
                _key(i) for i in asked
            )
        if odd:
            detail += "; unknown action on: " + ", ".join(i["id"] for i in odd)
        self.confirms = [_key(i) for i in asked]
        detail += self._contested_detail(items)
        self.add(3, "upgrade --plan", not ((asked and self.opt.asked_fails) or odd), detail)
        return plan

    def _contested_detail(self, items: list[dict[str, Any]]) -> str:
        """Remember the records the plan holds CONTESTED and say they are an expected step."""
        held = [i for i in items if i["category"] == "records"]
        self.contested = {f"{i['kind']} {i['record']}" for i in held}
        if not held:
            return ""
        return (
            f"; {len(held)} contested record(s) are an expected plan step (the operator "
            "resolves them, the apply does not): "
            + ", ".join(f"{i['kind']} {i['record']} -> {i['fix'].split('  ')[0]}" for i in held)
        )

    def step_apply(self) -> bool:
        repo = self.clone("apply")
        edited = self._hand_edit(repo)
        before_users = users_part(state_digests(repo))
        before_tree, before_shards = tree_digest(repo), shard_bytes(repo)
        done = self.apply_in(repo, "snapshot", "dry-apply")
        problems: list[str] = []
        if edited and not _edit_kept(repo, edited):
            problems.append(f"the hand edit inside the stamped block of {edited} was lost")
        if done.returncode != 0:
            problems.append(
                f"apply exited {done.returncode}: {(done.stderr or done.stdout).strip()[-200:]}"
            )
        problems += self._after_apply(repo, before_users)
        self.commit_all(repo, "applied")
        # a second apply changes nothing: no file, no event (the notes a plan keeps listing are
        # acknowledged again, which writes nothing)
        events, tree = len(events_of(repo)), tree_digest(repo)
        again = self.ddflow(
            self.new, repo, "upgrade", "--apply", "--backup", "none", agent="dry-apply"
        )
        if again.returncode != 0 or len(events_of(repo)) != events or tree_digest(repo) != tree:
            problems.append(f"a second apply was not a no-op (exit {again.returncode})")
        problems += self._restore(repo, before_tree, before_shards)
        self.report.facts["apply"] = "ok" if not problems else "problems"
        return self.add(
            4,
            "apply with a snapshot, again, restore",
            not problems,
            "; ".join(problems)
            or "users' records unchanged, "
            + (f"hand edit in {edited} kept, " if edited else "no stamped block to hand-edit, ")
            + "doctor clean, second apply a no-op, restore exact",
        )

    def _hand_edit(self, repo: Path) -> str:
        """Write a line of our own inside a stamped block of a managed instruction file and
        commit it, as an operator's hand edit would sit: the apply must not lose it. The name
        of the file, or "" when the project has no stamped block."""
        for name in ("CLAUDE.md", "AGENTS.md"):
            path = Path(repo) / name
            lines = path.read_text("utf-8").splitlines(keepends=True) if path.is_file() else []
            for at, line in enumerate(lines):
                if "ddflow:begin" in line and "sha=" in line:
                    lines.insert(at + 1, f"{HAND_EDIT}\n")
                    fsio.replace_text(path, "".join(lines))
                    self.commit_all(repo, "a hand edit")
                    return name
        return ""

    def _after_apply(self, repo: Path, before_users: dict[str, str]) -> list[str]:
        out: list[str] = []
        after = users_part(state_digests(repo))
        moved = [k for k in before_users if before_users[k] != after.get(k)]
        if moved:
            out.append(f"the apply changed {', '.join(moved)}")
        doctor = self.ddflow(self.new, repo, "doctor", agent="dry-apply")
        if not self.doctor_ok(doctor):
            out.append(
                f"doctor after the apply exited {doctor.returncode}: {doctor.stdout.strip()[-160:]}"
            )
        left = self.ddflow(self.new, repo, "upgrade", "--plan", "--json", agent="dry-apply")
        todo = [i["id"] for i in _items(left) if i["action"] != "note"]
        if todo:
            out.append(f"the plan after the apply still holds {', '.join(todo[:5])}")
        return out

    def _restore(self, repo: Path, tree: str, shards: dict[str, bytes]) -> list[str]:
        out: list[str] = []
        restored = self.ddflow(self.new, repo, "upgrade", "--restore", agent="dry-apply")
        if restored.returncode != 0:
            return [
                f"restore exited {restored.returncode}: {(restored.stderr or restored.stdout).strip()[-160:]}"
            ]
        if tree_digest(repo) != tree:
            dirty = self.git(
                repo,
                "--no-optional-locks",
                "status",
                "--porcelain",
                "--",
                ".",
                ":!.ddflow/events",
                ":!.ddflow/local",
                ":!.ddflow/backups",
            ).out
            out.append(
                "the files after restore are not the files before: "
                + (dirty.replace("\n", ", ")[:160] or "untracked content differs")
            )
        if lost := prefixes_kept(shards, shard_bytes(repo)):
            out.append(f"restore lost bytes of the shard(s) {', '.join(lost)}")
        return out

    def step_old_reader(self) -> bool | None:
        if self.opt.old is None:
            return self.add(
                5,
                "old ddflow on the upgraded copy",
                None,
                "no old ddflow given (--old-cmd, --build-old or --simulate-old)",
            )
        old = self.opt.old
        repo = self.clone("old")
        upgraded = self.apply_in(repo, "none", "dry-apply")
        if upgraded.returncode != O.OK:
            return self.add(
                5,
                "old ddflow on the upgraded copy",
                False,
                f"the copy was not upgraded (exit {upgraded.returncode}): "
                f"{(upgraded.stderr or upgraded.stdout).strip()[-160:]}",
            )
        self.commit_all(repo, "applied")
        shards = shard_bytes(repo)
        bad: list[str] = []
        for args in (("status", "--json"), ("doctor",), ("brief",)):
            done = self.ddflow(old, repo, *args, agent="dry-old")
            if (
                not done.ran
                or done.returncode not in (O.OK, O.FAIL, O.NOTHING)
                or "Traceback" in done.stderr
            ):
                bad.append(
                    f"old {' '.join(args)} exited {done.returncode}: {done.stderr.strip()[-120:]}"
                )
        write = self.ddflow(
            old,
            repo,
            "lesson",
            "add",
            "--title",
            "written by the old ddflow",
            "--new",
            agent="dry-old",
        )
        refused = write.returncode == O.REFUSED and "pgrade" in (write.stderr + write.stdout)
        landed = write.returncode == 0
        if not (refused or landed):
            bad.append(
                f"the old writer exited {write.returncode} without the remedy: {(write.stderr or write.stdout).strip()[-160:]}"
            )
        if lost := prefixes_kept(shards, shard_bytes(repo)):
            bad.append(f"the old ddflow disturbed the shard(s) {', '.join(lost)}")
        after = folded_state(repo)
        if after.fold_problems or after.skipped_kinds:
            bad.append("the new code cannot fold what the old one left")
        how = "refused its write with the remedy" if refused else "wrote without disturbing a byte"
        return self.add(
            5, "old ddflow on the upgraded copy", not bad, "; ".join(bad) or f"read fine, {how}"
        )

    def step_connect(self) -> bool:
        repo = self.clone("connect")
        before_tree, known = tree_digest(repo), {e.get("id") for e in events_of(repo)}
        forced = self._serve(repo, _HELLO, start_timeout=FORCED_TIMEOUT_S)
        self._forget_start(repo)  # each session runs the start check itself, not a kept answer
        default = self._serve(repo, _HELLO, start_timeout=None)  # as shipped
        self._forget_start(repo)
        first = self._serve(repo, [*_HELLO, _call(3, "ddflow_brief", {}), _TOOLS_LIST])
        if START_TIMED_OUT in (first.get(1) or {}).get("instructions", ""):
            self.unrunnable("MCP connect: propose, wait for consent, apply")
            return self.add(
                6,
                "MCP connect: propose, wait for consent, apply",
                None,
                f"the server's start check ran out of its time bound even with {START_TIMEOUT_S}s "
                "(a very loaded machine, or a log too big for it): it could not propose",
            )
        size, problems = self._proposed(first)
        problems += self._noted(forced) + self._default_path(default)
        applied = [
            e for e in events_of(repo) if e.get("id") not in known and e.get("kind") in APPLYING
        ]
        if applied or tree_digest(repo) != before_tree:
            problems.append("something was applied before the operator consented")
        problems += self._consented(repo)
        stale, more = self._told_to_restart(repo)
        problems += more
        told = (
            f"{len(stale)} older agent(s) told to restart"
            if stale
            else "no older-version agent in the log to tell"
        )
        return self.add(
            6,
            "MCP connect: propose, wait for consent, apply",
            not problems,
            "; ".join(problems)
            or f"proposed on connect, nothing applied until consent, then applied; "
            f"tools/list {size} bytes; {told}",
        )

    @staticmethod
    def _forget_start(repo: Path) -> None:
        """Drop the start report a session kept in the (git-ignored) project marker."""
        (repo / US.MARKER).unlink(missing_ok=True)

    def _noted(self, forced: dict[int, dict[str, Any]]) -> list[str]:
        """A start check given next to no time is a NOTE in the handshake: the upgrade is
        pending, the operator is asked and told where the plan is (B-start-timeout-drops-
        proposal). A log so small that the plan fit in the time gets the proposal instead,
        which is as good: what fails is silence."""
        text = (forced.get(1) or {}).get("instructions", "")
        note = all(w in text for w in ("PENDING", "Ask the operator", "ddflow upgrade --plan"))
        if note or ("newer than this project" in text and "Ask the operator" in text):
            return []
        return ["a start check that ran out of time left no note that the upgrade is pending"]

    def _default_path(self, shipped: dict[int, dict[str, Any]]) -> list[str]:
        """The server with the shipped time bound (a smoke test of the real default, which on an
        idle machine proposes and on a loaded one notes; the forced timeout above is the
        deterministic half): its handshake is never silent about the upgrade."""
        text = (shipped.get(1) or {}).get("instructions", "")
        if "newer than this project" in text and "Ask the operator" in text:
            return []
        return [
            "the handshake with the shipped start_timeout_s neither proposed nor noted the upgrade"
        ]

    def _proposed(self, first: dict[int, dict[str, Any]]) -> tuple[int, list[str]]:
        """What the first session shows: the handshake asks the operator, the tools fit."""
        problems: list[str] = []
        text = (first.get(1) or {}).get("instructions", "")
        if "newer than this project" not in text or "Ask the operator" not in text:
            problems.append("the handshake did not propose the upgrade and ask the operator")
        size, bad = tools_problems((first.get(2) or {}).get("tools") or [], self.opt.tools_budget)
        return size, problems + bad

    def _consented(self, repo: Path) -> list[str]:
        """The operator says yes: the confirmed apply over MCP finishes and doctor is clean."""
        arguments = {
            "apply": "all",
            "backup": "none",
            "confirm": self.confirms,
            "reason": self.opt.reason,
        }
        done = self._serve(repo, [*_HELLO, _call(4, "ddflow_upgrade", arguments)])
        body = _tool_body(done.get(4))
        out: list[str] = []
        if (body.get("applied") or {}).get("failed", 1):
            out.append(f"the confirmed apply over MCP did not finish: {json.dumps(body)[:160]}")
        doctor = self.ddflow(self.new, repo, "doctor", agent="dry-connect")
        if not self.doctor_ok(doctor):
            out.append(f"doctor after the consent exited {doctor.returncode}")
        return out

    def _told_to_restart(self, repo: Path) -> tuple[list[str], list[str]]:
        """`(the agents `status` says must restart, problems)`: a log holding agents stamped by
        an older ddflow must name them."""
        status = _tool_body(
            self._serve(repo, [*_HELLO, _call(5, "ddflow_upgrade", {"status": True})]).get(5)
        )
        stale = [b["agent"] for b in status.get("behind", [])]
        self.report.facts["restart"] = stale
        folded = folded_state(repo)
        running = V.running()
        if any(is_older(v, running) for v in folded.ddflow_versions) and not stale:
            return stale, ["the log holds older-version agents but none was told to restart"]
        return stale, []

    def _serve(
        self, repo: Path, msgs: list[dict[str, Any]], start_timeout: float | None = START_TIMEOUT_S
    ) -> dict[int, dict[str, Any]]:
        """Talk to the new MCP server over stdio for these messages; replies by request id.
        The start check gets ``start_timeout`` seconds (the rehearsal tests the flow, not the
        speed), or the shipped bound when None. Raises `CouldNotRun` when the server did not
        run to its end."""
        argv = [sys.executable, "-m", "ddflow.surfaces.mcp", "--repo", str(repo)]
        extra = (
            {} if start_timeout is None else {"DDFLOW_UPGRADE_START_TIMEOUT_S": str(start_timeout)}
        )
        done = self.capture(
            argv,
            timeout=TIMEOUT_S,
            cwd=repo,
            env={**self.env(self.new), **extra},
            input="\n".join(json.dumps(m) for m in msgs) + "\n",
        )
        if not done.ran:
            raise CouldNotRun(f"the MCP server did not run: {done.error}")
        replies: dict[int, dict[str, Any]] = {}
        for line in done.stdout.splitlines():
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict) and isinstance(msg.get("id"), int):
                replies[msg["id"]] = msg.get("result") or {"error": msg.get("error")}
        # what the steps judge is in the replies, and a reply that is big is cut in the middle
        # of the command's output: keep each one's head and tail
        self.commands[-1]["replies"] = {
            str(k): bounded(json.dumps(v, default=str), 2500) for k, v in replies.items()
        }
        return replies

    def step_agents(self) -> bool:
        a, b = self.opt.agents
        repo = self.clone("agents")
        up = self.apply_in(repo, "none", a)
        self.commit_all(repo, "applied")
        problems: list[str] = (
            [] if up.returncode in (O.OK, O.REFUSED) else [f"apply exited {up.returncode}"]
        )
        phase = _first_phase(repo)
        add = self.ddflow(
            self.new,
            repo,
            "task",
            "add",
            "DRY-1",
            "--title",
            "dry run task",
            "--phase",
            phase,
            "--globs",
            "dry/a.txt",
            agent=a,
        )
        claimed = self.ddflow(self.new, repo, "claim", "DRY-1", agent=a)
        refused = self.ddflow(self.new, repo, "claim", "DRY-1", agent=b)
        if add.returncode != 0 or claimed.returncode != 0:
            problems.append(
                f"{a} could not add and claim a task ({add.returncode}, {claimed.returncode})"
            )
        if refused.returncode != O.REFUSED:
            problems.append(f"{b} was not refused a task {a} holds (exit {refused.returncode})")
        wt = Path(repo) / ".ddflow" / "worktrees" / "DRY-1"
        if wt.is_dir():
            (wt / "dry").mkdir(exist_ok=True)
            fsio.replace_text(wt / "dry" / "a.txt", "dry run\n")
            self.commit_all(wt, "dry run edit")
        shown = self.ddflow(self.new, repo, "gate", "status", "DRY-1", "--json", agent=a)
        for gate in _pipeline(shown):
            self.ddflow(
                self.new,
                repo,
                "gate",
                "skip",
                "DRY-1",
                gate,
                "--reason",
                "dry run: a rehearsal, not evidence",
                agent=a,
            )
        merged = self.ddflow(self.new, repo, "merge", "DRY-1", agent=a)
        done = self.ddflow(self.new, repo, "complete", "DRY-1", "--model", "dry-run", agent=a)
        if merged.returncode != 0:
            problems.append(
                f"merge exited {merged.returncode}: {(merged.stderr or merged.stdout).strip()[-120:]}"
            )
        if done.returncode not in (O.OK, O.REFUSED):
            problems.append(f"complete exited {done.returncode}")
        # a refusal (no independent reviewer ran) is the design: release, never force
        left = (
            self.ddflow(self.new, repo, "release", "DRY-1", agent=a)
            if done.returncode == O.REFUSED
            else done
        )
        took = self.ddflow(self.new, repo, "claim", "DRY-1", agent=b)
        if done.returncode == O.REFUSED and (left.returncode != 0 or took.returncode != 0):
            problems.append(
                f"after the honest release {b} could not take the task ({left.returncode}, {took.returncode})"
            )
        doctor = self.ddflow(self.new, repo, "doctor", agent=a)
        if not self.doctor_ok(doctor):
            problems.append(f"doctor exited {doctor.returncode}")
        how = (
            "completed"
            if done.returncode == 0
            else "complete refused (no independent reviewer), released honestly"
        )
        return self.add(
            7,
            "two agents, one task",
            not problems,
            "; ".join(problems) or f"claim, refusal of the second agent, edit, gates, merge; {how}",
        )

    def union_of(self, one: Path, two: Path) -> Path:
        """A directory holding the shards of both clones side by side, as they were written:
        the log reads it, so a torn line is recovered the way it is in a real log."""
        union = self.opt.work / "union"
        events = union / ".ddflow" / "events"
        events.mkdir(parents=True)
        for tag, repo in (("one", one), ("two", two)):
            for shard in sorted((Path(repo) / ".ddflow" / "events").glob("*.jsonl")):
                shutil.copyfile(shard, events / f"{tag}--{shard.name}")
        return union

    def step_upcaster(self) -> bool:
        kind, problems = upcast_problems(self.pristine)
        return self.add(
            9,
            "an event upcaster over the old log",
            not problems,
            "; ".join(problems)
            or f"every {kind} event upcast with its id kept, the same records folded, "
            "an older reader skips the new version",
        )

    def step_shards(self) -> bool | None:
        if self.opt.old is None:
            return self.add(8, "shards from the old version merge", None, "no old ddflow given")
        old = self.opt.old
        one, two = self.clone("shard-one"), self.clone("shard-two")
        for repo, title in ((one, "from clone one"), (two, "from clone two")):
            self.ddflow(old, repo, "lesson", "add", "--title", title, "--new", agent="dry-shared")
            self.commit_all(repo, title)
        attrs = Path(one) / ".gitattributes"
        if not attrs.is_file() or "merge=union" not in attrs.read_text("utf-8"):
            return self.add(
                8,
                "shards from the old version merge",
                False,
                "the project has no merge=union for its shards",
            )
        written = self.union_of(one, two)
        self.git(one, "remote", "add", "two", str(two))
        self.git(one, "fetch", "--quiet", "two")
        merged = self.git(one, "merge", "--no-edit", "--quiet", "two/dryrun")
        if not merged.ok:
            return self.add(
                8,
                "shards from the old version merge",
                False,
                f"merge failed: {merged.text()[-160:]}",
            )
        want = state_part_digest(written)
        got = state_part_digest(one)
        ok = want == got
        return self.add(
            8,
            "shards from the old version merge",
            ok,
            "the merged shards fold to what both clones wrote"
            if ok
            else "the merged log folds differently from the union of what both clones wrote",
        )


# -- small readers --------------------------------------------------------------------------


def tools_problems(tools: list[dict[str, Any]], budget: int) -> tuple[int, list[str]]:
    """`(compact bytes of tools/list, what is wrong with it)`: empty or carrying output schemas
    (`[mcp].output_schemas` ships off). ``budget`` is reported, never enforced: the whole list
    over `TOOLS_LIST_BUDGET` is a number, and only the core tier keeps a ratchet (D-mcp-budget-report)."""
    size = len(json.dumps({"tools": tools}, separators=(",", ":")))
    out = []
    if not tools:
        out.append("tools/list answered nothing")
    if any("outputSchema" in t for t in tools):
        out.append("tools/list carries output schemas ([mcp].output_schemas should be off)")
    return size, out


#: The opening of every MCP session the rehearsal holds.
_HELLO = (
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "dry-run", "version": "0"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
)
_TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


def _call(at: int, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": at,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }


def _items(plan: P.Captured) -> list[dict[str, Any]]:
    """Every item of a plan (`upgrade --plan --json`); none when it is not one."""
    try:
        cats = json.loads(plan.stdout)["categories"]
    except (ValueError, KeyError, TypeError):
        return []
    return [i for v in cats.values() for i in v]


def contested_only(text: str, planned: set[str]) -> bool:
    """Is every problem in ``doctor``'s output one of the CONTESTED records in ``planned``
    (`"decision D-x"`)? Then the failure is the plan's announced step, not a surprise: the
    count of problems must equal the contested lines, each naming a planned record."""
    counted = re.search(r"(\d+) problem\(s\)", text)
    lines = [ln for ln in text.splitlines() if " is CONTESTED" in ln]
    if not counted or not lines or int(counted.group(1)) != len(lines):
        return False
    for ln in lines:
        named = re.search(r"\b(decision|lesson|research) (\S+) is CONTESTED", ln)
        if not named or f"{named.group(1)} {named.group(2)}" not in planned:
            return False
    return True


def _asked(plan: P.Captured) -> list[dict[str, Any]]:
    """The items of a plan the operator would be asked to confirm."""
    return [i for i in _items(plan) if i["action"] == "needs operator confirmation"]


def _edit_kept(repo: Path, name: str) -> bool:
    """Is the rehearsal's hand edit still in ``name``, or kept beside it in a `.local-edits`?"""
    here = Path(repo) / name
    held = [here, *sorted(here.parent.glob(name + ".local-edits*"))]
    return any(HAND_EDIT in p.read_text("utf-8", "replace") for p in held if p.is_file())


def _key(item: dict[str, Any]) -> str:
    return str(
        item.get("key")
        or item.get("repair")
        or item.get("migration")
        or item.get("path")
        or item["id"]
    )


def _tool_body(result: dict[str, Any] | None) -> dict[str, Any]:
    try:
        return dict(json.loads(((result or {}).get("content") or [{}])[0].get("text") or "{}"))
    except (ValueError, TypeError):
        return {}


def _pipeline(shown: P.Captured) -> list[str]:
    """The gates of an item's pipeline that are skipped by hand (`merge` is done by ddflow)."""
    try:
        return [g for g in json.loads(shown.stdout)["pipeline"] if g != "merge"]
    except (ValueError, KeyError, TypeError):
        return []


def _first_phase(repo: Path) -> str:
    st = folded_state(repo)
    for item in st.items.values():
        if item.kind == "phase":
            return item.id
    return "P1"


def state_part_digest(repo: Path) -> str:
    """One digest of the user's records, folded from a project's log the way the log reads it."""
    return D.of_obj(users_part(state_digests(repo)))


def bounded(text: str, limit: int = STREAM_BOUND) -> str:
    """``text`` cut to about ``limit`` characters, the head and the tail kept."""
    if len(text) <= limit:
        return text
    half = limit // 2
    if not half:  # too small to keep two ends
        return text[: max(limit, 0)]
    return f"{text[:half]}\n... [{len(text) - 2 * half} characters cut] ...\n{text[-half:]}"


def _rehearse(opt: Options) -> tuple[Report, Rehearsal]:
    """One rehearsal in ``opt.work``, which is removed afterwards unless it is not ours (a
    scratch directory that already held something is refused, and never cleaned) or ``keep``."""
    work = Path(opt.work)
    ours = not work.exists() or (work.is_dir() and not any(work.iterdir()))
    rehearsal = Rehearsal(opt)
    try:
        return rehearsal.run(), rehearsal
    finally:
        if ours and not opt.keep:
            shutil.rmtree(work, ignore_errors=True)


def diag_directory(opt: Options) -> Path:
    if opt.diag_dir:
        return Path(opt.diag_dir)
    if os.environ.get(DIAG_ENV):
        return Path(os.environ[DIAG_ENV])
    return Path(opt.source) / ".ddflow" / "local" / "dryrun"


def checks(report: Report) -> list[dict[str, Any]]:
    return [asdict(c) for c in report.checks]


def write_diagnostics(opt: Options, report: Report, runs: list[Rehearsal]) -> str:
    """Write the step table of a failed run and each command's bounded output to a file; the
    path ("" when it could not be written: the report must still come out). A rehearsal that was
    repeated keeps every run: its table, its checks and its commands (each tagged with its run)."""
    data = {
        "run": runs[-1].run_id,
        "started": report.started,
        "source": report.source,
        "from_commit": report.from_commit,
        "exit": report.exit_code,
        "table": report.table(),
        "checks": checks(report),
        "facts": report.facts,
        "runs": [
            {"run": r.run_id, "table": r.report.table(), "checks": checks(r.report)} for r in runs
        ],
        "commands": [c for r in runs for c in r.commands],
    }
    name = f"{clock.compact_at()}-{runs[-1].run_id}.json"
    for choice in (diag_directory(opt), None):
        try:
            where = choice or fsio.unused_dir("ddflow-dryrun-diag-")  # the system temp directory
            where.mkdir(parents=True, exist_ok=True)
            fsio.replace_text(where / name, json.dumps(data, indent=1, default=str) + "\n")
            return str(where / name)
        except OSError:
            continue
    return ""


def run_all(opt: Options) -> Report:
    """Run the whole rehearsal and clean up (unless ``opt.keep``). If the live project changed
    under it in a way it cannot tell from its own doing (`LIVE_CHANGED`), it is run once more
    in a scratch directory of its own; the second run decides, and a project that moved again
    leaves that check `unavailable`. A failed run, one that could not run (a clone or a build that
    failed; not an old ddflow that was never offered) or one repeated because the project moved
    leaves its step table and every command's output in a file, named in the report."""
    original = opt.old  # building the old ddflow replaces it, in a scratch directory that goes
    report, rehearsal = _rehearse(opt)
    runs = [rehearsal]
    if report.facts.get("live_changed"):
        first = report.facts["live_changed"]
        again = replace(opt, work=Path(f"{opt.work}-retry"), old=original)
        report, rehearsal = _rehearse(again)
        runs.append(rehearsal)
        report.facts["first_run"] = f"{LIVE_CHANGED}: {'; '.join(first)}"
    unexplained = (
        report.facts.get("live_changed")
        or report.facts.get("first_run")
        or report.facts.get("could_not_run")
    )
    if any(c.status == FAIL for c in report.checks) or unexplained:
        report.diagnostics = write_diagnostics(opt, report, runs)
    return report


# -- the command line (scripts/ci/upgrade-dry-run is this, started from a checkout) -----------

COULD_NOT_RUN = 2


def tools_budget(tree: Path) -> int:
    """The compact tools/list budget the suite ratchets, read from the tree under test; 0 when
    that tree carries no test of it (an installed package)."""
    path = tree / "tests" / "test_mcp_tool_budget.py"
    text = path.read_text("utf-8") if path.is_file() else ""
    found = re.search(r"^TOOLS_LIST_BUDGET\s*=\s*([\d_]+)", text, re.M)
    return int(found.group(1).replace("_", "")) if found else 0


def parser(doc: str = __doc__ or "") -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="upgrade-dry-run",
        description=doc,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--source", default="", help="the project to rehearse (default: this one)")
    which = ap.add_argument_group("the version the installed ddflow was built from (one of)")
    which.add_argument("--from-commit", default="", help="that commit")
    which.add_argument(
        "--installed-version", default="", help="that release: its tag, else its `release X` commit"
    )
    which.add_argument(
        "--previous-release",
        action="store_true",
        help="the newest release below this tree's version",
    )
    old = ap.add_argument_group("the old ddflow (steps 5 and 8)")
    old.add_argument("--old-cmd", default="", help="a ddflow to run as the old one, e.g. a venv's")
    old.add_argument(
        "--build-old", action="store_true", help="install the clone in a venv (uv) as the old one"
    )
    old.add_argument(
        "--simulate-old", default="", metavar="VERSION", help="this code declaring VERSION"
    )
    ap.add_argument(
        "--confirmations-ok",
        action="store_true",
        help="name the items the operator would be asked to confirm in the preview without "
        "failing (the release check: that is this project's own config, not the release's)",
    )
    ap.add_argument(
        "--max-show",
        type=int,
        default=25,
        help="run `show` on this many items in the read pass (0: every item; slow on a big log)",
    )
    ap.add_argument("--work", default="", help="scratch directory (default: a new temporary one)")
    ap.add_argument("--keep", action="store_true", help="keep the scratch directory")
    ap.add_argument(
        "--diag-dir",
        default="",
        help=f"where a failed run writes its step table and each command's output (default: "
        f"${DIAG_ENV}, else .ddflow/local/dryrun/ in the project)",
    )
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--report", default="", help="also write the report as Markdown to this file")
    ap.add_argument("--preview", default="", help="also write the upgrade preview to this file")
    return ap


def choose_commit(args: argparse.Namespace, source: Path) -> str:
    """The commit the installed ddflow was built from, from exactly one way of naming it."""
    named = [bool(args.from_commit), bool(args.installed_version), args.previous_release]
    if sum(named) != 1:
        raise CouldNotRun(
            "name the commit the installed ddflow was built from: --from-commit, "
            "--installed-version or --previous-release (exactly one)"
        )
    if args.from_commit:
        return str(args.from_commit)
    if args.installed_version:
        commit = release_commit(source, args.installed_version)
        if not commit:
            raise CouldNotRun(f"no tag or release commit for ddflow {args.installed_version}")
        return commit
    running = V.running()
    commit = previous_release(source, running)
    if not commit:
        raise CouldNotRun(f"no release tag below {running}")
    return commit


def old_from(args: argparse.Namespace) -> Cmd | None:
    if args.old_cmd:
        return Cmd(shlex.split(args.old_cmd), "", f"installed: {args.old_cmd}")
    return simulated_old(args.simulate_old) if args.simulate_old else None


def main(argv: list[str] | None = None, *, source: Path | None = None, doc: str = "") -> int:
    """The command line: exit 0 every check passed, 1 one failed, 2 it could not run."""
    args = parser(doc or __doc__ or "").parse_args(argv)
    project = Path(args.source or source or Path.cwd()).resolve()
    try:
        commit = choose_commit(args, project)
    except CouldNotRun as exc:
        print(f"upgrade-dry-run: {exc}", file=sys.stderr)
        return COULD_NOT_RUN
    opt = Options(
        source=project,
        work=Path(args.work) if args.work else fsio.unused_dir("ddflow-dry-run-"),
        from_commit=commit,
        old=old_from(args),
        keep=args.keep,
        tools_budget=tools_budget(Path(source_root())),
        build_old=args.build_old,
        asked_fails=not args.confirmations_ok,
        max_show=args.max_show,
        diag_dir=Path(args.diag_dir) if args.diag_dir else None,
    )
    report = run_all(opt)
    print(json.dumps(report.as_data(), indent=2) if args.json else report.table())
    if args.report:
        fsio.replace_text(args.report, report.markdown())
    if args.preview and report.preview:
        fsio.replace_text(args.preview, report.preview)
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
