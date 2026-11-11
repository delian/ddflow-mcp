"""The default `LogStore`: the local jsonl shards and blob files behind one port.

The `LogStore` port is declared in `ddflow.adapters.log_store`; this module supplies its
single default adapter. Because the bottom `adapters` layer cannot reach up into
`ddflow.infra.log` and `ddflow.infra.blobstore`, the adapter that delegates to them lives
here, in `services` (r-core-pure, the `ConfigStore` shape). The jsonl shard BYTES and the
blob files are untouched -- `read_from`, `shards`, `snapshot` and the blob methods are the
`EventLog` / `BlobStore` paths exactly -- so a remote or shared backend is a later adapter
behind the same port, and this one holds the behaviour, unchanged.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ddflow.adapters.log_store import LogStore
from ddflow.infra.blobstore import BlobStore
from ddflow.infra.log import EventLog

__all__ = ["LogStore", "LocalLogStore"]


class LocalLogStore:
    """The default `LogStore`: an `EventLog` over `<root>/.ddflow/events` and a `BlobStore`
    over `<root>/.ddflow/blobs`.

    ``stamp`` is off: the version stamp and skew guard are domain write paths, not the
    storage seam, so `append` stores exactly the record it is given (the fake is faithful
    to that). The domain `EventLog` and its stamping are unchanged.
    """

    def __init__(
        self,
        root: Path | str,
        agent_id: str = "",
        *,
        stamp: bool = False,
        lock_timeout_s: float = 30.0,
        log_cfg: Any = None,
        cache_writes: bool = True,
    ) -> None:
        self._log = EventLog(
            Path(root),
            agent_id,
            lock_timeout_s=lock_timeout_s,
            log_cfg=log_cfg,
            cache_writes=cache_writes,
        )
        self._log.stamp = stamp
        self._blobs = BlobStore(self._log.root)

    def append(self, kind: str, subject: str, data: dict[str, Any] | None = None):
        return self._log.append(kind, subject, data)

    def append_many(self, items):
        return self._log.append_many(items)

    def read_from(self, offset: int = 0):
        return [e for e in self._log.read_all() if e.lamport > offset]

    def shards(self) -> list[str]:
        return [p.name for p in self._log.shards()]

    def snapshot(self) -> list[Any]:
        return list(self._log.read_all())

    def compact(self) -> int:
        # The jsonl log is never compacted (an append-only line already parsed cannot have
        # changed; see `infra/log/_read.py`); a fold is a read, which a caller does itself.
        return 0

    def blob_put(self, ref: str, raw: bytes) -> None:
        self._blobs.put(ref, raw)

    def blob_get(self, ref: str) -> bytes | None:
        return self._blobs.get_bytes(ref)

    def blob_refs(self) -> Iterator[str]:
        return self._blobs.refs()
