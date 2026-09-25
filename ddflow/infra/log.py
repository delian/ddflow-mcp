"""The append-only log — the one place events are written and read.

Serialised appends under `fcntl.flock` with an `fsync`, per-agent shards so two agents
never write the same file, and a tail read for the Lamport clock so the cost of
appending is O(shards) rather than O(events).

The `Event` record itself lives in `core.events`: it is a value with no I/O, and the
domain layer needs it without needing this.
"""

from __future__ import annotations

import contextlib
import fcntl
import getpass
import json
import os
import socket
import subprocess
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from ..core.events import (
    PROVENANCE_KINDS,
    SCHEMA_VERSION,
    TAIL_MAX_BYTES,
    TAIL_WINDOW_BYTES,
    Event,
    _kinds,
    canonical,
    utcnow,
)
from . import proc as P

__all__ = [
    "PROVENANCE_KINDS",
    "SCHEMA_VERSION",
    "Event",
    "EventLog",
    "canonical",
    "default_agent_id",
    "effective_agent_id",
    "resolve_agent_id",
    "utcnow",
]

_AGENT_ID_CACHE: dict[str, str] = {}


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
    """
    host = socket.gethostname().split(".")[0]
    root = str(fallback_root or "")
    key = f"{os.getcwd()}|{root}"
    if key in _AGENT_ID_CACHE:
        return _AGENT_ID_CACHE[key]

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
    ident = f"{host}-{name}"
    _AGENT_ID_CACHE[key] = ident
    return ident


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

    def __init__(self, root: Path, agent_id: str = "", *, lock_timeout_s: float = 30.0) -> None:
        self.root = Path(root)
        self.dir = self.root / ".ddflow" / "events"
        self.agent_id = agent_id or default_agent_id(self.root)
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

    # -- shard paths ---------------------------------------------------------------
    @property
    def shard(self) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in self.agent_id)
        return self.dir / f"{safe}.jsonl"

    def shards(self) -> list[Path]:
        if not self.dir.is_dir():
            return []
        return sorted(self.dir.glob("*.jsonl"))

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
        if kind not in _kinds():
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
        """Cheapest correct clock read: last line of each shard.

        Lamport values are non-decreasing WITHIN a shard because a shard has exactly
        one writer, so the tail carries that shard's maximum. Reading tails is O(shards)
        rather than O(events) — on NFS, where a full re-read measured ~36x slower than
        local, that difference is the difference between usable and not.
        """
        high = 0
        for p in self.shards():
            tail = _last_line(p)
            if not tail:
                continue
            try:
                high = max(high, int(json.loads(tail).get("lamport", 0)))
            except (ValueError, TypeError):
                continue
        return high

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
    def read_all(self) -> list[Event]:
        out: list[Event] = []
        self.skipped_lines = 0
        for p in self.shards():
            try:
                text = p.read_text("utf-8", errors="replace")
            except FileNotFoundError:
                continue
            for raw in text.splitlines():
                line = raw.strip()
                if not line:
                    continue
                try:
                    out.append(Event.from_json(line))
                except (json.JSONDecodeError, KeyError):
                    self.skipped_lines += 1
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
