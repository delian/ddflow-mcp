# D-hot-cold-tiering: hot and cold events that keep every piece of evidence

Status: proposed for operator approval (task PS-hot-cold-design). Source: D-scale-answers (3)(4),
R-scalability option G/E. Prototype: `ddflow/infra/log/_tier.py`; falsifier plan as running
tests: `tests/test_hot_cold_design_fixture.py`. No production code changes.

## Recommendation (short)

1. Tier ONE kind first: `lease.renewed`. Write each renewal to a per-agent COLD shard, and
   write to the hot shard a coarse SUMMARY that is itself an ordinary `lease.renewed`
   (same kind, `at` = latest renewal, plus a `summary` reference). Today's ddflow folds a
   summary exactly like a renewal, so old readers stay correct.
2. Keep `ddflow.seen` HOT for good (the skew guard reads the highest stamp; 0.6 percent of
   events). `gate.started` / `gate.out_of_order` are folded (open gate rounds, `gate_order`
   counts), so they are phase 2, only after phase 1 is measured, counted by the summary.
3. The full log is the union of hot and cold shards ordered by (lamport, id). Replay,
   export, verify, edit-by-correction and `--full` fold read the union: output identical
   to an untiered log. The hot view (hot shards only) is a derived speed path.
4. Default `tier.enabled = false` (today's behaviour). Forward-only: already committed
   shards are never rewritten, so the 39,655 historic renewals stay where they are; the
   local State checkpoint (PS-state-checkpoint) is what makes history cheap.
5. Be honest about the gain: tiering lease renewals cuts hot EVENT COUNT by 19.5 percent
   (28 percent with phase 2) and hot bytes by 4.7 percent. It does not shrink the repository
   (cold shards are committed). It is the growth brake, not the main speed-up; the
   checkpoint plus tail fold is. Do phase 1 only if the operator wants the brake now.

## Measured size model (real log, 2026-11-04, `.ddflow/events`, 878 shards)

135,495 events, 121.07 MB (252 B per renewal, 33 us per event to fold per R-scalability).

| kind | events | share | bytes | share |
|---|---|---|---|---|
| lease.renewed | 39,655 | 29.3 % | 10.01 MB | 8.3 % |
| gate.started | 9,319 | 6.9 % | 1.88 MB | 1.6 % |
| gate.out_of_order | 2,339 | 1.7 % | 0.60 MB | 0.5 % |
| ddflow.seen | 824 | 0.6 % | 0.19 MB | 0.2 % |
| gate.passed + gate.failed (evidence) | 22,589 | 16.7 % | 64.5 MB | 53.3 % |

Note the count/bytes gap: the four operational kinds are 38.5 percent of events but 10.5
percent of bytes; the BYTES are gate evidence (already externalisable to the blobstore).
Tiering buys parse and fold time (count), not disk.

Summaries needed (one per lease per window, simulated on the real renewals; renewal gap
p50 123 s, p90 602 s, p99 2400 s; 1,924 distinct leases):

| summary window | summaries | hot events | hot vs today (count) |
|---|---|---|---|
| 300 s | 19,595 | 115,435 | -14.8 % |
| 600 s (default) | 13,177 | 109,017 | -19.5 % |
| 1800 s | 6,635 | 102,475 | -24.4 % |

At 600 s a summary is about 330 B (252 plus the reference), so hot bytes go
121.07 -> about 115.4 MB (-4.7 %). Phase 2 removes up to 11,658 more events minus their
summaries: hot about 97k events (-28 %). Cold shards hold 10.0 MB (phase 1) and are
committed. Projection at 1M events with the same mix: the hot fold sees about 800k, so
cold load 15-30 s becomes 12-24 s: tiering alone does not fix scale; checkpoint plus
tail fold does (0.47 s load of an 85 MB State). Measurements that were not made:
end-to-end wall time with the prototype (not wired; `unavailable` until PS-hot-cold-state).

## (1) Which kinds, declared as data

```toml
[tier]
enabled = false                       # default: today's behaviour
cold_kinds = ["lease.renewed"]        # phase 2 adds gate.started, gate.out_of_order
summary_every_s = 600                 # validated: <= lease.ttl_s / 2
fall_through_max_bytes = 1048576      # search reads at most this much cold per query
```

Never cold: everything with evidence value (gate outcomes, reviews, prompts, decisions,
task events), `lease.acquired`, `lease.released`, `lease.expired`, `ddflow.seen`. A new
kind is hot unless listed. An unknown kind in a cold shard is ignored and reported
(r-replay-compat).

## (2) Hot cache and hot summary, each cold range referenced

- Cold shard: `.ddflow/cold/<agent>-<clone>.jsonl`, same line format as hot shards, one
  writer per file (the existing per-agent-per-clone naming), append-only, fsynced.
- Summary (hot shard, kind `lease.renewed`): `data = {holder, at: <latest renewal in the
  run>, summary: {shard, start, end, sha256, count}}`. `start`/`end` are byte offsets
  of whole lines; `sha256` covers exactly those bytes. A summary can always be re-expanded
  (`reexpand`) and its digest proves the range unchanged.
- Hot cache: a local, gitignored State checkpoint built from hot shards only (derived,
  r-derived-cache-bounded: fingerprint = schema + fold revision + tier-policy revision,
  size cap, GC, kill switch `DDFLOW_TIER=0`, rollback = delete the file). It is never
  trusted when the fingerprint differs.

## (3) Sharing cold data between clones through git

Nothing new is shared: cold shards are plain committed files, one writer each, so a merge
is a union of distinct files (no shared mutable file, no conflict; same property the hot
shards have today). Summaries travel in hot shards. Rule: write order is cold fsync first,
summary second, so a pushed summary never refers to bytes the writer lacks; but git can
push the hot shard and not yet the cold shard (a partial push). A reader missing the cold
range reports `unavailable: pull <shard>` (never "no result"); the summary alone still
carries liveness and `count`. A new clone gets cold data with an ordinary `git pull`;
sparse/partial clones may skip `.ddflow/cold/` and run in hot view with a notice.
A server without a filesystem (r-log-is-the-state) writes the same two directories.

## (4) Secondary search

The search index (derived, local FTS) indexes hot events and summaries: a summary's text is
kind, subject, holder, time span, count. Renewals have no free text, so summary-level
matching is complete for them; for a kind with text (a future cold kind) the index also
holds the cold lines of the shards present. A query that hits a summary falls through to
its cold range via `(shard, start, end)`, reading at most `fall_through_max_bytes`; over
budget returns `truncated: true`, absent shard returns `unavailable`. Both are shown to
the caller. Ids resolve through a local id -> (shard, offset) index, so edit-by-correction
and `show` find a cold event by id.

## (5) Export, replay, verify, correction

- `read_all()`, replay, export, `verify`, `--full`: union of tiers, ordered (lamport, id),
  de-duplicated by id: byte-for-byte the events written, plus the summaries (which are real
  events and are exported flagged by their `summary` field). Test: same events written
  with tiering off and on give identical unions apart from summaries.
- Fold: `--full` folds the union (the reference); the default after phase 1 folds the hot
  view. Equivalence test (to be added in PS-hot-cold-state): State(hot) == State(full) on
  every field a cold kind feeds, at every summary boundary and exactly after a flush.
- Edit-by-correction: a correction is a new hot event naming the cold event id; nothing in
  a cold shard changes. `verify` also checks every summary digest.

## (6) Liveness across clones and provable expiry

Liveness already travels by git today: another clone sees a renewal only after the writer
commits and pushes the shard. Tiering does not add a transport, it adds a resolution: the
summary cadence (default 600 s, flush also at release, completion and shutdown). Rules:
`summary_every_s <= ttl_s / 2` (config check), the first renewal of a run is summarised at
once, so a live holder is never more than `summary_every_s` behind its true last renewal;
with ttl 1800 s and grace 120 s the margin is at least 1200 s. Expiry stays what it is:
`renewed_at + ttl + grace < now` over the hot view; the proof of an expiry is the last
summary, and `verify`/`show --full` can re-expand its cold range (when present) to show
the true last renewal. A holder whose cold tail is newer than its last summary and whose
clone is unreachable cannot be seen by anyone else: the same limit as an unpushed shard
today. A lease also records `lease.released` / `lease.expired` hot, unchanged.

## (7) Upgrade and rollback

- Old reads new: `.ddflow/cold/` is ignored by an old ddflow (shard glob is `events/`);
  summaries are `lease.renewed` and fold as renewals, so leases stay live. Only the cold
  events are invisible to an old reader (that is fine: they are folded to a max anyway).
  An old ddflow that WRITES keeps writing hot renewals: valid. The skew guard stamps the
  tier as a recorded capability so an older writer is told once.
- New reads old: no cold dir means no tiering, identical to today; old logs replay unchanged
  (r-replay-compat); renewals already in hot shards are folded as they are.
- Migration: none for data. Enabling is `ddflow config --set tier.enabled true`; a dry
  run `ddflow tier plan` (to be built) prints what would be tiered and the expected hot
  size without writing. Forward-only, idempotent, resumable.
- Rollback: set `tier.enabled=false`; writers return to hot renewals at once. Cold shards stay
  committed and still read by the union reader (nothing is removed, so no data step). If a
  summary digest ever fails, the fold falls back to the full union and `verify` reports.
- Upgrade note in `ddflow/templates/upgrade/unreleased/` ships with the production change.

## (8) Failure modes

| scenario | outcome |
|---|---|
| crash after cold fsync, before summary | events are in the union; the next summary covers them; liveness only lags (test `crash_between_cold_and_summary`) |
| crash mid-line in cold shard | torn tail skipped and counted, as hot reader does; refs cover whole lines only |
| cold range edited or corrupted | `DigestMismatch`; `verify` flags it; the git history holds the original |
| clone never pushes | its hot and cold shards are absent together: identical to an unpushed shard today |
| hot pushed, cold not | summary alone gives liveness and count; search reports `unavailable` |
| clock skew | `at` stays the writer's wall clock exactly as in lease.renewed today; ordering is lamport; grace_s absorbs skew as before; no new clock |
| two machines, one agent id | the shard name includes the clone id: one writer per file; a same-name file with different bytes is the conflict the design forbids (`git_merge` rejects it in the test) |
| ref points past EOF (truncated cold) | `DigestMismatch`/`Unavailable`, never a partial answer |

Wiring notes (roborev 3978): tiering requires an adopted clone (a clone id in the shard
name); an unadopted checkout keeps hot renewals and `ddflow tier plan` says so. The
production writer imports the clone-suffix shape from `_agent.py` instead of redeclaring it.

## Alternatives rejected

- Move old renewals out of existing shards: rewrites committed shards (r-replay-compat).
- Drop renewals, keep only the last: loses evidence; operator said keep every event.
- A database or an external store for cold: no second store (r-log-is-the-state).
- A new summary kind: old readers would treat leases as expired; the compatible
  `lease.renewed` summary costs nothing.
- Sealed age segments (cold by age rather than by kind): possible phase 3 for all kinds,
  after the checkpoint exists.

## Open questions with recommended defaults (for the operator)

1. Do phase 1 now, or only the checkpoint first? Default: checkpoint first (bigger win);
   tiering phase 1 right after, as the growth brake.
2. `summary_every_s`: 600 (default), 300 if cross-clone liveness feels coarse. Cost: +6.4k hot events.
3. Phase 2 kinds (`gate.started`, `gate.out_of_order`): default NO until phase 1 is measured;
   they feed the fold and an old reader would undercount `gate_order`.
4. Should the cold dir be excluded from sparse clones by default? Default: no, pull it;
   revisit above 100 MB cold.
5. Historic renewals: leave in place (default), no retiering of committed shards.
6. Cold shards in the same repository (default) versus a separate evidence repository
   (r-consent-outward needs recorded consent); default: same repository.

## Falsifier test plan

Falsifier: two clones and a crash where any event or evidence blob cannot be recovered,
found by search, or replayed. The tests in `tests/test_hot_cold_design_fixture.py`: disabled
policy = today; union loses nothing; summary folds as a renewal; liveness lag below cadence;
every ref re-expands with digest; two-clone merge without conflict; crash between cold and
summary; torn tail; altered range; unpushed cold (unavailable); bounded fall-through;
tiering on/off give identical unions; `ddflow.seen` stays hot. To add with the production
tasks: State(hot) == State(full) on the real log copy; upgrade-dry-run on a previous
release fixture; old-ddflow-reads-new-log test; a multi-process crash test with kill -9.
