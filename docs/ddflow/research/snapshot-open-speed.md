# Snapshot open speed: why opening a snapshot is slow, and what could make it cheap

Research date 2026-10-24 (item snapshot-speed-research). Legend: **F** verified (source read or run here), **I** inference, **U** unverified.
Measurements: this machine (CPython 3.13.12, worktree checkout of this repo), current queue state at the time of writing — 105,172 events, 81 MB across 571 shard files of `.ddflow/events/` — warm disk cache, median of 2-3 runs. Instrumentation scripts run out of `/tmp/kilo/` only; nothing in the repo was modified to measure.

## 0. What "snapshot open" is (F)

"Opening a snapshot" here is the per-command state load, i.e. every ddflow operation's prologue (`ddflow/api/_base.py::_load_fresh`): `Config.load` → `identity.open_log` → `EventLog.read_all()` (JSONL parse, optionally seeded from the on-disk read snapshot at `.ddflow/local/read-snapshot-<fingerprint>.bin`, or from the in-process parse cache) → `fold()` over the events → opportunistic `shardmarks.observe` / lease localisation / `overlay` — and, on any search-adjacent path, `Store.stale()` against the SQLite projection `.ddflow/index.db-<fingerprint>` (a cache and search index over the event log, `ddflow/infra/store.py`). Two cache families sit between the caller and the log bytes: the **read snapshot** (parsed events as tuples, `infra/log.py`, B166) and the **SQLite store** (fold-derived rows + FTS + the similarity postings). Both are git-ignored, disposable, per-code-fingerprint derivatives; the event log stays the only source of truth.

## 1. Probe: where the open time goes, at the current queue size (F)

One fresh process, one command, warm disk cache:

| stage | warm process, delta read | cold process (no snapshot file reachable) | notes |
|---|---|---|---|
| `Config.load` | 2 ms | 2 ms | negligible |
| `open_log` (agent identity, shard glob) | ~0-17 ms | ~0-17 ms | negligible; no git rev reads on this path |
| `read_all`, full JSONL parse | 1.5 s | 1.7 s (fresh parse) | measured with `snapshot=False`; ~1.1 s is `json.loads` + `Event.from_json` |
| `read_all`, in-process delta read (2nd read, same process) | 367 ms | — | per-shard prefix sha256 verify + merge sort of 105k events |
| `fold` over 105,172 events | 516 ms | 528 ms | every open recomputes this: ~200k events/s, no fold cache |
| `log.mark()` | <1 ms | <1 ms | O(shards): 571 `stat`s + shardmeta cache |
| `Store.stale()` (index current) | <1 ms | <1 ms | four cheap numbers vs meta |
| `Store` rebuild | — | 4.3 s | only when the code fingerprint changed or events landed since the index was built; net index build ≈ 2.2 s at ~6k records, index file ≈ 100 MB |

Costs classify as:

- **Cached across opens**: `Config.load`, `mark`, `Store.stale`. Effectively free, warm.
- **Recomputed per open, linear in the log**: `fold` (~0.5 s at 105k) always; the JSONL parse (~1.1 s) whenever the process is a fresh one-shot and the read snapshot is not usable; the merge sort of all events when the parse was not reused.
- **Recomputed per open, linear in shard count**: the prefix verification (571 × `read_bytes` + sha256 + `Event(*t)` reconstruction).
- **Size-independent but present**: `Store` rebuild on every fingerprint change (every ddflow release, every change under `ddflow/core/`).

**F, the surprising one — the snapshot is currently NOT helping this queue.** With defaults (`reuse_parsed=true`, `snapshot=true`, `max_cached_events=100_000`), a fresh process reading this 105,172-event log found no snapshot at `.ddflow/local/read-snapshot-1f0d29ee164a1b75.bin`, re-parsed all 81 MB, and did not write one afterwards. The mechanism is the memory ceiling, all-or-nothing: `_maybe_write_snapshot` aborts the *entire* write when **one** shard's parse could not be held (`any(d.parsed is None ...)`, `infra/log.py:1763`), and holding per shard is refused once the process would exceed `[log].max_cached_events` (105,172 > 100,000). One uncached shard suppresses the whole write. So the on-disk snapshot — which the knob's own doc names as the tool "a project big enough to hit this wants" — *turns itself off at exactly the scale where it pays*. That is a cliff, not a gradient: one event past the ceiling and a log that reaped ~2x cold reads stops reaping any.

