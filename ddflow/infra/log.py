"""The append-only log — the one place events are written and read.

Serialised appends under `fcntl.flock` with an `fsync`, and per-agent shards so two
agents never write the same file. The Lamport clock is the max over every parsed event,
not each shard's tail: a shard two clones wrote under one id is not monotone (B190).

Reading exploits the same append-only property: `read_all` re-parses only the bytes
APPENDED since the last read, because parsing is 84% of a read's cost (97 ms of 115 ms
at 20,000 events) and a line already parsed cannot have changed. This is why the log is
never compacted — see `tests/test_log_read_cache.py` for the probe that declined it.

The `Event` record itself lives in `core.events`: it is a value with no I/O, and the
domain layer needs it without needing this.
"""

from __future__ import annotations

import bisect
import contextlib
import functools
import getpass
import itertools
import json
import marshal
import operator
import os
import re
import secrets
import socket
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, TypeVar

from ..config import Config, LogConfig, SessionConfig
from ..core import digest as D
from ..core import redact as R
from ..core import upcasters as UP
from ..core.events import (
    OLDER_MARK,
    PROVENANCE_KINDS,
    SCHEMA_VERSION,
    SEEN_KIND,
    SKEW_OVERRIDDEN_KIND,
    TAIL_MAX_BYTES,
    TAIL_WINDOW_BYTES,
    Event,
    SkewRefused,
    canonical,
    stamp_facts,
    utcnow,
)
from ..core.model import known_kinds
from ..core.slug import safe_filename
from . import fsio
from . import git as G

__all__ = [
    "PROVENANCE_KINDS",
    "SCHEMA_VERSION",
    "Event",
    "EventLog",
    "SkewRefused",
    "canonical",
    "clear_parse_cache",
    "default_agent_id",
    "effective_agent_id",
    "resolve_agent_id",
    "utcnow",
]

_AGENT_ID_CACHE: dict[str, str] = {}

#: Kinds an append writes WITHOUT the stamp-and-guard step: the stamp itself, and the
#: override that exists to be written while the guard would refuse everything else.
_STAMP_EXEMPT = frozenset({SEEN_KIND, SKEW_OVERRIDDEN_KIND})

#: The local, git-ignored marker: the last ddflow version THIS machine acted under.
SEEN_MARKER = Path(".ddflow") / "local" / "seen.json"

#: The on-disk read snapshot (B166), beside the seen marker in the machine-local,
#: git-ignored directory. Never merged and never committed: it is a CACHE of the log,
#: and the log stays the only source of truth.
SNAPSHOT_FILE = "read-snapshot.bin"
#: How long a snapshot of OTHER code (another version or parser) is kept before the next
#: write garbage-collects it. One snapshot per fingerprint (D-compat): worktrees on
#: different ddflow versions stop overwriting each other's.
SNAPSHOT_STALE_DAYS = 7.0
#: A snapshot in use is touched at most this often (see `_touch_snapshot`).
SNAPSHOT_TOUCH_S = 86400.0
SNAPSHOT_FORMAT = 1
#: A snapshot stores each event as a tuple in `Event`'s own field order, derived from the
#: dataclass so the writer, the reader and the header cannot disagree about the layout.
_EVENT_FIELDS = tuple(f.name for f in fields(Event))
_event_tuple = operator.attrgetter(*_EVENT_FIELDS)
#: No snapshot below this many events: a cold parse is cheap there and a file is not free.
#: A module constant rather than a `[log]` knob for now (follow-up B-log-snapshot-knobs,
#: waiting on config.py); tests lower it.
SNAPSHOT_MIN_EVENTS = 5000
#: `DDFLOW_SNAPSHOT=0` turns the snapshot off (neither read nor written) for a process.
SNAPSHOT_ENV = "DDFLOW_SNAPSHOT"


def running_version() -> str:
    """The version of the code that is running (read at call time, so a test can set it)."""
    import ddflow

    return str(getattr(ddflow, "__version__", "") or "")


def version_known(version: str) -> bool:
    """A version worth stamping: one that parses. A source tree reporting "unknown" must not
    stamp a log with a version that compares as nothing."""
    from ..core.events import version_key

    return bool(version_key(version))


def install_kind() -> str:
    """How this ddflow is installed, cheaply: `source-tree` when the package lives in a
    checkout, else `installed`. (`services.install_info` is richer and runs git; a stamp
    written on every first write must not.)"""
    here = Path(__file__).resolve()
    return (
        "installed"
        if any(p in ("site-packages", "dist-packages") for p in here.parts)
        else ("source-tree")
    )


def skew_message(version: str, highest: str, by: str = "") -> str:
    who = f" (stamped by {by})" if by else ""
    return (
        f"REFUSED: this project's log has been worked on by ddflow {highest}{who}, and this "
        f"ddflow is {version}, which is older: writing now could drop or misread what the "
        f"newer one recorded. Upgrade ddflow-mcp to >= {highest} and retry (for example "
        f"`uvx --refresh --from ddflow-mcp ddflow ...`, or restart the MCP server after "
        f"upgrading). Reads still work. If you cannot upgrade, ask the user; only if the "
        f'user insists, rerun with --allow-older-version --reason "<why>" (MCP: the '
        f"allow_older_version argument carrying the reason). That override is recorded "
        f"(skew.overridden), marks this session's events as written by an older ddflow, and "
        f"covers this session only. [upgrade].skew = warn or off turns the guard down."
    )


_SKEW_WARNED: set[tuple[str, str]] = set()


def _warn_skew_once(version: str, highest: str) -> None:
    if (version, highest) in _SKEW_WARNED:
        return
    _SKEW_WARNED.add((version, highest))
    import sys

    print(
        f"ddflow: this log has been worked on by ddflow {highest}; this ddflow is {version}. "
        f"Writing anyway ([upgrade].skew = warn).",
        file=sys.stderr,
    )


def _write_seen_marker(root: Path, version: str) -> None:
    """Record, machine-locally, the version this machine last acted under. Best effort: a
    marker that cannot be written costs only the once-per-version upgrade notice."""
    path = Path(root) / SEEN_MARKER
    try:
        fsio.ensure_ignored_dir(path.parent)
        fsio.atomic_write(path, json.dumps({"version": version, "at": utcnow()}) + "\n")
    except OSError:
        pass


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class LogMark:
    """What `EventLog.mark()` saw: `(shard name, size in bytes, clock on its last line)`
    per shard, in name order. Equal marks mean nothing was appended; compare with `==`."""

    shards: tuple[tuple[str, int, int], ...]

    @property
    def count(self) -> int:
        return len(self.shards)

    @property
    def lamport(self) -> int:
        """The highest TAIL clock -- not the highest clock: after a union merge of a shard
        with two writers they differ (see `EventLog._highest_lamport`). Harmless for a
        fingerprint, since the merge changed the size; do not take a clock from it."""
        return max((high for _n, _s, high in self.shards), default=0)

    @property
    def bytes(self) -> int:
        return sum(size for _n, size, _h in self.shards)


