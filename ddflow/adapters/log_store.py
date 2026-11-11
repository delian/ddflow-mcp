"""The `LogStore` port: the append-only event log and its content-addressed blobs.

The event log is the project's source of truth, and only the local jsonl shards exist today:
simulators and tests need a real directory for it, and a remote or git-shared log would
touch the core. This port is that seam (ADP-log-store). It declares the storage contract
only -- append with ordering and a lock, read from an offset, list shards, snapshot,
compact, blob put and get by digest -- with no implementation. The default adapter is the
current local jsonl shards (`ddflow.services.log_store.LocalLogStore`); the in-memory fake
(`ddflow.adapters.fakes.log_store.FakeLogStore`) serves the ETA simulator and swarm tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol, runtime_checkable

from .ports import port


@port
@runtime_checkable
class LogStore(Protocol):
    """Append-only event log plus content-addressed blobs, behind one seam.

    Home: `ddflow.adapters.log_store`. Single default adapter
    (`ddflow.services.log_store.LocalLogStore`: the local jsonl shards of
    `infra.log.EventLog` and `infra.blobstore.BlobStore`); the fake is
    `ddflow.adapters.fakes.log_store.FakeLogStore`. A remote or shared backend is a later
    adapter behind this same port and needs consent, never a new code path in a caller.
    """

    def append(
        self,
        kind: str,
        subject: str,
        data: dict[str, Any] | None = None,
        *,
        agent: str = "",
    ) -> Any:
        """Write one event and return it, with its ordering filled in. Ordering is the
        store's monotonic sequence (the lamport clock of the local shards), and the write
        is serialised under the store's lock. ``agent`` names the shard (the local adapter
        uses its own agent's shard; the fake creates one)."""

    def append_many(self, items: Any) -> list[Any]:
        """Append several ``(kind, subject, data)`` events as one unit: all of them, or none.
        The lock is held throughout, so no other writer's event lands between them."""

    def read_from(self, offset: int = 0) -> list[Any]:
        """Every event whose sequence number is above ``offset``, sorted and de-duplicated
        (`offset=0` is everything). The one incremental read: a caller passes the last
        sequence it has already seen."""

    def shards(self) -> list[str]:
        """The names of the shards present, sorted."""

    def snapshot(self) -> list[Any]:
        """The events as of this moment, as a fresh list: a point-in-time copy a later
        append does not change."""

    def compact(self) -> int:
        """Fold the shards down, without changing what `read_from(0)` returns. Returns how
        many shards were merged away (0 for the local jsonl store, which is never
        compacted)."""

    def blob_put(self, ref: str, raw: bytes) -> None:
        """Store ``raw`` under its content digest ``ref`` (``<algo>:<hex>``) unless it is
        already there; a ``ref`` that is not the address of these bytes is refused."""

    def blob_get(self, ref: str) -> bytes | None:
        """The bytes stored under ``ref``, or ``None``."""

    def blob_refs(self) -> Iterator[str]:
        """The digest of every blob present."""
