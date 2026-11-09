"""``EventLog``: the append path, assembled from the guard and read mixins."""

from __future__ import annotations

import contextlib
import ipaddress
import json
import os
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, TypeVar

from ...config import Config, LogConfig, SessionConfig
from ...core import redact as R
from ...core import upcasters as UP
from ...core.events import CAPABILITIES_KIND, SEEN_KIND, SKEW_OVERRIDDEN_KIND, Event, utcnow
from ...core.model import known_kinds
from ...core.redact_errors import PatternError, fail_closed_data
from ...core.secret_shapes import session_secret_patterns
from ...core.slug import safe_filename
from .. import blobstore as BS
from .. import fsio
from .. import hostinfo as H
from .. import shardmeta as SMT
from ._agent import default_agent_id
from ._guard import _GuardMixin
from ._lock import _HELD, _flock, _held_key
from ._read import _last_line, _ReadMixin
from ._skew import LogMark

#: Kinds an append writes WITHOUT the stamp-and-guard step: the stamp itself, and the
#: override that exists to be written while the guard would refuse everything else.
_STAMP_EXEMPT = frozenset({SEEN_KIND, SKEW_OVERRIDDEN_KIND, CAPABILITIES_KIND})

T = TypeVar("T")


def _reviewer_redact_names(root: Path) -> list[str]:
    """The LOCAL configured reviewers' service names and model strings.

    A local reviewer's endpoint names a private address, so its service name and model
    are none of a secret, a host, a path or the machine's own name: the committed-log
    name pass must learn them or they leak into the pushed shards as review evidence
    (bug B8ef0d325ed). A public reviewer's name and model name a public endpoint and
    stay. The reviewer loader is imported lazily (services <-> infra) and any of it
    being unreadable is the caller's fallback, not this write's.
    """
    try:
        from ...services.review.model import load_reviewers
    except Exception:
        return []
    try:
        out: list[str] = []
        for rev in load_reviewers(root):
            if _is_local_endpoint(rev.base_url):
                for value in (rev.name, rev.model):
                    if value:
                        out.append(value)
        return out
    except Exception:
        return []