- When the snapshot IS reachable (584-shard-era snapshots of other checkouts, and the two-shard benches B166/B169 shipped), the designed win is the parse: ~505 ms of `json.loads` per 100k events falls to ~215 ms of `marshal.loads` + `Event(*t)` (ship doc, `docs/RESEARCH.md`; **F** consistent with what I measured at 571 shards, where per-shard read+hash overhead dilutes the win to ~0.2 s).
- **Cache clutter grows with version count, not log size (F).** This machine's primary checkout holds 8 `read-snapshot-*.bin` (≈ 460 MB for one 81 MB log) and 60+ `index.db-*` files, one per code fingerprint, each kept up to 7 days of disuse. Every ddflow upgrade (and this project churns many) multiplies both families.

## 2. Do the same slow open path explain the two dupes bugs? (F: No.)

`ddflow dupes` pays the open above, then `pair_records` over all sweep records (`ddflow/api/knowledge/pairs.py`): build an in-memory TF-IDF inverted index over 6,134 in-scope records (742 ms) and run one `limit=None` query per record. Measured here: **5,405 hits per query** (dense vocabulary — nearly everything shares terms with everything) at **9.2 ms per query**, so the sweep alone is ≈ **48 s**, 72,845 pairs above the floor before any `--limit`. The open is 2.0–2.5 s of that (≈ 4%).

