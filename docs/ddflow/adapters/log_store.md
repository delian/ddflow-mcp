# LogStore

The append-only event log and its content-addressed blobs, behind one port.

## Home

- Port: `ddflow.adapters.log_store.LogStore` (declaration only).
- Default adapter: `ddflow.services.log_store.LocalLogStore` — the local jsonl shards
  (`ddflow.infra.log.EventLog` over `.ddflow/events`, `ddflow.infra.blobstore.BlobStore`
  over `.ddflow/blobs`). The shard bytes and blob files are untouched.
- Fake: `ddflow.adapters.fakes.log_store.FakeLogStore` — in-memory, for the ETA simulator
  and the swarm tests (no directory, no lock file).

A remote or git-shared log is a later adapter behind this same port and needs consent.

## The contract

- `append(kind, subject, data, *, agent="")` — one event, ordered by the store's monotonic
  sequence, written under its lock; returns the record.
- `append_many(items)` — several events as one atomic unit (all or none).
- `read_from(offset=0)` — events with a sequence above `offset`, sorted, de-duplicated.
- `shards()` — the shard names present.
- `snapshot()` — a point-in-time copy of the events.
- `compact()` — fold the shards without changing `read_from(0)`; returns shards merged away
  (the local jsonl store returns 0: it is never compacted).
- `blob_put(ref, raw)` / `blob_get(ref)` / `blob_refs()` — content-addressed blobs by digest.

The default adapter turns the version stamp and skew guard OFF on its `EventLog`: they are
domain write paths, not the storage seam, so `append` stores the record it is given and the
fake is faithful to it. The domain `EventLog` and its stamping are unchanged.