def _is_local_endpoint(base_url: str) -> bool:
    """``True`` when ``base_url`` names a private/loopback/link-local address or host."""
    try:
        from urllib.parse import urlparse

        host = (urlparse(base_url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    if host in ("localhost", "localdomain") or host.endswith(
        (".local", ".internal", ".lan", ".home.arpa")
    ):
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False

#: Called after each event is on disk, under the append lock: `(log, event, size_before,
#: size_after)` of the written shard. A module registers itself here (`services.shardmarks`
#: keeps the shard high-water mark current, Ba24a8d32ee) so every EventLog -- the CLI's
#: `Ctx.log`, the MCP server's, `open_log`'s -- follows, with no layering inversion. An error
#: in a hook never fails the append.
APPEND_HOOKS: list[Callable[[EventLog, Event, int, int], None]] = []


class EventLog(_GuardMixin, _ReadMixin):
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
        BS.remember(self.root)
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
        self._meta: SMT.ShardMeta | None = None
        self._redactor: Any = None
        self._lookup_net = True
        self._caps_active: set[str] | None = None
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
        Re-entrant by depth count, keyed on (pid, thread, lock path) rather than on this
        object, because flock is per-open-file-description: a SECOND EventLog instance on
        the SAME thread would otherwise deadlock against the lock the first one holds.
        A DIFFERENT thread is not a re-entrant caller: it queues on the flock and waits
        the holder out, like a writer in another process (Bf7f6540615).
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
                payload.update(self._stamp_and_guard(kind))
            return self._write(kind, subject, payload, observed)

    def append_many(self, items: Iterable[tuple[str, str, dict[str, Any]]]) -> list[Event]:
        """Append several events as ONE unit: all of them, or (when one cannot be written)
        none. The append lock is held throughout, so no other writer's event lands between
        them; a failure truncates this shard back to where it was, so a reader never sees
        half of a merge or a split."""
        with self.transaction():
            before = self.shard.stat().st_size if self.shard.exists() else 0
            clock = self._lamport
            out: list[Event] = []
            try:
                for kind, subject, data in items:
                    out.append(self.append(kind, subject, data))
            except BaseException:
                if self.shard.exists():
                    os.truncate(self.shard, before)
                self._lamport = clock
                raise
            return out

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
        # Large gate evidence leaves the shard, after redaction so the blob holds what the shard would.
        data = BS.externalize(self.root, self.log_cfg, kind, data)
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
            st0 = os.fstat(fd)
            size, before_mtime = st0.st_size, st0.st_mtime_ns
            if size and os.pread(fd, 1, size - 1) != b"\n":
                line = b"\n" + line
            view = memoryview(line)
            while view:  # a short write is legal; finish the line rather than tear it
                view = view[os.write(fd, view) :]
            os.fsync(fd)
        finally:
            os.close(fd)
        if self._meta is not None:
            self._meta.advance(self.shard, (size, before_mtime), ev, len(line))
        for hook in APPEND_HOOKS:  # every log, however built (Ba24a8d32ee)
            with contextlib.suppress(Exception):
                hook(self, ev, size, size + len(line))
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
            self._lookup_net = True
            try:
                cfg = Config.load(self.root)
                self._lookup_net = bool(cfg.session.redact_lookup_network)
                patterns = session_secret_patterns(cfg.session, cfg.log.redact_builtin_shapes)
                # `[upstream].redact_extra`, once that section exists (`names_for`)
                up = getattr(cfg, "upstream", None)
                names = [str(n) for n in (getattr(up, "redact_extra", None) or [])]
                names.extend(_reviewer_redact_names(self.root))
            except Exception:
                # an unreadable config: the built-in patterns for THIS write, and the config is
                # read again on the next one, so a fixed config is not shadowed by the fallback
                patterns, cached = session_secret_patterns(SessionConfig()), False
            host = H.hostname()
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
        try:
            return R.redact_event_data(kind, data, red, lookup_network=self._lookup_net)
        except PatternError:
            raise
        except Exception:  # fail closed: a placeholder in every text field, never the raw text
            return fail_closed_data(data)

    def redact_event_text(self, event_id: str, field: str, marker: str) -> tuple[Event, Event]:
        """Redact one free-text field of an already-written event, in place.

        The sanctioned redaction (D-event-log-redaction-forward): the sensitive or superseded
        text leaves the shard, the event's content address is recomputed so `verify` still
        holds (`id == compute_id` for every event), and the caller records the auditable
        redaction event naming ``redacted.id`` (the address the log now holds for that event).

        Returns ``(original, redacted)``: the event as written, and it with ``data[field]``
        replaced by ``marker``. Raises ``KeyError`` when no event has that id. Call the
        caller under the append lock.
        """
        original = self._find_event(event_id)
        if original is None:
            raise KeyError(f"no event {event_id!r}")
        if field not in original.data:
            # Refuse a field the record never wrote: redacting a field that is not there would
            # add it rather than remove the sensitive one, leaving the target text committed.
            raise KeyError(f"no field {field!r} in event {event_id!r}")
        data = dict(original.data)
        data[field] = marker
        redacted = Event(
            kind=original.kind,
            subject=original.subject,
            data=data,
            agent=original.agent,
            lamport=original.lamport,
            ts=original.ts,
            schema=original.schema,
        )
        redacted = Event(**{**redacted.__dict__, "id": redacted.compute_id()})
        self._rewrite_line(event_id, redacted)
        return original, redacted

    def _find_event(self, event_id: str) -> Event | None:
        for shard in self.shards():
            for raw in shard.read_text(encoding="utf-8").splitlines():
                try:
                    got = json.loads(raw).get("id")
                except (ValueError, TypeError, AttributeError):
                    continue
                if got == event_id:
                    return Event.from_json(raw)
        return None

    def _rewrite_line(self, old_id: str, ev: Event) -> None:
        """Replace the line whose event id is ``old_id`` with ``ev``'s canonical line.

        The whole shard is rewritten atomically through `infra.fsio.replace_text` (the one
        home for `tempfile`/`os.replace`): a reader sees the old shard or the new one, the
        shard keeps its permission bits (or the write happens in place where a rename would
        give it away), and the content is fsynced before the rename."""
        line = ev.to_json()
        for shard in self.shards():
            lines = shard.read_text(encoding="utf-8").splitlines()
            for i, raw in enumerate(lines):
                try:
                    got = json.loads(raw).get("id")
                except (ValueError, TypeError, AttributeError):
                    continue
                if got == old_id:
                    lines[i] = line
                    fsio.replace_text(shard, "\n".join(lines) + "\n")
                    if self._meta is not None:
                        self._meta._rows.pop(shard.name, None)
                    return
        raise KeyError(old_id)