@dataclass(slots=True)
class _Parsed:
    """What has already been parsed out of one shard, and how far into it we got.

    `consumed` is a byte offset just past the last NEWLINE taken, never merely the
    file size: a shard can end in a torn fragment from an append that died mid-write,
    and marking that fragment consumed would skip the event permanently once the
    writer completed it.

    `digest` is a hash of those `consumed` bytes, and it is re-verified on every read.
    That is the whole validity check, and it is a CONTENT check on purpose. The first
    version of this used `st_ino` on the reasoning that "a `git merge` writes a temp
    file and renames, so the inode changes" — which is false, and measurably so:

        $ git checkout -q other && stat -c %i .ddflow/events/a1.jsonl
        218500670
        $ git checkout -q main && stat -c %i .ddflow/events/a1.jsonl
        218500670

    Both `git checkout` and `git merge` rewrite the file IN PLACE. So switching branches
    left a warm cache serving the events of the branch you left, the tail read starting
    mid-line in the new file, and `ddflow doctor` reporting a torn append about an intact
    log — and because `Store.rebuild` takes its fingerprint from `head()` (the real file)
    while taking its events from `read_all()` (the stale set), that wrong state was
    written into the SQLite index and stamped as current. A transient cache bug became
    on-disk corruption that a fresh process would not rebuild away.

    Hashing is affordable precisely because parsing is not: at 20,000 events the prefix
    is 3.58 MB, which reads in 1.1 ms and digests in 4.3 ms, against the 97 ms of
    `Event.from_json` it avoids. A heuristic would save ~5 ms and cost soundness.
    """

    consumed: int
    digest: str
    #: Extended IN PLACE as the shard grows (never copied per read), so a caller must
    #: not hand this list out: `_read_shard` and `read_all` return copies.
    events: list[Event]
    skipped: int
    #: Identity of one unbroken lineage of this shard's bytes. Minted on a full parse and
    #: kept across every extension (which is only taken after the prefix digest verified),
    #: so "same `gen`" means "the events up to any earlier count are still the same
    #: events". `read_all`'s merged order keys on it.
    gen: object = None
    #: True when the events came (in whole or in part) from the on-disk snapshot rather
    #: than from parsing this process's own read of the bytes. `verify()` refuses those.
    seeded: bool = False
    #: How many leading events came from the snapshot (the rest were parsed here).
    snap_n: int = 0


@dataclass(slots=True)
class _Delta:
    """One shard read, in the form `read_all` can merge incrementally."""

    parsed: _Parsed | None  # the cache entry holding `whole`; None when not cached
    whole: list[Event]  # every whole-line event of the shard (the entry's own list)
    torn: list[Event]  # events parsed from an unterminated trailing fragment
    skipped: int  # unparseable lines, torn fragment included


@dataclass(slots=True)
class _Merged:
    """The de-duplicated, Lamport-sorted union of one events directory (B169).

    `read_all` used to sort every event and re-hash every id on EVERY call, which kept the
    warm read linear in the log even though the parse was already incremental. This holds
    the result and folds only what was appended since into it.
    """

    marks: dict[Path, tuple[object, int]]  # shard -> (lineage, whole events accounted for)
    uniq: list[Event]
    seen: set[str]


#: Absolute shard path -> what has been parsed from it. Process-lifetime and keyed by
#: path rather than held on the instance, because a fresh `EventLog` is built per API
#: call and per MCP tool call — an instance-level cache would never be hit twice.
#:
#: Sound ONLY because the log is append-only, and for a reason worth stating: an
#: incremental *projector* can disagree with `fold` and so is refused outright
#: (`Store.rebuild`), but an incremental *reader* cannot, because each line parses
#: independently of every other. Re-using a parse is not a second implementation of
#: anything.
#:
#: **Unlocked, because nothing in this process is concurrent.** `mcp.serve` is a strictly
#: sequential `for raw in stdin:` loop — no threads, no asyncio — so a request is finished
#: before the next is read. Adding concurrency to that loop means guarding this dict.
#:
#: No eviction policy beyond the ceiling: once full, the shards already in it keep their
#: entries and later ones are simply not cached. That is unfair rather than wrong, and a
#: project with enough shards to notice has a bigger problem (B166).
_PARSE_CACHE: dict[Path, _Parsed] = {}

#: Events directory -> its merged, sorted, de-duplicated order. Derived entirely from
#: `_PARSE_CACHE` (and re-validated against it on every read), so it can be dropped at any
#: moment at the cost of one full sort.
_MERGED: dict[Path, _Merged] = {}

#: Events directory -> snapshot entries not yet consumed by a shard read in this process
#: (shard file name -> (consumed, digest, skipped, event tuples)). Presence of the key
#: means "already tried to load"; the dict is emptied as shards are seeded.
_SNAPSHOTS: dict[Path, dict[str, tuple]] = {}

#: Events directory -> how many events the on-disk snapshot is known to cover, to decide
#: when the tail since then is worth a rewrite.
_SNAP_COVERED: dict[Path, int] = {}

#: Events directory -> the snapshot's saved merged order, for `_merge` to adopt once:
#: ([(shard name, events snapshotted)], flat indices into the concatenation of those
#: shards' snapshotted events, in sorted de-duplicated order).
_SNAP_ORDER: dict[Path, tuple[list[tuple[str, int]], list[int]]] = {}

#: Appends no larger than this are placed by bisection; a bigger batch rebuilds.
_MAX_INCREMENTAL = 64


def clear_parse_cache() -> None:
    """Drop every parsed shard.

    For tests. NOT needed after a merge or a branch switch — the per-read digest check
    detects those, which is the point of making it a content check. Nothing in `ddflow`
    calls this, and that is the correct amount.
    """
    _PARSE_CACHE.clear()
    _MERGED.clear()
    _SNAPSHOTS.clear()
    _SNAP_COVERED.clear()
    _SNAP_ORDER.clear()


def _cached_events() -> int:
    """How many events the whole cache is holding, across every shard of every repo."""
    return sum(len(e.events) for e in _PARSE_CACHE.values())


def _digest(data: bytes) -> str:
    """Are these the same bytes? An ephemeral, in-memory comparison — NOT a content
    address.

    Deliberately not the repo's `blake2b` convention (`events.Event.compute_id`,
    `ids.short_id`), and the distinction matters: those are stable identities that are
    written to disk and cannot change without invalidating every stored id. This one is
    never persisted and never compared across processes, so the only criterion is speed
    over a multi-megabyte buffer — where `sha256` wins on CPU acceleration, measured at
    4.3 ms per 3.58 MB against blake2b's 10.4 ms.
    """
    return D.content_digest(data)


@functools.lru_cache(maxsize=1)
def _parser_stamp() -> str:
    """A fingerprint of the code that turns a line into an Event.

    A snapshot holds PARSED events, so it must die when the parser changes even if the
    version string does not (a development checkout, a patched install): hashing the
    bytecode and constants of `Event.from_json` and `Event.compute_id` is cheap and needs
    no list of "things that affect parsing" to keep current.
    """
    h = D.hasher()
    for fn in (
        Event.from_json,
        Event.compute_id,
        Event.body,
        canonical,
        _parse_lines,
        _parse_event,
        _recover_glued,
    ):
        code = fn.__code__
        h.update(code.co_code)
        h.update(repr(code.co_consts).encode())
        h.update(repr(code.co_names).encode())
    return h.hexdigest()[:16]


#: Every line `Event.to_json` writes starts with this (`canonical` sorts the keys).
_LINE_START = '{"agent":'


def _parse_event(line: str) -> Event | None:
    """One line as an Event, or None. Any JSON that is not an event object is None, not
    an exception: a torn fragment can be any prefix of a line, including `123`."""
    try:
        return Event.from_json(line)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def _recover_glued(line: str) -> Event | None:
    """The whole event an older writer appended straight onto a torn fragment (B28b3839fe6).

    Before the writer terminated a torn tail, the next append landed on the fragment's line
    as `<fragment><event>`, and the event was lost with it. Each later start of an event
    line is tried as the event; one is accepted only when its id is the hash of its body,
    so a fragment cannot be resurrected as something it never was.
    """
    at = line.find(_LINE_START, 1)
    while at > 0:
        ev = _parse_event(line[at:])
        if ev is not None and ev.id and ev.id == ev.compute_id():
            return ev
        at = line.find(_LINE_START, at + 1)
    return None


def _parse_lines(chunk: bytes) -> tuple[list[Event], int]:
    """`(events, unparseable-line-count)` for a run of whole lines."""
    out: list[Event] = []
    skipped = 0
    # "\n" alone ends a line: `splitlines()` also breaks on U+2028, U+2029 and U+0085, which
    # `canonical` writes raw inside a string, and cut such an event in two (B5035a55092).
    for raw in chunk.decode("utf-8", errors="replace").split("\n"):
        line = raw.strip()
        if not line:
            continue
        ev = _parse_event(line)
        if ev is None:
            skipped += 1  # the fragment is still reported, recovered event or not
            ev = _recover_glued(line)
        if ev is not None:
            out.append(ev)
    return out, skipped


