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

import contextlib
import fcntl
import getpass
import hashlib
import json
import os
import secrets
import socket
import subprocess
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import LogConfig
from ..core.events import (
    PROVENANCE_KINDS,
    SCHEMA_VERSION,
    TAIL_MAX_BYTES,
    TAIL_WINDOW_BYTES,
    Event,
    canonical,
    utcnow,
)
from ..core.model import known_kinds
from . import proc as P

__all__ = [
    "PROVENANCE_KINDS",
    "SCHEMA_VERSION",
    "Event",
    "EventLog",
    "canonical",
    "clear_parse_cache",
    "default_agent_id",
    "effective_agent_id",
    "resolve_agent_id",
    "utcnow",
]

_AGENT_ID_CACHE: dict[str, str] = {}


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
    events: tuple[Event, ...]
    skipped: int


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


def clear_parse_cache() -> None:
    """Drop every parsed shard.

    For tests. NOT needed after a merge or a branch switch — the per-read digest check
    detects those, which is the point of making it a content check. Nothing in `ddflow`
    calls this, and that is the correct amount.
    """
    _PARSE_CACHE.clear()


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
    return hashlib.sha256(data).hexdigest()


def _parse_lines(chunk: bytes) -> tuple[list[Event], int]:
    """`(events, unparseable-line-count)` for a run of whole lines."""
    out: list[Event] = []
    skipped = 0
    for raw in chunk.decode("utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            out.append(Event.from_json(line))
        except (json.JSONDecodeError, KeyError):
            skipped += 1
    return out, skipped


def _toplevel(path: str) -> str:
    try:
        r = P.run(
            ["git", "-C", path, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def _common_dir(path: str) -> str:
    try:
        r = P.run(
            ["git", "-C", path, "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if r.returncode != 0 or not r.stdout.strip():
        return ""
    p = Path(r.stdout.strip())
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
    host = socket.gethostname().split(".")[0]
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


def clone_suffix_since(root: Path | str) -> float:
    """When this clone got its suffix (the clone-id file's mtime; it is never rewritten),
    or 0.0 when it has none. Before that moment this clone derived the bare id."""
    try:
        return (Path(root) / CLONE_ID_FILE).stat().st_mtime
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
        path.parent.mkdir(exist_ok=True)
        ignore = path.parent / ".gitignore"
        if not ignore.exists():
            ignore.write_text("*\n", "utf-8")
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}")
        tmp.write_text(secrets.token_hex(3) + "\n", "utf-8")
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass
        except OSError:
            # No hard links here (some FUSE and SMB mounts). An exclusive create still
            # picks ONE winner; a reader racing it may see it empty for an instant, and
            # an empty read is not cached, so that caller simply asks again.
            with contextlib.suppress(FileExistsError):
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                try:
                    os.write(fd, tmp.read_bytes())
                finally:
                    os.close(fd)
        finally:
            tmp.unlink()
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


@contextlib.contextmanager
def _flock(path: Path, timeout_s: float) -> Iterator[None]:
    """Exclusive advisory lock, with a bounded wait and a real error on timeout.

    The lock file is created once and NEVER atomically replaced: renaming over a lock
    file puts two holders on two different inodes, each believing it is exclusive.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"could not acquire {path} within {timeout_s}s; "
                        f"another agent may be wedged — check `ddflow doctor`"
                    ) from None
                time.sleep(0.02)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


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
    ) -> None:
        # Defaults come FROM the dataclass rather than being repeated here, so the
        # documented default and the effective one cannot drift.
        self.log_cfg = log_cfg or LogConfig()
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
        self.skipped_lines = 0

    @property
    def agent_id(self) -> str:
        if not self._agent_id:
            self._agent_id = default_agent_id(self.root)
        return self._agent_id

    # -- shard paths ---------------------------------------------------------------
    @property
    def shard(self) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in self.agent_id)
        return self.dir / f"{safe}.jsonl"

    def shards(self) -> list[Path]:
        if not self.dir.is_dir():
            return []
        return sorted(self.dir.glob("*.jsonl"))

    def extent(self) -> dict[str, int]:
        """A cheap fingerprint of how much log there IS: shard name -> size in bytes.

        `os.stat` per shard, no reads. Exists so a caller can fold the log OUTSIDE the
        append lock and then, inside it, PROVE that nothing was appended in between —
        which is the difference between holding the lock across an O(all-events) read and
        holding it across a handful of `stat` calls. On the NFS mount this package was
        designed against, that read is ~36x its local cost.

        **Take this BEFORE the read, never after.** After is unsafe in a way that looks
        fine: a write landing between the read and the fingerprint is recorded in the
        size, so the later comparison says "unchanged" while the folded state is missing
        that event. Before, the same write makes the sizes differ and the caller falls
        back to re-reading — conservative, and conservative is the only safe direction
        here.

        Sizes rather than mtimes: mtime granularity is one second on some filesystems,
        and two appends inside one second are exactly the case this has to catch. A new
        shard appearing (another agent's first write) changes the KEY set, so that is
        caught too.
        """
        out: dict[str, int] = {}
        for path in self.shards():
            try:
                out[path.name] = path.stat().st_size
            except OSError:
                # Vanished between the glob and the stat. Recording it as absent makes
                # the comparison differ, which sends the caller down the safe path.
                continue
        return out

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
            # Re-read inside the lock: another agent may have advanced the clock.
            high = self._highest_lamport()
            for e in observed:
                high = max(high, e.lamport)
            self.dir.mkdir(parents=True, exist_ok=True)
            self._lamport = max(self._lamport, high) + 1
            ev = Event(
                kind=kind,
                subject=subject,
                data=dict(data or {}),
                agent=self.agent_id,
                lamport=self._lamport,
                ts=utcnow(),
            )
            ev = Event(**{**ev.__dict__, "id": ev.compute_id()})
            line = ev.to_json() + "\n"
            fd = os.open(self.shard, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
            try:
                os.write(fd, line.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
        return ev

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

    def head(self) -> tuple[int, int, int]:
        """`(shards, highest_lamport, total_bytes)` — the cheap fingerprint of the log.

        O(shards): a stat per file and one tail read each, never a full parse. Exists
        so a caller can answer "has anything changed?" without answering "what is
        everything?" — `Store.stale` used to call `read_all()` to decide whether it
        needed to call `read_all()`, which on the NFS mount this was designed against
        cost ~36x the local read it was trying to avoid.

        Byte count is in the fingerprint as well as the Lamport high-water mark because
        two shards can be appended to concurrently: the highest Lamport can stay put
        while a second agent's shard grows, and an index that missed that would serve
        a stale answer while reporting itself current.

        **One pass, size and tail read adjacently per shard.** It used to stat every
        shard and then call `_highest_lamport()`, which walked them all again — so an
        append landing between the two walks, carrying a Lamport value at or below the
        current high, was invisible in BOTH components and the fingerprint came back
        byte-identical to the pre-append one. That is exactly the interleaving the byte
        count was added to catch, defeated by the read order. Reading a shard's size
        and its tail together makes the two components describe the same observation of
        that shard. (Raised THEORETICAL by the cross-family critic 2026-09-24; probe and
        regression test in `tests/test_rubber_duck_findings.py`.)

        The Lamport component is the highest TAIL, not the highest clock: after a union
        merge of a shard with two writers they differ (see `_highest_lamport`). That is
        harmless here -- the merge changed the byte count -- but do not take a clock
        from it.
        """
        total = 0
        shards = 0
        high = 0
        for p in self.shards():
            try:
                total += p.stat().st_size
                shards += 1
            except OSError:
                continue
            tail = _last_line(p)
            if not tail:
                continue
            try:
                high = max(high, int(json.loads(tail).get("lamport", 0)))
            except (ValueError, TypeError):
                continue
        return shards, high, total

    # -- reading -------------------------------------------------------------------
    def _read_shard(self, path: Path) -> tuple[list[Event], int]:
        """One shard's events, re-parsing only what has been APPENDED since last time.

        Parsing dominates a read — 97 ms of 115 ms at 20,000 events — and an
        append-only file guarantees the bytes already parsed have not changed, so the
        tail is the only new work. The guarantee is CHECKED rather than assumed: the
        consumed prefix is re-hashed on every read (measured: a 20,000-event warm read
        costs 11.9 ms against 121.1 ms uncached, ~10x) and any mismatch falls back to a
        full parse. A
        rewrite, a truncation, a `git checkout`, a `git merge` and a delete-and-recreate
        all land in that one case, so there is no list of mechanisms to keep current.

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
            return [], 0
        cached = _PARSE_CACHE.get(path) if self.log_cfg.reuse_parsed else None
        start, base, skipped = 0, [], 0
        hasher = None
        if cached is not None and len(data) >= cached.consumed:
            # The ONLY thing that licenses re-using a parse: those exact bytes are still
            # there. Not the inode, not the size, not the mtime -- all three are proxies,
            # and the inode proxy was wrong about the case it was written for.
            h = hashlib.sha256(data[: cached.consumed])
            if h.hexdigest() == cached.digest:
                start, base, skipped = cached.consumed, list(cached.events), cached.skipped
                # Kept so the digest STORED below continues this one rather than making a
                # second pass over the same prefix. `hexdigest()` does not finalise a
                # hashlib object, so it can still be updated with the appended bytes --
                # which makes the store side O(appended) like the parse beside it.
                hasher = h
        chunk = data[start:]
        cut = chunk.rfind(b"\n") + 1  # 0 when the tail holds no newline at all
        whole, fragment = chunk[:cut], chunk[cut:]
        fresh, fresh_skipped = _parse_lines(whole)
        events = base + fresh
        skipped += fresh_skipped
        # The ceiling is GLOBAL -- every shard of every repo this process has read. It is
        # documented as a memory ceiling, and a per-shard limit is not one: a repo with
        # eight agents would hold eight times the promised bound.
        held = _cached_events() - len(_PARSE_CACHE.get(path, _Parsed(0, "", (), 0)).events)
        if self.log_cfg.reuse_parsed and held + len(events) <= self.log_cfg.max_cached_events:
            if hasher is None:
                digest = _digest(data[: start + cut])
            else:
                hasher.update(whole)
                digest = hasher.hexdigest()
            _PARSE_CACHE[path] = _Parsed(start + cut, digest, tuple(events), skipped)
        else:
            # Over the ceiling (or disabled): do not hold it, and do not leave a STALE
            # entry behind either -- an entry left behind would be served forever.
            _PARSE_CACHE.pop(path, None)
        torn, torn_skipped = _parse_lines(fragment)
        return events + torn, skipped + torn_skipped

    def read_all(self) -> list[Event]:
        out: list[Event] = []
        self.skipped_lines = 0
        live = self.shards()
        # A deleted shard's entry would otherwise live for the process lifetime: nothing
        # visits a path `shards()` no longer returns, so `_read_shard` never sees it. That
        # is a slow leak, and it is also a trap -- `rm -rf .ddflow && ddflow init` under a
        # running server can land a NEW shard on the SAME path, and the digest check would
        # then be the only thing standing between it and the old events.
        if self.log_cfg.reuse_parsed:
            for gone in [p for p in _PARSE_CACHE if p.parent == self.dir and p not in live]:
                del _PARSE_CACHE[gone]
        for p in live:
            events, skipped = self._read_shard(p)
            out.extend(events)
            self.skipped_lines += skipped
        # De-duplicate by content address: merging two branches can bring the same
        # event in twice via two shard copies, and a union must be idempotent.
        seen: set[str] = set()
        uniq: list[Event] = []
        for e in sorted(out, key=lambda e: e.sort_key()):
            eid = e.id or e.compute_id()
            if eid in seen:
                continue
            seen.add(eid)
            uniq.append(e)
        return uniq

    def verify(self) -> list[str]:
        """Integrity check: every event's id must equal the hash of its body."""
        problems = []
        for e in self.read_all():
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
