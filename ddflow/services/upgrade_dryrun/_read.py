"""Helpers that read, never write: the new command, the snapshot of the live project, and the
fold/digest/upcast readers the steps compare before and after."""

from __future__ import annotations

import contextlib
import json
import sys
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from ...config import Config
from ...core import digest as D
from ...core import upcasters as U
from ...core.events import Event, is_older
from ...core.model import State, fold
from ...infra import git as G
from ...infra.log import EventLog
from ..backups import SIDECAR_SUFFIX
from ._model import _SIMULATED_OLD, KEPT, UPGRADE_OWNED, Cmd


def source_root() -> str:
    """The directory holding the `ddflow` package that is running: the NEW code's PYTHONPATH."""
    return str(Path(sys.modules["ddflow"].__file__).resolve().parent.parent)


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