- **B098fd705f8** (`ddflow dupes <item>`, ~2 min per item on a 3,398-item log): `pairs_from(item=...)` deliberately sets `stop_after=0` and computes the **full pairwise sweep**, then post-filters rows to pairs containing the item (`pairs.py:184,187`). One item costs the whole sweep. The open path is a rounding error in that figure.
- **Bb38cc7b0d3** (`--limit 5` past a 120 s tool timeout on a 2,000-item queue): the full sweep with the bound applied only after the fact. Same cause, same numbers at 2,000 items (~5-6k sweep records ⇒ ~45-50 s at today's measured per-query cost; a 120 s timeout leaves only ~2.5x headroom).
- **Verdict**: both bugs are the pair sweep — an O(n²) tf-idf pass, in-memory and rebuilt per invocation, not the snapshot-open path. The open path itself is ~2 s and linear; it is not what times out. (My focus here is snapshot open; the sweep is treated in §3.4 as its one interaction point.)

## 3. Proposal, per stage (concrete, with expected win and the invariants that must hold)

The standing invariant for every item below: **the event log is the only source of truth; every cache is a pure function of the log's bytes plus the code fingerprint, verified by digest before any use, and a doubted cache degrades to a slower correct read — never to a wrong one.** Each proposed change keeps `read_all(snapshot=False)` and `verify()` byte-true.

**3.1 Make the read snapshot survive past the memory ceiling.** Chesterton's-hedgehog note first: today's writer requires the in-process parse to be *held* before it may persist it, and holding stops at `max_cached_events`. Decouple them: stream the tuples to the snapshot file during (or straight after) the delta parse even when the process will not retain them. Expected win: fresh one-shot commands on logs past 100k events stop re-parsing 81 MB per invocation — 1.5 s → ~0.4 s (read + verify + rebuild events), the win the two-shard bench promised. Risk: the writer must not hold a second 50-70 MB payload to do it (stream in shard order); the per-fingerprint file boundary (D-compat, `_snapshot_path`) is unchanged. Verify: `read_all(snapshot=True)` ≡ `read_all(snapshot=False)` still byte-identical folded state — a disagreeing cache reads as absent already (the "every doubt ends in parsing the log" rule).

**3.2 Shard-count-proof the seeded read (dirty-range / tail-only reads).** At 571 shards, seeded and unseeded reads are costly for *verification*, not parsing: each shard re-reads `read_bytes()` then re-hashes the consumed prefix to grant reuse. Keep a per-shard `(consumed-0, digest)` gate but read only `[consumed-0:]` and verify the prefix with the budget already spent on the tail; the existing whole-file hash stays as the fallback reject. Expected win: 571 × (read + sha256) + full re-read of 81 MB collapses toward hash-of-delta time; measured 367 ms in-process delta read → ~100 ms targets. Invariants: the stored digest must be a *prefix* digest of bytes actually on disk (already the shape of `_read_delta`); a torn/rewritten shard must still be caught — a size/mtime fast path may only *reject* the cache faster, never accept it.

**3.3 Stop rebuilding the whole SQLite projection after every code change (it is 4.3 s of a 2 s command).** `_rebuild_locked`'s own comment resolves this: *"There is deliberately no incremental update. An incremental projector is a second implementation of `fold` that can disagree with it."* That invariant was right when the incremental project would have duplicated `fold`. The log side found the sandbox where it is safe — append-only shards, prefix hashes, `mark()` as the staleness watermark — and the store's sheet meta already stores exactly those. Reproduce that sandbox on the store side (`meta.lamport/bytes/shards` are already written; add per-shard consumed-offset rows): an incremental rebuild replays only the appended deltas keyed on `log.mark()` and re-verifies consumed prefixes exactly as `_read_delta` does; any mismatch is a rebuild. Expected win: warm opens never pay more than the delta replay; the 4.3 s/fingerprint-change + the ~2.2 s net rebuild vanish off the hot path. Risk: HIGH-ambition item — it makes the store's correctness story depend on the same verification machinery the log side already productionizes; the falsifier is `tests/test_store.py::test_dropping_the_db_changes_nothing` extended over append-only deltas.

**3.4 Schematic-only, to close the loop on the two bugs (my siblings' focus, open verbatim for whoever implements):** `dupes <item>` should query only the item's terms — scoring symmetry guarantees the pair *set* is unchanged (cosine commutes, scores rounded twice), so the per-item call becomes one query plus build (~1.5-2 s) instead of the full sweep (48 s). For `dupes --limit`, move the bound inside the sweep (the `stop_after` engine already exists, honoured in `pair_records`; the discard for item calls is `pairs.py:184`) so the 120 s tool budget is respected from the first query, with a partial-read notice so a ceiling never silently hides pairs. Full unbounded sweeps stay O(n²); that is inherent and acceptable for an explicit "look at the whole history" ask, not for a per-item or `--limit` call. The durable postings already stored in `similar_terms`/`similar_docs` (`_insert_similar`) could serve the sweep's queries at build cost ≈ 0.

**Cost/risk ledger:** 3.1 ≈ small (writer decoupling, one test surface) · 3.2 ≈ medium (touch the one trust boundary: prefix verification) · 3.3 ≈ large (a second `fold`-derived projector; must be deliberate, knob-gated (`r-everything-configurable`), landing behind the criterion: same rows, same meta, and `dropdb`-parity before the rebuild is allowed to be skipped) · 3.4 ≈ medium but bounded, and it is the only proposal (with the two open bugs) whose payoff is minutes per call.

## 4. Contradictions and open questions

- **Which invariant generalises?** The store says "no incremental update, ever" (the comment in `_rebuild_locked`); the log side adopted incremental reads gated by byte-hash verification and is happy. §3.3 is exactly "incremental under verification" — a decision, not a mechanism, and it contradicts the store's recorded position. An operator should say whether the criterion is "never incremental" or "never *unverified* incremental" before anyone builds it.
- **Is a snapshot-per-version still paid for?** D-compat's one-cache-per-fingerprint keeps old checkouts correct at the price of caches multiplying with every release (8 snapshots ≈ 460 MB, 60+ index.db for one project). 7-day-untouched GC bounds growth but never shrinks the *working* set on a machine running N versions. Worth one decision: newest-N cap vs the compat guarantee.
- **Unbounded sweep vs the 120 s tool budget**: the current headroom at today's scale is ~2.5x; the sweep grows quadratically while budgets are flat — the open, the sweep and the budget cannot all three stay as they are.
- **How central is the 100k/105k razor?** The ceiling measured here is 5% away from silently switching this project between "snapshot works" and "snapshot never engages". Defaults should contain the cliff, e.g. the ceiling refusing to *hold* must not also refuse to *persist* (that is 3.1).