def _sorted_unique(events: list[Event]) -> list[Event]:
    """Lamport-sorted and de-duplicated by content address -- the reference definition.

    Merging two branches can bring the same event in twice via two shard copies, and a
    union must be idempotent.
    """
    seen: set[str] = set()
    uniq: list[Event] = []
    for e in sorted(events, key=Event.sort_key):
        eid = e.id or e.compute_id()
        if eid in seen:
            continue
        seen.add(eid)
        uniq.append(e)
    return uniq


def _toplevel(path: str) -> str:
    r = G.run(path, "rev-parse", "--show-toplevel", timeout=G.PROBE_TIMEOUT)
    return r.out if r.ok else ""


def _common_dir(path: str) -> str:
    r = G.run(path, "rev-parse", "--git-common-dir", timeout=G.PROBE_TIMEOUT)
    if not r.ok or not r.out:
        return ""
    p = Path(r.out)
    return str((Path(path) / p).resolve() if not p.is_absolute() else p.resolve())


def default_agent_id(fallback_root: Path | str | None = None) -> str:
    """A stable identity for "the agent working here".

    It used to be ``{host}-{pid}``, which is stable for exactly one process — so
    `ddflow claim` and the `git commit` hook that ran seconds later were different
    agents. The hook then refused the holder's own commit **and told them their lease
    belonged to somebody else**. Out of the box, the enforcement layer rejected correct
    behaviour and blamed the user for it.

    The working model is **one agent per worktree**, so the worktree is the identity:

    * inside a worktree of the managed repository -> that worktree's name, so two
      parallel agents differ while every process inside one worktree agrees;
    * anywhere else -> the managed repository's own name, so a command run with
      ``--repo X`` from an unrelated directory still lands on X's identity rather than
      on whatever happened to be the shell's cwd.

    ``DDFLOW_AGENT`` overrides both, and a harness running several agents inside ONE
    tree must set it — there is no signal that can distinguish them otherwise.

    Host and tree name are not unique ACROSS CLONES: two machines both called `ubuntu`
    with the repository checked out as `ddflow` both derived `ubuntu-ddflow`, wrote one
    shard between them, and `merge=union` then interleaved two writers' clocks in it
    (B190). So once the project is adopted the id also carries this clone's random
    suffix (:func:`_clone_suffix`) — shared by every worktree of the clone, because the
    suffix lives in the primary checkout, so the claim and the commit hook still agree.
    """
    root = str(fallback_root or "")
    key = f"{os.getcwd()}|{root}"
    if key in _AGENT_ID_CACHE:
        return _AGENT_ID_CACHE[key]
    ident = bare_agent_id(fallback_root)
    suffix = _clone_suffix(Path(root)) if root else ""
    if not suffix:
        # Not cached: an unadopted repo gains its suffix at `ddflow init`, and a
        # long-lived MCP server must pick that up rather than keep the bare name.
        return ident
    ident = f"{ident}-{suffix}"
    _AGENT_ID_CACHE[key] = ident
    return ident


def bare_agent_id(fallback_root: Path | str | None = None) -> str:
    """The derived id WITHOUT this clone's suffix: `{host}-{tree}`, which is what every
    derived id was before B190, and so the holder of any lease claimed before it."""
    host = _host()
    root = str(fallback_root or "")
    name = ""
    here = _toplevel(os.getcwd())
    # Only trust the cwd when it belongs to the SAME repository we are managing;
    # otherwise `ddflow --repo /elsewhere` run from another checkout would take that
    # checkout's identity, and the hook in /elsewhere would disagree with it.
    if here and (not root or _common_dir(here) == _common_dir(root)):
        name = Path(here).name
    elif root:
        name = Path(root).name
    if not name:
        try:
            name = getpass.getuser()
        except Exception:
            name = "agent"
    return f"{host}-{name}"


def _host() -> str:
    return socket.gethostname().split(".")[0]


def tree_agent_ids(tree: Path | str, root: Path | str) -> set[str]:
    """The identities an agent standing in the linked worktree ``tree`` of the clone at
    ``root`` derives for itself: `{host}-{tree name}`, bare and with this clone's suffix.

    What :func:`default_agent_id` answers from INSIDE ``tree``, asked from anywhere --
    so a command can tell whether the tree it was run from is some other identity's
    working tree. Never creates the suffix: an unadopted project has none to compare.
    """
    bare = f"{_host()}-{Path(tree).name}"
    path = Path(root) / CLONE_ID_FILE
    try:
        suffix = path.read_text("utf-8").strip()
    except OSError:
        suffix = ""
    return {bare, f"{bare}-{suffix}"} if suffix else {bare}


def clone_suffix_since(root: Path | str) -> float:
    """When this clone got its suffix (the clone-id file's mtime; it is never rewritten),
    or 0.0 when it has none. Before that moment this clone derived the bare id.

    "Has a suffix" means what :func:`_clone_suffix` means by it -- the file has content --
    so an empty file is no suffix here either."""
    path = Path(root) / CLONE_ID_FILE
    try:
        return path.stat().st_mtime if path.read_text("utf-8").strip() else 0.0
    except OSError:
        return 0.0


#: Where a clone keeps its identity suffix. Under `.ddflow/local/`, which is gitignored:
#: a committed suffix would be every clone's suffix, which is the collision again.
CLONE_ID_FILE = Path(".ddflow") / "local" / "clone-id"


def _clone_suffix(root: Path) -> str:
    """This clone's random identity suffix, created on first use and never changed.

    `root` is the PRIMARY checkout (`worktree.repo_root`), so every worktree of one
    clone reads the same file. "" when the project is not adopted — deriving an id must
    not create `.ddflow/` in someone's repository, or the "is this adopted?" check
    answers yes about itself — and "" when the file cannot be written.

    Created by hard-linking a fully written temp file into place, so two processes
    racing to create it agree on ONE value and neither can read a half-written one.
    The directory gets its own `.gitignore`: a project whose `.ddflow/.gitignore`
    predates `local/` must not commit the suffix and hand it to every clone.
    """
    path = root / CLONE_ID_FILE
    if not path.parent.parent.is_dir():
        return ""
    with contextlib.suppress(FileNotFoundError):
        return path.read_text("utf-8").strip()
    try:
        fsio.ensure_ignored_dir(path.parent)
        # Exclusive: the first writer wins and the rest read its value. Where there are no
        # hard links (some FUSE and SMB mounts) a reader racing it may see it empty for an
        # instant, and an empty read is not cached, so that caller simply asks again.
        with contextlib.suppress(FileExistsError):
            fsio.atomic_write(path, secrets.token_hex(3) + "\n", exclusive=True)
        return path.read_text("utf-8").strip()
    except OSError:
        return ""


def resolve_agent_id(root: Path | str, cfg: Any = None, declared: str = "") -> tuple[str, str]:
    """(identity, WHICH LAYER produced it). See :func:`effective_agent_id`.

    The layer is returned rather than inferred, because inferring it by comparing the
    result against each candidate is wrong whenever two candidates agree: with
    `DDFLOW_AGENT` unset and no `[agent].id`, the derived name differs from
    `cfg.agent.id` (`""`), so a value-comparison recorded the source as `env` and
    `ddflow config --explain` told an operator the environment was responsible for a
    variable nothing had set. An operator debugging identity is precisely the person
    who cannot afford that.
    """
    if declared:
        return declared, "explicit"
    env = os.environ.get("DDFLOW_AGENT", "")
    if cfg is not None:
        if env and getattr(cfg, "sources", {}).get("agent.id", "default") == "default":
            return env, "env"
        if getattr(getattr(cfg, "agent", None), "id", ""):
            return cfg.agent.id, "config"
    elif env:
        return env, "env"
    return default_agent_id(root), "derived"


