# Backlog — findings accepted but not fixed in this pass

Each entry is a real finding from the review stack that was judged out of scope for the
initial build. None is a silent TODO in the source: they are all here, with the analysis
that produced them, so the next session does not rediscover them.

In an adopted project these would be Orchard tasks (`orchard task add ...`). They live in
markdown here because Orchard is not yet dogfooding itself — which is itself the first
item below.

## Structural

- **B1 — ~~`fold` is a 150-line `if/elif` ladder~~ — DONE.** Replaced by
  `model.HANDLERS: dict[str, Callable]`, and `events._kinds()` now *derives* the
  vocabulary from it rather than declaring a second copy. A kind that nothing
  interprets can no longer exist. *Found by: roborev architecture (C4). Retired in the
  same session; 104 tests passed unchanged across the refactor, which is the evidence
  it was behaviour-preserving.*

- **B2 — Lifecycle classification is split across `schedule` and `lease`.** `lease.scan`
  treats "RUNNING with no lease" as `stale_running` needing recovery; `plan()` hands the
  same item to another agent as ready, with no mention that someone was mid-flight. Both
  behaviours are defensible; deciding them in two modules that never consult each other
  is not. *Found by: roborev architecture (C6).*

- **B3 — The live-lease dict is computed three times** (`State.active_leases`, inline in
  `plan`, and formerly in `_alternatives`). Route all of them through
  `State.active_leases`. *Found by: roborev architecture (C6).*

## Performance

- **B4 — The coordination path holds the write lock across an O(all-events) read.**
  `lease.acquire` does `read_all()` + `fold()` *inside* the flock. Folding is cheap
  (407k events/s measured) but the I/O is not, and on the NFS mount this was designed
  against that read is ~36× the local cost. Move the read before the lock and
  re-validate only the affected item inside it. *Found by: roborev architecture (C9).
  Not urgent: measured contention is far below the point where it matters.*

- **B5 — `Store.stale()` reads everything to decide whether to read everything.** It
  calls `log.read_all()` to compare event counts. `EventLog._highest_lamport()` already
  demonstrates the cheap pattern — a per-shard tail read, O(shards). Add
  `EventLog.head() -> (shards, highest_lamport, total_bytes)` and compare that instead.
  *Found by: roborev architecture (C9).*

## Declared but unimplemented

- **B6 — `log.compacted` is vocabulary without a mechanism.** The event kind exists,
  `PROVENANCE_KINDS` documents what compaction must preserve, and `fold` handles the
  marker — but nothing implements it. Either implement (rewrite shards keeping every
  `PROVENANCE_KINDS` event plus the last state-bearing event per subject, then append
  the marker) or remove the kind. Declaring a retention policy the system cannot execute
  is worse than declaring none. *Found by: roborev architecture (C10). Not urgent: at
  407k events/s the growth ceiling is far away.*

- **B7 — Orchard does not dogfood itself.** This backlog should be an Orchard queue, and
  this project's own development should run through its own gates. The reason it does
  not yet is bootstrapping order, not principle.

## Mechanical cleanups

- **B8 — `renew` / `release` / `expire` share ~85% of one body** (`lease.py`); they
  differ only in a guard and a payload. *Found by: roborev duplication (D5).*
- **B9 — `"no such item" → stderr → FAIL` appears 5× in `cli.py`**, and the same
  condition is phrased two further ways in `gates.py` and `lease.py`. *(D6)*
- **B10 — `phase add` and `task add` argparse blocks are re-typed**, though the
  `record`/`skip` pair right below them is already loop-generated. *(D7)*
- **B11 — `cli._csv` and `config._coerce`'s list branch are the same expression.** *(D9)*

## Coverage gaps

- **B12 — No cross-family review has been run against `orchard/`.** The configured
  critic's endpoint was unreachable during the whole build (HTTP 000, with a concurrent
  session saturating it). Recorded as `unavailable`, never as a pass. This is the
  single largest gap in the review of this code, because every reviewer that *did* run
  shares the author's pretraining family.
- **B13 — Windows is untested.** `fcntl.flock` is POSIX-only; `events.py` would need an
  `msvcrt.locking` branch. The rest is portable. No probe was run, so this is
  `THEORETICAL`.
- **B14 — Multi-machine clock skew is reasoned about but unprobed.** The Lamport
  ordering is designed so wall-clock skew cannot reorder history, and lease expiry has a
  `grace_s` knob for it — but no two-machine probe was run. `THEORETICAL`.