def effective_agent_id(root: Path | str, cfg: Any = None, declared: str = "") -> str:
    """The identity a write will actually carry, resolved in ONE place.

    There are four layers -- an explicit declaration (`--agent`, or `ddflow_identify`
    on an MCP connection), `DDFLOW_AGENT`, `[agent].id` in config, and the tree-derived
    default -- and until this function existed, only `cli.Ctx.__init__` knew all four.

    The typed MCP path did not go through `Ctx`. It called `EventLog(repo, "")`, which
    falls straight to `default_agent_id()` and reads neither the env var nor the
    config. So with `DDFLOW_AGENT=alpha` set -- which the demo harnesses do --
    `ddflow_claim` wrote as `alpha` down the argv path while `ddflow_update` wrote as
    the tree name down the typed one, into a different shard, on the same connection.
    `ddflow_identify` with no argument reported the tree name too, so the one tool whose
    job is to make identity visible misreported it.

    Two encodings of one precedence is the duplicate-then-drift shape; the fix is one
    encoding, called from both. `cfg` is optional so callers below the config layer can
    still ask.
    """
    return resolve_agent_id(root, cfg, declared)[0]


#: Transaction depth per (pid, lock path), NOT per EventLog instance.
#: `fcntl.flock` is per-open-file-description, so a second `os.open` of the same file
#: in the same process blocks forever against the lock this process already holds. Two
#: EventLog objects for one repo in one process is entirely reasonable and happens
#: today, so the guard has to be keyed on what actually identifies the lock.
_HELD: dict[tuple[int, str], int] = {}


def _held_key(path: Path) -> tuple[int, str]:
    return (os.getpid(), str(path))


def _holder_note(path: Path) -> str:
    """Who holds the flock on `path` right now, from the kernel; '' when it cannot say.

    flock cannot say who owns a lock, but Linux lists every flock in /proc/locks with the
    holder's pid and the file's device and inode. Asking the kernel costs the hot path
    nothing and writes nothing: an earlier design had each holder write a note INTO the
    lock file, which put per-writer bytes into a file that an un-adopted project commits
    (no `.ddflow/.gitignore` yet), where two clones' notes conflict on merge.

    Best effort and never raises: this runs while a TimeoutError is being built. Other
    platforms (no /proc/locks) and a pid from another namespace give ''.
    """
    try:
        st = os.stat(path)
        want = f"{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}:{st.st_ino}"
        for line in Path("/proc/locks").read_text().splitlines():
            f = line.split()
            # `1: FLOCK ADVISORY WRITE 4242 fd:00:131077 0 EOF`.
            if any("->" in tok for tok in f[:2]):
                continue  # a blocked waiter (the kernel prefixes it), never the holder
            if len(f) >= _LOCK_FIELDS and f[1] == "FLOCK" and f[5] == want:
                pid = int(f[4])
                if pid > 0:
                    return f" Held by {_describe_pid(pid)}."
    except (OSError, ValueError):
        return ""
    return ""


#: Fields on a /proc/locks line up to and including the `major:minor:inode` column.
_LOCK_FIELDS = 6


#: How much of a holder's command line a lock-timeout message shows: its first
#: _CMD_SHOWN characters, as always, and past that its last _CMD_TAIL.
_CMD_SHOWN = 200
_CMD_TAIL = 60
#: An interpreter whose first argument is the script it runs: `python3`, `python3.13`, and
#: the build-suffixed `python3.13t` (free-threaded), `python3.13d`, `python3-dbg`.
_PYTHON = re.compile(r"python[0-9.]*[a-z]?(-dbg)?")


def _describe_pid(pid: int) -> str:
    """`pid N (command line)` -- the command from /proc, or just the pid.

    The interpreter (and a script it runs) is shown by its base name: a venv under a deep
    directory filled the whole budget with its path, cutting off the arguments that say
    WHAT holds the lock (bug B3a4bf051b4). A line still too long keeps its first
    _CMD_SHOWN characters and gains its tail, so nothing shown before is lost.
    """
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return f"pid {pid}"
    argv = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
    if argv:
        argv[0] = os.path.basename(argv[0]) or argv[0]
    if len(argv) > 1 and _PYTHON.fullmatch(argv[0]) and argv[1].startswith("/"):
        argv[1] = os.path.basename(argv[1]) or argv[1]  # the script it runs, never an argument
    cmd = " ".join(argv).strip()
    if len(cmd) > _CMD_SHOWN + _CMD_TAIL + 3:
        cmd = f"{cmd[:_CMD_SHOWN]}...{cmd[-_CMD_TAIL:]}"
    return f"pid {pid} ({cmd})" if cmd else f"pid {pid}"


@contextlib.contextmanager
def _flock(path: Path, timeout_s: float) -> Iterator[None]:
    """Exclusive advisory lock, with a bounded wait and a real error on timeout.

    The lock file is created once and NEVER atomically replaced: renaming over a lock
    file puts two holders on two different inodes, each believing it is exclusive. It is
    also never WRITTEN: its content is empty, so a project that commits it (before
    `.ddflow/.gitignore` exists) cannot get a merge conflict from it.

    A timeout says how long this process waited and, on Linux, who holds the lock (B184:
    a timeout under machine load was suspected and could not be told from a wedged agent).
    """
    started = time.monotonic()
    with contextlib.ExitStack() as held:
        try:  # only the acquisition: a timeout raised inside the block is not ours
            held.enter_context(fsio.file_lock(path, timeout_s, poll_s=0.02))
        except fsio.LockTimeout:
            raise TimeoutError(
                f"could not acquire {path} within {timeout_s}s "
                f"(waited {time.monotonic() - started:.1f}s, this is pid {os.getpid()})."
                f"{_holder_note(path)} Another agent may be wedged, or the machine is "
                f"overloaded -- check `ddflow doctor`"
            ) from None
        yield


class EventLog:
    """Sharded append-only log rooted at ``<root>/.ddflow/events``.

    One ``<agent-id>.jsonl`` per agent. Readers take no lock: a torn final line is
    possible in principle on a crash mid-append, so ``read_all`` skips unparseable
    lines and counts them, rather than failing the whole system for one bad byte.
    A torn line is reported by ``ddflow doctor``.
    """

    def __init__(
        self,
        root: Path,
        agent_id: str = "",
        *,
        lock_timeout_s: float = 30.0,
        log_cfg: LogConfig | None = None,
        cache_writes: bool = True,
    ) -> None:
        # Defaults come FROM the dataclass rather than being repeated here, so the
        # documented default and the effective one cannot drift.
        self.log_cfg = log_cfg or LogConfig()
        #: False for a log built only to READ someone else's repository (a sibling project
        #: in `external.sync`, an export): reading it must not leave a snapshot in it.
        self.cache_writes = cache_writes
        self.root = Path(root)
        self.dir = self.root / ".ddflow" / "events"
        # Derived on first USE, not here (bug B244aeaad5c): deriving can create this
        # clone's `.ddflow/local/clone-id`, and a log built only to be read -- a sibling
        # project's, in `external.sync` -- must not write into that repository.
        self._agent_id = agent_id
        self.lock_path = self.root / ".ddflow" / "events.lock"
        self.lock_timeout_s = lock_timeout_s
        # NOT created here. Merely constructing a log -- which happens on every CLI
        # invocation and every MCP handshake -- must not leave a directory behind in
        # someone's repository. It also made the "is this project adopted?" check lie:
        # the server created `.ddflow/events/` while answering the handshake, so the
        # next question about whether `.ddflow` existed answered yes about itself.
        # The directory is created on the first append instead.
        self._lamport = 0
        self._redactor: Any = None
        self.skipped_lines = 0

    @property
    def agent_id(self) -> str:
        if not self._agent_id:
            self._agent_id = default_agent_id(self.root)
        return self._agent_id

    #: Stamp this version into the log and apply the skew guard on append. Class-level and
    #: on by default; only the storage-mechanics tests (event counts, byte offsets) turn it
    #: off, so a stamp does not have to be threaded through every assertion about the log.
    stamp: bool = True

    # -- shard paths ---------------------------------------------------------------
    @property
    def shard(self) -> Path:
        safe = safe_filename(self.agent_id, repl="_", unicode=True)
        return self.dir / f"{safe}.jsonl"

    def shards(self) -> list[Path]:
        if not self.dir.is_dir():
            return []
        return sorted(self.dir.glob("*.jsonl"))

    def mark(self, *, clock: bool = True) -> LogMark:
        """A cheap fingerprint of how much log there IS: per shard, its size and (with
        `clock`) the clock on its last line. `os.stat`, plus one tail read per shard when
        the clock is asked for; never a parse.

        One mark answers "has anything changed?" for every caller that asks it: a
        decision folded outside the append lock and re-proved under it
        (`decide_then_append`), the wait loop's poll, and the index's staleness check.
        It replaces `extent()` (sizes only) and `head()` (three totals), which each missed
        a change the other saw: totals cannot see one shard shrink while another grows by
        the same amount, and sizes ignore the clock.

        `clock=False` is `extent()`'s price -- a `stat` per shard, the clock recorded as 0
        -- for the paths that run under the append lock or poll: shards are append-only,
        so a size says they changed, and a tail read per shard there is ~36x dearer on
        NFS (B4). Only the index needs the clock (`Store.stale`). Compare marks taken
        with the same `clock`.

        **Take it BEFORE the read, never after.** After is unsafe in a way that looks
        fine: a write landing between the read and the mark is recorded in it, so the
        later comparison says "unchanged" while the folded state is missing that event.
        Before, the same write makes the marks differ and the caller re-reads -- the
        conservative direction, the only safe one.

        Sizes rather than mtimes (granularity is a second on some filesystems, and two
        appends inside one second are the case this must catch). A new shard changes the
        key set. **One pass, size and tail adjacent per shard**: two walks let an append
        land between them with a clock at or below the current high and move neither
        (B54). A rewrite in place that keeps the size and the last clock is not seen
        here; only the prefix digest of `_read_delta` catches that.
        """
        out: list[tuple[str, int, int]] = []
        for p in self.shards():
            high = 0
            try:
                size = p.stat().st_size
            except OSError:
                # Vanished between the glob and the stat: absent from the mark, so the
                # comparison differs and the caller takes the safe path.
                continue
            try:
                tail = _last_line(p) if clock else ""
            except FileNotFoundError:
                continue  # vanished between the stat and the tail read: the same
            # Any other OSError (a shard that cannot be READ) propagates: the callers
            # that ask for the clock turn it into "could not run", never into "unchanged".
            if tail:
                try:
                    high = int(json.loads(tail).get("lamport", 0))
                except (ValueError, TypeError, AttributeError):
                    high = 0
            out.append((p.name, size, high))
        return LogMark(tuple(out))

    @contextlib.contextmanager
    def decide_then_append(self, decide: Callable[[], T]) -> Iterator[T]:
        """Decide from a read made OUTSIDE the append lock, then hold the lock for the write.

        Yields what `decide()` returned, with the lock held, for the caller to append
        under. The invariant is not "the read happened inside the lock" but "the state the
        decision is made from reflects every event at the moment of the append". Holding
        the lock across the read is one way to get it; proving, under the lock, that the
        log did not change is another, and it holds the lock for a handful of `stat` calls
        (`mark(clock=False)`) instead of a read of every shard (~36x dearer on NFS). When
        the mark HAS changed `decide()` runs again inside the lock, which is the old
        behaviour exactly -- so `decide` must be safe to call twice, and must take any
        clock it uses afresh.
        """
        before = self.mark(clock=False)
        decided = decide()
        with self.transaction():
            if self.mark(clock=False) != before:
                decided = decide()
            yield decided

    # -- transactions ---------------------------------------------------------------
    @contextlib.contextmanager
    def transaction(self) -> Iterator[EventLog]:
        """Hold the append lock across a read-decide-append sequence.

        Lease acquisition is check-then-act: "is anyone holding this? no -> claim it".
        Without a lock spanning BOTH halves, two agents both read "free" and both
        claim, which is the exact race this whole coordination layer exists to stop.
        Re-entrant by depth count, keyed on (pid, lock path) rather than on this object,
        because flock is per-open-file-description: a SECOND EventLog instance in the
        same process would otherwise deadlock against the lock the first one holds.
        """
        key = _held_key(self.lock_path)
        if _HELD.get(key, 0):
            _HELD[key] += 1
            try:
                yield self
            finally:
                _HELD[key] -= 1
            return
        with _flock(self.lock_path, self.lock_timeout_s):
            _HELD[key] = 1
            try:
                yield self
            finally:
                _HELD.pop(key, None)

    # -- writing -------------------------------------------------------------------
    def append(
        self,
        kind: str,
        subject: str,
        data: dict[str, Any] | None = None,
        *,
        observed: Iterable[Event] = (),
    ) -> Event:
        """Append one event. Returns it with id and lamport filled in.

        ``observed`` lets a caller declare events it has seen but not folded, so the
        Lamport clock advances past them even when the caller read a filtered view.
        """
        if kind not in known_kinds():
            raise ValueError(
                f"unknown event kind {kind!r}; add a handler to model.HANDLERS deliberately"
            )
        ctx = (
            contextlib.nullcontext()
            if _HELD.get(_held_key(self.lock_path), 0)
            else _flock(self.lock_path, self.lock_timeout_s)
        )
        with ctx:
            payload = dict(data or {})
            if kind not in _STAMP_EXEMPT and self.stamp:
                # The version stamp and the skew guard, in the same lock as the write: a
                # refusal must not be raced past, and the stamp must precede the event
                # that caused it (decisions D-upgrade-event-kinds, D-upgrade-skew-guard).
                payload.update(self._stamp_and_guard())
            return self._write(kind, subject, payload, observed)

    def _write(
        self, kind: str, subject: str, data: dict[str, Any], observed: Iterable[Event] = ()
    ) -> Event:
        """Write one event. The caller holds the lock."""
        # The kind's payload version (`core.upcasters`), here where EVERY write passes --
        # the version stamp and the skew override call this directly. Absent at version 1,
        # so the bytes written are unchanged until a kind's shape changes.
        data = UP.stamp(kind, data)
        # The log is committed: free text goes through the `log` redaction profile HERE,
        # where every write passes, declared per kind (D-unify 6, bug B5deba76d04).
        data = self._redact_data(kind, data)
        # Re-read inside the lock: another agent may have advanced the clock.
        high = self._highest_lamport()
        for e in observed:
            high = max(high, e.lamport)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lamport = max(self._lamport, high) + 1
        ev = Event(
            kind=kind,
            subject=subject,
            data=data,
            agent=self.agent_id,
            lamport=self._lamport,
            ts=utcnow(),
        )
        ev = Event(**{**ev.__dict__, "id": ev.compute_id()})
        line = (ev.to_json() + "\n").encode("utf-8")
        fd = os.open(self.shard, os.O_CREAT | os.O_RDWR | os.O_APPEND, 0o644)
        try:
            # A shard that does not end in a newline holds a torn append from a writer
            # that died mid-line (every writer holds this lock, so none is mid-line now).
            # Terminate it in the SAME write: the fragment stays its own unreadable line,
            # which doctor reports, and this event gets a line of its own instead of being
            # glued onto the fragment and lost with it (B28b3839fe6).
            size = os.fstat(fd).st_size
            if size and os.pread(fd, 1, size - 1) != b"\n":
                line = b"\n" + line
            view = memoryview(line)
            while view:  # a short write is legal; finish the line rather than tear it
                view = view[os.write(fd, view) :]
            os.fsync(fd)
        finally:
            os.close(fd)
        return ev

    def _redact_data(self, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        """``data`` with the free-text fields of ``kind`` redacted (`core.redact.LOG_TEXT_FIELDS`).

        The redactor is built on first need from the project's session patterns and the
        machine's own hostname and home. An unreadable config falls back to the built-in
        patterns; a bad pattern still raises, since this is a security control."""
        red = self._redactor
        if red is None:
            names: list[str] = []
            cached = True
            try:
                cfg = Config.load(self.root)
                patterns = [*cfg.session.redact_patterns, *cfg.session.redact_extra]
                # `[upstream].redact_extra`, once that section exists (`names_for`)
                up = getattr(cfg, "upstream", None)
                names = [str(n) for n in (getattr(up, "redact_extra", None) or [])]
            except Exception:
                # an unreadable config: the built-in patterns for THIS write, and the config is
                # read again on the next one, so a fixed config is not shadowed by the fallback
                patterns, cached = [*SessionConfig().redact_patterns], False
            try:
                host = socket.gethostname()
            except OSError:
                host = ""
            # the `log` profile's machine-local inputs, as `services.redact_report.redactor`
            # resolves them: this machine's hostname and $HOME, no repo root
            red = R.Redactor(
                "log",
                secret_patterns=patterns,
                names=names,
                hostname=host,
                home=os.path.expanduser("~"),
            )
            if cached:
                self._redactor = red
        return R.redact_event_data(kind, data, red)

    # -- the version stamp and the skew guard -----------------------------------------
    def _all_events(self) -> Iterator[Event]:
        """Every parsed event, unsorted and not de-duplicated: the stamp facts do not need
        order, and sorting the whole log on each append would cost more than the append."""
        for p in self.shards():
            yield from self._read_shard(p)[0]

    def _stamp_and_guard(self) -> dict[str, Any]:
        """Refuse an older ddflow's write to a newer log, stamp this version on first use.

        Returns extra `data` for the event about to be written: the older-version mark when
        a session-scoped override is what lets it through. Raises `SkewRefused` otherwise.
        Caller holds the lock."""
        version = running_version()
        facts = stamp_facts(self._all_events(), self.agent_id, version)
        extra: dict[str, Any] = {}
        if facts.skewed:
            policy = self._skew_policy()
            if policy == "refuse":
                if facts.override is None:
                    raise SkewRefused(skew_message(version, facts.highest, facts.highest_by))
                extra[OLDER_MARK] = version
            elif policy == "warn":
                _warn_skew_once(version, facts.highest)
        if not facts.seen_by_me and version_known(version):
            self._write(SEEN_KIND, "ddflow", {"version": version, "install": install_kind()})
            _write_seen_marker(self.root, version)
        return extra

    def _skew_policy(self) -> str:
        """`[upgrade].skew`: refuse (default) | warn | off. Read only when a skew is actually
        found, so the common append never loads the config for it."""
        try:
            from ..config import Config

            return str(Config.load(self.root).upgrade.skew)
        except Exception:
            return "refuse"

    def override_skew(self, reason: str) -> Event | None:
        """Record a session-scoped override: let this agent's open session write although
        the running ddflow is OLDER than the log's highest stamp (`skew.overridden`).

        Returns the event, or None when there is nothing to override: no skew, or a policy
        (`warn`, `off`) that never refuses. A reason is required otherwise: refusing once and
        then overriding silently would only be a slower way of not asking."""
        reason = (reason or "").strip()
        with self.transaction():
            version = running_version()
            facts = stamp_facts(self._all_events(), self.agent_id, version)
            if not facts.skewed:
                return None
            if facts.override is not None:
                return facts.override
            if self._skew_policy() != "refuse":
                return None  # warn/off never refuse, so there is nothing to override
            if not reason:
                raise SkewRefused(
                    f"--allow-older-version needs --reason: say why this session may write "
                    f"with ddflow {version} to a log ddflow {facts.highest} has worked on "
                    f"(ask the operator first)."
                )
            return self._write(
                SKEW_OVERRIDDEN_KIND,
                facts.session or "ddflow",
                {
                    "running": version,
                    "log_version": facts.highest,
                    "session": facts.session,
                    "reason": reason,
                },
            )

    def _highest_lamport(self) -> int:
        """The highest clock value in ANY event of any shard.

        It used to read only each shard's last line, on the reasoning that a shard has
        one writer so its tail carries its maximum. Two clones sharing an agent id break
        that, and `merge=union` puts the other clone's suffix AFTER ours: the merged
        shard read [1, 2, 3, 4, 2], the next append got 3, and `update --title v5`
        folded before the v4 it was written after (bug B28589b8b26). Nothing on disk
        says a shard had two writers, so the tail cannot be trusted for any shard.

        Reading every event is affordable because `_read_shard` re-parses only what was
        appended since the last read: measured 7.5 ms per append at 20,000 events warm,
        against 0.04 ms for the tail read — and an append is almost always preceded by a
        fold that warmed the cache.
        """
        high = 0
        for p in self.shards():
            for e in self._read_shard(p)[0]:
                high = max(high, e.lamport)
        return high

    def clock_regressions(self) -> dict[str, tuple[int, int, int]]:
        """Shards whose clock goes BACKWARDS in file order: name -> (event number, the
        value before, the value after), for the first decrease in each.

        One writer only ever increases its clock, so a decrease means two writers shared
        the shard -- two clones resolving to one agent id, merged with `merge=union`.
        The clock copes with it now; the shared identity is still worth fixing, since a
        lease held under it is "mine" in both clones.
        """
        out: dict[str, tuple[int, int, int]] = {}
        for p in self.shards():
            prev = 0
            for n, e in enumerate(self._read_shard(p)[0], 1):
                if e.lamport < prev:
                    out[p.name] = (n, prev, e.lamport)
                    break
                prev = e.lamport
        return out

    # -- reading -------------------------------------------------------------------
    def _read_shard(self, path: Path) -> tuple[list[Event], int]:
        """One shard's events (a fresh list) and its unparseable-line count."""
        d = self._read_delta(path)
        if d is None:
            return [], 0
        return d.whole + d.torn, d.skipped

    def _read_delta(self, path: Path, trust_snapshot: bool = True) -> _Delta | None:
        """One shard, re-parsing only what has been APPENDED since last time.

        Parsing dominates a read — 97 ms of 115 ms at 20,000 events — and an
        append-only file guarantees the bytes already parsed have not changed, so the
        tail is the only new work. The guarantee is CHECKED rather than assumed: the
        consumed prefix is re-hashed on every read and any mismatch falls back to a
        full parse. A rewrite, a truncation, a `git checkout`, a `git merge` and a
        delete-and-recreate all land in that one case, so there is no list of mechanisms
        to keep current.

        A trailing fragment with no newline is parsed (so `doctor` keeps reporting a
        torn append) but never marked consumed, so the next call re-reads it and sees
        the completed line.
        """
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            # Vanished between the glob and the read -- another agent's shard removed, or
            # a branch switch. Benign, and the only OSError that is: anything else means
            # the file is THERE and we cannot read it (permissions, a stale NFS handle,
            # a directory where a shard should be), and swallowing that would drop a
            # whole agent's events from the queue while reporting success. Caught by the
            # cross-family critic: the pre-cache code caught `FileNotFoundError` only,
            # and widening it to `OSError` turned a loud failure into a silent one.
            return None
        cached = _PARSE_CACHE.get(path) if self.log_cfg.reuse_parsed else None
        if cached is not None and cached.seeded and not trust_snapshot:
            cached = None  # `verify` re-parses from the bytes: it must not trust a cache
        start, skipped = 0, 0
        hasher = None
        base: _Parsed | None = None
        if cached is None and trust_snapshot and self.log_cfg.reuse_parsed:
            seeded = self._seed_from_snapshot(path, data)
            if seeded is not None:
                base, hasher = seeded
                start, skipped = base.consumed, base.skipped
        elif cached is not None and len(data) >= cached.consumed:
            # The ONLY thing that licenses re-using a parse: those exact bytes are still
            # there. Not the inode, not the size, not the mtime -- all three are proxies,
            # and the inode proxy was wrong about the case it was written for.
            view = memoryview(data)  # slicing bytes would COPY the whole prefix
            h = D.hasher(view[: cached.consumed])
            if h.hexdigest() == cached.digest:
                start, skipped = cached.consumed, cached.skipped
                base = cached
                # Kept so the digest STORED below continues this one rather than making a
                # second pass over the same prefix. `hexdigest()` does not finalise a
                # hashlib object, so it can still be updated with the appended bytes.
                hasher = h
        chunk = data[start:]
        cut = chunk.rfind(b"\n") + 1  # 0 when the tail holds no newline at all
        whole, fragment = chunk[:cut], chunk[cut:]
        fresh, fresh_skipped = _parse_lines(whole)
        skipped += fresh_skipped
        prior = len(base.events) if base is not None else 0
        # The ceiling is GLOBAL -- every shard of every repo this process has read. It is
        # documented as a memory ceiling, and a per-shard limit is not one: a repo with
        # eight agents would hold eight times the promised bound.
        held = _cached_events() - len(cached.events if cached is not None else ())
        parsed: _Parsed | None = None
        if (
            self.log_cfg.reuse_parsed
            and held + prior + len(fresh) <= self.log_cfg.max_cached_events
        ):
            if hasher is None:
                digest = _digest(data[: start + cut])
            else:
                hasher.update(whole)
                digest = hasher.hexdigest()
            if base is not None:
                base.events.extend(fresh)
                base.consumed, base.digest, base.skipped = start + cut, digest, skipped
                parsed = base
            else:
                parsed = _Parsed(start + cut, digest, fresh, skipped, gen=object())
            _PARSE_CACHE[path] = parsed
            events = parsed.events
        else:
            # Over the ceiling (or disabled): do not hold it, and do not leave a STALE
            # entry behind either -- an entry left behind would be served forever.
            _PARSE_CACHE.pop(path, None)
            events = (base.events + fresh) if base is not None else fresh
        torn, torn_skipped = _parse_lines(fragment)
        return _Delta(parsed, events, torn, skipped + torn_skipped)

    # -- on-disk snapshot (B166) ----------------------------------------------------
    def _snapshot_path(self) -> Path:
        """`read-snapshot-<fingerprint>.bin`: the fingerprint is the running version and the
        parser stamp, so code that cannot read another's snapshot never shares its file."""
        stem = SNAPSHOT_FILE.removesuffix(".bin")
        key = D.content_digest(f"{running_version()}/{_parser_stamp()}".encode())[:16]
        return self.dir.parent / "local" / f"{stem}-{key}.bin"

    def _touch_snapshot(self) -> None:
        """Mark the snapshot as used (at most daily): reads never write it, so without this its
        mtime says when it was written and a sibling checkout on other code would collect a
        long-lived reader's snapshot as abandoned."""
        path = self._snapshot_path()
        with contextlib.suppress(OSError):
            if time.time() - path.stat().st_mtime > SNAPSHOT_TOUCH_S:
                os.utime(path)

    def _collect_stale_snapshots(self, mine: Path) -> None:
        """Remove the snapshots of other code untouched for `SNAPSHOT_STALE_DAYS`, and the
        pre-fingerprint `read-snapshot.bin` once it is that old. Best effort."""
        stem = SNAPSHOT_FILE.removesuffix(".bin")
        cutoff = time.time() - SNAPSHOT_STALE_DAYS * 86400
        for f in mine.parent.glob(f"{stem}*.bin"):
            if f == mine or not re.fullmatch(rf"{re.escape(stem)}(-[0-9a-f]{{16}})?\.bin", f.name):
                continue
            with contextlib.suppress(OSError):
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()

    def _snapshot_enabled(self) -> bool:
        # A log built only to read someone else's repository (`cache_writes=False`) neither
        # writes NOR reads a snapshot there: what is in another checkout's `.ddflow/local/`
        # is that checkout's own state, not something to decode into this process.
        return (
            self.log_cfg.reuse_parsed and self.cache_writes and os.environ.get(SNAPSHOT_ENV) != "0"
        )

    def _load_snapshot(self) -> dict[str, tuple]:
        """The snapshot's per-shard entries, or `{}` -- never an exception, never a guess.

        Trusted only when ALL of: the header parses; its format, ddflow version and Event
        field list are the ones running; the payload's length and sha256 match the header
        (bit rot, truncation, a half-written file); and the payload unmarshals into the
        expected shape. Any doubt is "no snapshot", and the caller parses the log. Even a
        snapshot that passes is only a CLAIM about shard prefixes: `_seed_from_snapshot`
        re-hashes the real bytes before using any of it.

        What that does NOT prove is that the decoded events are the ones those bytes
        parse to: the shard hashes bind the snapshot to the log, and its own checksum
        guards against rot and truncation, but a hand-crafted file with consistent
        checksums is believed. That is the same trust as the rest of `.ddflow/local/`
        (machine-local state in the operator's own checkout, never merged or fetched), which
        is why a read-only log skips snapshots entirely and `verify()` never uses one.
        `marshal` is used for speed and, like any file in your own checkout, is not a
        place to put untrusted bytes.
        """
        memo = _SNAPSHOTS.get(self.dir)
        if memo is not None:
            return memo
        entries: dict[str, tuple] = {}
        _SNAPSHOTS[self.dir] = entries
        _SNAP_COVERED[self.dir] = 0  # what is covered is what gets seeded from THIS file
        try:
            raw = self._snapshot_path().read_bytes()
            head, _, payload = raw.partition(b"\n")
            meta = json.loads(head)
            if (
                meta["format"] != SNAPSHOT_FORMAT
                or meta["version"] != running_version()
                or meta["fields"] != list(_EVENT_FIELDS)
                or meta["parser"] != _parser_stamp()
                or meta["size"] != len(payload)
                or meta["sha256"] != D.content_digest(payload)
            ):
                return entries
            # Only reached after the file's own sha256, size, ddflow version and parser
            # fingerprint matched (above): local machine state, never merged or fetched.
            self._touch_snapshot()
            body = marshal.loads(payload)  # nosec B302
            order = body.pop("\0order", None)
            for name, (consumed, digest, skipped, tuples) in body.items():
                if (
                    isinstance(name, str)
                    and isinstance(consumed, int)
                    and isinstance(digest, str)
                    and isinstance(skipped, int)
                    and isinstance(tuples, list)
                ):
                    entries[name] = (consumed, digest, skipped, tuples)
            if order is not None:
                names, idxs = order  # a malformed order poisons the whole snapshot
                _SNAP_ORDER[self.dir] = (list(names), list(idxs))
        except Exception:
            entries.clear()
            _SNAP_ORDER.pop(self.dir, None)
        return entries

    def _seed_from_snapshot(self, path: Path, data: bytes) -> tuple[_Parsed, Any] | None:
        """A `_Parsed` for this shard built from the snapshot, iff its bytes still match.

        Returns `(parsed, hasher)` with the hasher already holding the prefix, so the
        caller continues the digest over the appended tail instead of hashing twice.
        """
        if not self._snapshot_enabled():
            return None
        entry = self._load_snapshot().pop(path.name, None)
        if entry is None:
            return None
        consumed, digest, skipped, tuples = entry
        if not 0 <= consumed <= len(data):
            return None
        h = D.hasher(memoryview(data)[:consumed])
        if h.hexdigest() != digest:
            return None  # the shard is not the one the snapshot describes
        try:
            events = [Event(*t) for t in tuples]
        except Exception:
            return None
        _SNAP_COVERED[self.dir] = _SNAP_COVERED.get(self.dir, 0) + len(events)
        parsed = _Parsed(consumed, digest, events, skipped, object(), True, len(events))
        return parsed, h

    def _maybe_write_snapshot(
        self, deltas: list[tuple[Path, _Delta]], merged: _Merged | None
    ) -> None:
        """Persist the parse when enough has accumulated since the last snapshot.

        Best effort: a snapshot that cannot be written costs only the next cold read.
        Written atomically (temp file + rename) so a concurrent reader sees the old file
        or the new one, never half of either, and never from a read that left some shard
        uncached (a partial snapshot would simply be re-derived, but there is no point).
        """
        if (
            not self.cache_writes
            or not self._snapshot_enabled()
            or any(d.parsed is None for _, d in deltas)
        ):
            return
        total = sum(len(d.whole) for _, d in deltas)
        covered = _SNAP_COVERED.get(self.dir, 0)
        if total < SNAPSHOT_MIN_EVENTS or total - covered < max(1000, covered // 10):
            return
        body = {
            p.name: (
                d.parsed.consumed,
                d.parsed.digest,
                d.parsed.skipped,
                [_event_tuple(e) for e in d.whole],
            )
            for p, d in deltas
            if d.parsed is not None
        }
        if merged is not None and {p: n for p, (_, n) in merged.marks.items()} == {
            p: len(d.whole) for p, d in deltas
        }:
            # The sorted, de-duplicated order is derived data too, and sorting 100k events
            # is as costly as unmarshalling them: save it as indices into the shards'
            # concatenated events so a cold read can skip the sort.
            names = [(p.name, len(d.whole)) for p, d in deltas]
            where = {id(e): i for i, e in enumerate(e for _, d in deltas for e in d.whole)}
            body["\0order"] = (names, [where[id(e)] for e in merged.uniq])
        try:
            payload = marshal.dumps(body)
            meta = {
                "format": SNAPSHOT_FORMAT,
                "version": running_version(),
                "fields": list(_EVENT_FIELDS),
                "parser": _parser_stamp(),
                "size": len(payload),
                "sha256": D.content_digest(payload),
            }
            target = self._snapshot_path()
            fsio.ensure_ignored_dir(target.parent)
            # A cache: never torn for a reader, but no fsync -- a crash costs a cold read.
            fsio.atomic_write(target, json.dumps(meta).encode() + b"\n" + payload, fsync=False)
            _SNAP_COVERED[self.dir] = total
            self._collect_stale_snapshots(target)
        except (OSError, ValueError):
            pass

    def read_all(self, *, snapshot: bool = True) -> list[Event]:
        """Every event, Lamport-sorted and de-duplicated.

        `snapshot=False` re-derives everything from the log's bytes (what `verify` uses):
        the on-disk snapshot is neither read nor trusted for that call.
        """
        self.skipped_lines = 0
        live = self.shards()
        # A deleted shard's entry would otherwise live for the process lifetime: nothing
        # visits a path `shards()` no longer returns, so `_read_delta` never sees it. That
        # is a slow leak, and it is also a trap -- `rm -rf .ddflow && ddflow init` under a
        # running server can land a NEW shard on the SAME path, and the digest check would
        # then be the only thing standing between it and the old events.
        if self.log_cfg.reuse_parsed:
            for gone in [p for p in _PARSE_CACHE if p.parent == self.dir and p not in live]:
                del _PARSE_CACHE[gone]
        deltas: list[tuple[Path, _Delta]] = []
        for p in live:
            d = self._read_delta(p, snapshot)
            if d is None:
                continue
            deltas.append((p, d))
            self.skipped_lines += d.skipped
        torn = [e for _, d in deltas for e in d.torn]
        if not self.log_cfg.reuse_parsed or any(d.parsed is None for _, d in deltas):
            if self.log_cfg.reuse_parsed:
                # Over the memory ceiling: the maintained order would outlive its parse.
                # (A cache-disabled reader leaves it alone: it is validated against the
                # parse cache by lineage, so another reader's copy cannot go stale.)
                _MERGED.pop(self.dir, None)
            return _sorted_unique([e for _, d in deltas for e in d.whole] + torn)
        merged = self._merge(deltas)
        if snapshot:
            self._maybe_write_snapshot(deltas, merged)
        if not torn:
            return list(merged.uniq)
        # A torn fragment is transient and never part of the maintained order.
        return _sorted_unique(merged.uniq + torn)

    def _merged_from_snapshot(self, deltas: list[tuple[Path, _Delta]]) -> _Merged | None:
        """The snapshot's saved order, adopted only if it still describes these events.

        Every shard it names must have been seeded from THIS snapshot with exactly the
        count it was saved with; anything else (a shard rewritten, a shard gone) and the
        order is discarded and rebuilt from the events, the old way. Events appended since
        are not in the order: `_merge` folds them in as it would any append.
        """
        saved = _SNAP_ORDER.pop(self.dir, None)
        if saved is None or not self._snapshot_enabled():
            return None
        try:
            names, order = saved
            by_name = {p.name: (p, d) for p, d in deltas}
            base: list[Event] = []
            marks: dict[Path, tuple[object, int]] = {}
            for name, count in names:
                p, d = by_name[name]
                if d.parsed is None or not d.parsed.seeded or d.parsed.snap_n != count:
                    return None
                base.extend(d.whole[:count])
                marks[p] = (d.parsed.gen, count)
            if len(set(order)) != len(order) or (
                order and (min(order) < 0 or max(order) >= len(base))
            ):
                return None
            uniq = [base[i] for i in order]
            seen = {e.id or e.compute_id() for e in uniq}
            # The order is the one datum the shard hashes do not cover, so check it is what
            # a sort + de-dupe would have made: it holds exactly the distinct events of
            # these shards, and it is in key order.
            if seen != {e.id or e.compute_id() for e in base}:
                return None
            keys = [e.sort_key() for e in uniq]
            if any(a >= b for a, b in itertools.pairwise(keys)):
                return None
            return _Merged(marks, uniq, seen)
        except Exception:
            return None

    def _merge(self, deltas: list[tuple[Path, _Delta]]) -> _Merged:
        """Bring this directory's maintained order up to date with `deltas`.

        Incremental only when PROVABLY equivalent to a from-scratch sort and de-dupe:
        every shard already folded must still be the same byte lineage (`gen`), and no
        appended event may repeat an id already held (a duplicate shard copy, which the
        from-scratch pass resolves by sort order). Anything else rebuilds, which is the
        old behaviour exactly.
        """
        m = _MERGED.get(self.dir)
        if m is not None:
            _SNAP_ORDER.pop(self.dir, None)  # superseded: never adopt it later
        if m is None:
            m = self._merged_from_snapshot(deltas)
            if m is not None:
                _MERGED[self.dir] = m
        fresh: list[Event] = []
        ok = m is not None
        if m is not None:
            paths = {p for p, _ in deltas}
            ok = set(m.marks) <= paths
            for p, d in deltas:
                mark = m.marks.get(p)
                if mark is None:
                    fresh.extend(d.whole)
                elif d.parsed is None or mark[0] is not d.parsed.gen or mark[1] > len(d.whole):
                    ok = False
                    break
                else:
                    fresh.extend(d.whole[mark[1] :])
        if ok and m is not None and len(fresh) <= _MAX_INCREMENTAL:
            fresh.sort(key=Event.sort_key)
            add: list[Event] = []
            ids: set[str] = set()
            for e in fresh:
                eid = e.id or e.compute_id()
                if eid in m.seen:
                    ok = False
                    break
                if eid in ids:
                    continue
                ids.add(eid)
                add.append(e)
            if ok:
                uniq = m.uniq
                for e in add:
                    if not uniq or uniq[-1].sort_key() < e.sort_key():
                        uniq.append(e)
                    else:
                        bisect.insort(uniq, e, key=Event.sort_key)
                m.seen |= ids
                m.marks = {p: (d.parsed.gen, len(d.whole)) for p, d in deltas if d.parsed}
                return m
        whole = [e for _, d in deltas for e in d.whole]
        uniq = _sorted_unique(whole)
        m = _Merged(
            {p: (d.parsed.gen, len(d.whole)) for p, d in deltas if d.parsed},
            uniq,
            {e.id or e.compute_id() for e in uniq},
        )
        _MERGED[self.dir] = m
        return m

    def verify(self) -> list[str]:
        """Integrity check: every event's id must equal the hash of its body."""
        problems = []
        for e in self.read_all(snapshot=False):
            if e.id and e.id != e.compute_id():
                problems.append(
                    f"{e.id}: content does not match its address (edited after the fact?)"
                )
        if self.skipped_lines:
            problems.append(
                f"{self.skipped_lines} unparseable line(s) — likely a torn append after a crash"
            )
        return problems


def _last_line(path: Path) -> str:
    """Read the final non-empty line without reading the file.

    Seeks a small window from the end and grows it. Bounded at 64 KiB: an event larger
    than that is a bug elsewhere, and the bounded read keeps the clock cheap on NFS.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    if size == 0:
        return ""
    window = TAIL_WINDOW_BYTES
    with path.open("rb") as fh:
        while window <= TAIL_MAX_BYTES:
            fh.seek(max(0, size - window))
            chunk = fh.read()
            lines = [ln for ln in chunk.split(b"\n") if ln.strip()]
            if lines and (size <= window or len(lines) > 1):
                return lines[-1].decode("utf-8", "replace")
            window *= 2
    return ""
