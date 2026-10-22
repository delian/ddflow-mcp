# Batched gates: run ci and review once for several parallel items, sized by changed lines

Task RM-batch-gates, agent res-batch, 2026-10-22. Operator request (verbatim, 2026-10-22):
"Can you research for an option of execution that ddflow shall support where several tasks
or bugs are implemented in parallel and then they wait for the common roborev and other
gates to be checked based on the size of the changes (so review, critic per X changed
lines) so we can speed up the development and make the gates and review steps more rare
and supposedly faster? Is this make sense? ..."

Labels: MEASURED (computed from the committed event log `.ddflow/events` and `git diff`
of each item's merge, window 2026-10-01 to 2026-10-22, 1 076 completed items, 15 145 gate
results), ESTIMATED (arithmetic on measured numbers; assumptions named), UNVERIFIED (from
memory or a search that returned no primary source). The scripts are not committed; every
figure can be recomputed from `worktree.merged`, `gate.passed|failed`, `review.triaged`
and `ci.result` events. Related research that this builds on: `review-scaling.md`
(RV-scale-research), decision `D-gate-economy-r3`, task `B-batch-integration`.

## 0. Verdict

It makes sense, in two steps of very different risk.

1. Batching the FULL-SUITE checks (ci command set, unit_tests, standards over the combined
   range) is cheap, low risk, already decided in principle (D-gate-economy-r3 (a): "full CI
   per batch in the merge queue") and not yet built: the merge queue today runs ci once PER
   ENTRY (`api/mergequeue_run.py`; `[merge_queue].max_batch` only bounds entries per run).
2. Batching the MODEL REVIEWS (rubber_duck, critic) by changed lines is where most agent
   hours are (60% of gate run-hours), and the measured cost curve favours it: a review
   request costs mostly a fixed price, not a price per line. It is also where the risks
   (attribution, independence, depth) live, so it comes second, behind a knob, after the
   numbers from step 1 and the review-state fixes.

Recommendation: option B, then option C behind a knob (section 6).

## 1. What the log says (MEASURED unless marked)

### 1.1 Where the gate hours go

Per gate, summed `elapsed_s` of results since 2026-10-01 (1 076 completed items):

| gate | run hours | share of these four |
|---|---|---|
| rubber_duck | 287 | 31% |
| critic | 275 | 30% |
| ci | 199 | 21% |
| unit_tests | 169 | 18% |

Review is 562 of 930 hours: 0.52 review hours per completed item, 0.86 gate hours in all.
Whole-log medians (`ddflow metrics`, n=1 105): cycle time claim to done p50 1.1 h, p90 3.8 h;
review rounds per item p50 4, p90 8; queue wait (ready to claimed) p50 52 min, p90 19 h, of
which behind a glob conflict p50 0, p90 4.0 h. Gate time p50/p90: ci 7/14 min, critic 6/18,
rubber_duck 6/16, unit_tests 10/23.

### 1.2 A review request has a large fixed price

Review results (rubber_duck and critic) bucketed by the size of the item they ran on
(changed lines, code plus tests, from `git diff merge-base..branch_head`, `.ddflow` excluded;
item size, not the delta of that round):

| item size (lines) | review runs | elapsed p50 | findings per run | runs with a finding |
|---|---|---|---|---|
| < 50 | 541 | 2.2 min | 1.34 | 64% |
| 50-149 | 942 | 4.6 | 1.38 | 74% |
| 150-399 | 1 586 | 6.2 | 2.50 | 87% |
| 400-999 | 1 121 | 8.3 | 4.15 | 95% |
| 1000+ | 320 | 10.6 | 6.13 | 96% |

Median item is 199 lines. Going from 50 to 1000 lines (x20) multiplies review time by x4.8
and findings per run by x4.5. Together with `review-scaling.md` (elapsed = 426 s fixed + 0.10
s per kchar, R^2 0.00; chunks reviewed in one parallel wave) the reading is the same: time is a
fixed cost plus a slow tail, not proportional to lines. ESTIMATE: four items of 200 lines
reviewed as one 800-line request take about 8.3 min instead of 4 x 6.2 = 24.8 min, a factor of
3 less reviewer time for the same lines, if the number of rounds does not grow.

Review minutes spent per completed item (all its review runs): p50 6 min below 50 lines,
16 / 30 / 45 / 51 for the next buckets; summed 28 / 92 / 192 / 176 / 63 hours by bucket. The
two middle buckets (50 to 999 lines) take 460 of the 551 hours.

### 1.3 What the findings are worth, by size

Triaged findings by item size: confirmed 55% / 53% / 49% / 55% / 54% (n = 310 / 674 / 1 900 /
2 185 / 1 058). Refuted shares 45 / 47 / 51 / 45 / 46%. Precision does not depend on size
in this range: a bigger batch will not by itself drown the author in false findings. (The
same corpus: HIGH severity 21% confirmed, MEDIUM 53%, LOW 79%, from `review-scaling.md` 3.)
review-scaling's other finding applies in full: confirmed findings per reviewed kchar fall from
0.086 (< 5k chars) to 0.006 (40k+), so a large unit is reviewed with LESS depth per line. That
is the counter-force to batching and the reason for line-sized review units (D-BG-6).

Review runs per completed item: p50 2 / 4 / 5 / 6 / 6 by size bucket (mean 3.3 to 5.7, p90 6 to
9). The round budget (`[review].max_rounds` = 2 per gate) explains the plateau: 2 gates x 2
rounds. So "4-7 rounds each" is two gates times two to three rounds; a batch pays those rounds
once per unit instead of once per item.

### 1.4 Full-suite checks and how often they are red

- The merge-stage ci (`ci.result stage=merge`, the CI command set on the merge result): 840
  passed, 16 failed = 1.9% red. ESTIMATE, independent members: a batch of k is red with
  probability 1-0.981^k: 7% at k=4, 14% at k=8; a culprit search costs about log2(k)+1 extra
  runs of the suite on that rare path.
- The per-item `ci` gate fails more (32% of 1 753 results; the first ci run of an item passes
  in 63% of 806 items; mean 1.98 runs per item), rising with size: 25% (< 50 lines), 29%, 38%,
  38%, 39%. The reasons that name a test are mostly cross-item guards:
  `test_constants_ratchet`, `test_architecture_guards::test_ratchet`, golden output snapshots,
  scenario tests. Those are the tests two parallel items break TOGETHER (each adds a knob;
  the ratchet counts both). They stay always-run guards at item level (`ci_scope.always`) and
  run again on the combined tree: batching moves nothing there, it only stops running them
  N times.
- unit_tests fails 4% to 9% by size.
- Merge gate failures (7% of 1 238) are dominated by one cause: "Your local changes to the
  following files would be overwritten" (69 of ~80), a dirty-tree stash failure, not a code
  failure. Filed as a defect (section 8).

### 1.5 Can items be batched at all: overlap

Merged items grouped by merge time into windows; group size and share of groups in which
two members changed the same non-generated file (generated: `tests/golden/`, README, docs,
upgrade templates):

| window | groups | items per group mean / p50 / max | groups with a source-file overlap | including generated files |
|---|---|---|---|---|
| 1 h | 280 | 3.7 / 3 / 15 | 37% | 51% |
| 2 h | 174 | 6.0 / 6 / 22 | 64% | 70% |
| 4 h | 96 | 10.8 / 10 / 36 | 81% | 84% |
| 8 h | 53 | 19.6 / 19 / 49 | 91% | 92% |

Merges arrive at about 3.7 per hour. Leases already forbid two RUNNING items holding the same
glob, so an overlap inside a window is a SEQUENCE (B started after A released), not a parallel
pair. Consequence: a batch can only be formed from items whose changed paths are pairwise
disjoint (the lease rule), and a batch window of 1 h would find a conflict-free subset most
of the time while a 4 h window would almost always have to split. Hot files (`config.py`,
`api/gates.py`, README tables) are the reason; generated files are regenerated once per batch
from their recipes (`pins`), as the merge queue already does per entry.

### 1.6 Wait versus run: what could not be measured

`gate.started` is written after ci's lock is taken: for ci, unit_tests, rubber_duck and
critic the wall time between `gate.started` and the result equals `elapsed_s` to the second
(median wait 0.0 min, p90 0.1 min; 2 h of 192 ci hours). The queue wait the operator sees
(bug B1fa77a6832: three consecutive `gate run ci` attempts of about 30 min each ended
"blocked on <sha> (pid N)", no FIFO) is therefore NOT in the event log. `lease.waiting`
exists for file leases only. UNKNOWN: the real ci queue wait. BG-measure adds `lock_wait_s`
(and queue position) to gate evidence so the claim can be checked after fx-cilock lands.
One thing is certain from the host rule `[ci].max_concurrent_suites = 1`: at ~10 agents the
full suite is a single shared server, so any reduction in the NUMBER of suite runs is a
reduction in waiting, not only in CPU.

### 1.7 Cost of waiting for a batch (the price of the idea)

Batching trades item latency for throughput. At 3.7 merges per hour a count trigger of N=4
fills in about 65 min on average (ESTIMATE, Poisson), longer than the median cycle time
(1.1 h). So: an age trigger T is mandatory, and the member's lease must not be held while
it waits (section 4.3). Without those, batching would slow single items and keep their globs
locked, which is the opposite of the goal.

## 2. Best practice (web; UNVERIFIED marks where no primary page was reached)

- Merge queues test the PR on top of the base AND the PRs ahead of it. GitHub's merge queue
  builds a merge group (latest base plus the queued PRs) and has separate settings for build
  concurrency and for how many PRs go into one group. A third-party doc describes
  concurrency as how many groups are tested at once; I did not reach GitHub's own page for
  the exact option names (UNVERIFIED).
- Bors (try/staging/master) and Mergify: a batch passes or fails as a unit; if the batch of
  #1+#2+#3 is green the queue knows #1 and #1+#2 are also fine and lands all. Cost: blast
  radius, one bad PR fails its batch (Mergify, vendor source). Mergify can run several
  batches in parallel (speculative checks) trading cost for throughput.
- Zuul's dependent pipeline tests each change on top of the ones ahead of it in parallel,
  assuming the ones ahead pass; when one fails, those behind are retested without it. A
  pipeline `window` caps how many changes at the head run jobs at once (Zuul docs, fetched
  via search snippets).
- Kubernetes Tide made batch testing/merging the cornerstone of the merge bot (replacing
  Submit Queue); dynamic batch sizing and concurrent batches were listed as future work
  (SIG Testing slides).
- Uber SubmitQueue: speculative validation of several changes against predicted future
  states of HEAD, isolating the failing change and retrying the others; later work reports
  that landing out of order is safe when every path in the speculation tree has the same
  outcome and cut wait time 74% (Uber engineering blog, via search summary).
- Google runs most tests post-submit in batches, and builds "culprit finders" to find the
  change that broke a batch, which flakiness complicates ("What Breaks Google?", ICST 2023
  workshop; search summary). This is the shape of option B+C: batch, then search for the
  culprit.
- Chromium commit queue and GitLab merge trains: no source reached by search; from memory
  (UNVERIFIED) both run each change on top of those ahead of it (train) or try-jobs per
  change, so they are per-change speculative, not batched.
- Review size: the SmartBear/Cisco study is commonly cited as "review 200 to 400 lines at a
  time, defect detection falls beyond, and faster than about 500 LOC per hour finds fewer
  defects"; the figures are the vendor's interpretation of its data from 2006
  (SmartBear / Atlassian summaries). The ddflow data has the same shape for a different
  reviewer: per-char yield drops as the diff grows (review-scaling 3). Google's own study of
  9 million reviews reports a median of one approver and review latency under 4 hours, and
  Google's guidance is small changes (small-CL guidance not reached; UNVERIFIED).
- Also standard practice, not searched, from memory (UNVERIFIED): trunk-based development
  with short-lived branches; stacked diffs (Phabricator, Sapling, Graphite); test impact
  analysis (run what the change reaches, full suite on a batch or nightly: ddflow's
  `testselect` + `ci_scope` is this); quarantine and rerun of flaky tests (ddflow
  `services/flakes.py`); just-in-time defect prediction scoring change risk (size, files,
  author history) to choose review depth (ddflow's risk tiers in D-gate-economy-r3 (b) are
  the manual form).

Pattern across all of these: the ONE heavy, shared, global check is batched and bisected;
the per-change checks stay per change; reviews are sized, not pooled without limit.
That is exactly options B then C.

## 3. What ddflow has to reuse

| need | existing | where | gap |
|---|---|---|---|
| ordered landing, serial integrator, lock | merge queue: `queue add/run/status/remove`, entries in `.ddflow/local/mergequeue/`, parks failures, regenerates pins | `services/mergequeue.py`, `api/mergequeue_run.py`, `api/mergequeue_add.py`, `[merge_queue]` | runs ci once PER entry; `max_batch` is a loop bound, not one combined run; no queue-wait record |
| combined full run decision | D-gate-economy-r3 (a): affected tests per item, full CI per batch | decision, `services/testscope.py`, `ci_scope` evidence | the "batch" half is unbuilt |
| batch-integration plan | task B-batch-integration (open): integration ref, pipeline once, red batch bisected, result recorded on every item | queue | not started; overlaps BG-ci/BG-fanout; reconcile (D-BG-10) |
| culprit search | `ddmin` (1-minimal failing subset) and `bisect` with a probe callback | `services/bisect.py`, `api/bisect.py` | probe is for test files; a member-level probe = ci on the merge of a subset |
| ci host lock | `suitelock.hold`, `[ci].max_concurrent_suites`, `suite_wait_s` | `services/suitelock.py` | no FIFO (fix-B1fa77a6832 in flight) |
| test selection | reverse import closure, always-run guards, `unit_tests_scope`, `covered_by` evidence | `services/testselect.py`, `testscope.py`, `testreach.py`, `api/gates.py` `_covered_by_ci` | `covered_by` is the model for "this gate is covered by batch run X" |
| review chunking with attribution inputs | chunked parallel review, `max_chunk_chars`, per-chunk findings with `location`, manifest of the whole change, `reviewed_paths`, `exclude_globs`, delta rounds, `combined_gates` + `review_id` (one request, two gates) | `services/review.py`, `review_manifest.py`, `review_exclude.py`, `api/review*.py`, `[review]` | chunk to member map; per-member evidence from one request |
| standards on a range | roborev one range job `--since <merge-base>`, queue-aware wait | `services/roborev_branch.py` | already combined-range: just point it at the batch range |
| gate pipelines per kind | `[gates].kind_pipelines`, doc pipeline by diff (`docsonly`), `pipeline_diff.effective_pipeline` | `config_sections/gates.py` | a `batch_gates` list |
| periodic batch gates | cadence: integration_tests, dedupe_sweep, architecture_review | `services/cadence.py` | batch ci can reuse the "due" evaluators for age/count triggers |
| item graph, overlap | `ItemGraph` (GR-itemgraph, open), lease glob overlap (`globcheck`, `leases`) | `core/itemgraph.py`(planned) | queue ordering by needs and overlap is already assigned to B-batch-integration |
| atomic multi-record writes | `api/batch.py` (`batch`/`fold`: all events under one lock) | `api/batch.py` | name clash: `ddflow batch` is the edit batch; use `queue` |
| config layering, company floors | `strictest=` on knobs, `[org]` layer (D-org-config) | `config_sections/_docs.py` | tighten-only knobs for batch limits |
| approvals for overrides | P-remote-approvals (ask engine, single-use approvals bound to a command) | `services/ask`, `services/approval` | an override of a company floor goes through it |

Not reusable as is: the merge queue's entry (`Entry`) has no members and no group result;
gate outcomes are written one item at a time (`api/gates.py` record path), so fan-out needs
one transaction that writes N outcomes (as `api/batch.py` does for edits).

## 4. Constraints a design must hold

1. Attribution. A finding or a red test must name its owning member. Review findings carry
   `location` (file:line) and a chunk id; with pairwise path-disjoint members a path maps to
   exactly one member, so attribution is a lookup in a manifest {path: member}. Shared
   generated files are excluded from review already (`exclude_globs`, pins).
2. One bad member must not hold the batch. Culprit search (ddmin over members) on a red
   combined run; the rest land. A confirmed review finding sends only its owner back to
   fixing; the owner is dropped from the batch (its other members' outcomes stand because
   disjoint paths).
3. Independence. Existing rule: at least one reviewer from a different model family than the
   author. In a batch the authors may differ; a review unit is valid for member m only if one
   reviewer family differs from m's author family; otherwise split the unit by author family.
4. Item-level evidence. "Bug not closed without a regression test that fails against the
   unfixed code" and "every gate carries an outcome" are per item and stay so: the regression
   proof is produced in the item's own worktree BEFORE it is eligible for a batch; batch
   results are written as gate outcomes on each member (evidence names the batch run), never
   as a silent skip.
5. Leases. A member must not keep its file leases while it waits for the batch (hot files;
   queue wait p90 19 h, 4 h behind glob conflicts). Staging: after its per-item gates pass the
   member is committed on its branch and enqueued; its lease is released; its paths are
   recorded in the batch manifest and checked disjoint at batch formation, with a re-check at
   merge (another item may have landed on the path since).
6. Review depth. A unit above a size limit is split (review-scaling: depth per line falls on
   large diffs); the limit is a knob, default from the SmartBear range and the measured knee
   (see decision D-BG-6).
7. Nothing weakened by size alone: D-gate-economy-r3 (b) already says review depth = the
   larger of the size rule and the risk tier. A member on a security/engine/hook/lease glob or
   tier:deep keeps its own review; it may still join the batch for ci.

## 5. Options

### A. Keep per-item gates; fix the lock and the rounds only

fx-cilock FIFO, review-state fixes (fix-B4ab42dbedb, fix-Ba93aec8448, fix-Bdfc7ed1299), split
on truncation, round cap. No new concept. Gains: fairness and fewer wasted rounds; no
reduction in the NUMBER of suite runs or review requests. Risk: none. This is a prerequisite
for B and C anyway.

### B. Batch only ci, unit_tests (full) and standards

Per-item: research, rules, implement, rubber_duck, critic, bug_hunt, dedupe, the regression
proof, plus lint/build/affected tests (cheap). Per batch: the full CI command set once on the
merge of all members, unit_tests full once, roborev range once over the batch range. Culprit
search on red. Gains (ESTIMATE, from 1.1 and 1.4): ci+unit_tests are 368 run hours for 1 076
items; a batch of 4 replaces 4 suite runs by 1 plus 7% bisect overhead, so 55 to 65% of the
FULL-suite part. How much of the 368 h is "full suite" versus affected-tests is not separable
in the log (`ci_scope.scope` is `selected` in 603 results; full otherwise): UNKNOWN, so the
range is stated for the suite part only. Risk: low (1.9% red rate; bisect is a solved
pattern); no review semantics change.

### C. Batch everything, reviews sized by changed lines

B plus rubber_duck/critic (and bug_hunt, dedupe, optionally) per REVIEW UNIT: the batch diff
is cut into units of at most X changed lines (default 400; chunk boundaries at member
boundaries first, then at file boundaries inside a member, never across a member when it can
be avoided), each unit reviewed by the usual two gates (or the combined request under
`combined_under_lines`), findings mapped back by path to members. Small batch total
(< `skip_below` lines, default 0 = never) may skip the second reviewer only if the company
floor allows. Gains (ESTIMATE): review is 562 h; at equal rounds, units of ~800 lines cost
about a third of the reviewer time of the same items reviewed alone (1.2) and sit in the
cheapest part of the findings-per-minute curve; realistic saving 40 to 55% of review hours
(220 to 310 h per 3 weeks) once rounds per batch rise a little (a bigger unit finds more per
round: 4.15 findings per run at 400-999 lines against 1.4 below 150). Risks: attribution
errors, a reviewer of a different family not available for every author mix, the unit
fix-and-re-review loop touching several owners at once (a single owner's fix would trigger a
delta over their files only: delta rounds exist), depth per line. Needs the review-state
fixes first.

### Comparison

| | A | B | C |
|---|---|---|---|
| suite runs per item | 1 (fair queue) | ~1/k | ~1/k |
| review requests per item | 4-6 | 4-6 | ~(4-6)/k x (1+growth) |
| new concepts | none | batch record, group ci, culprit search | + review units, attribution, family split |
| item latency | unchanged | + wait for trigger (bounded by T) | + wait |
| independence/regression rules | untouched | untouched | per-item enforced at fan-out |
| depends on | fx-cilock | fx-cilock, B-batch-integration reconciled | + review-state fixes |
| risk | none | low | medium |
| estimated saving of 930 gate hours/3 wk | ~0 (queue time, not run time) | ~100 to 140 h (ESTIMATE: 55 to 65% of the full-suite share, taken as 40% of 368 h) | B plus 220 to 310 h of review |

## 6. Recommendation and rollout

1. Land A's prerequisites (fx-cilock FIFO fix-B1fa77a6832; fix-B4ab42dbedb, fix-Ba93aec8448,
   fix-Bdfc7ed1299 for review state and round counting).
2. BG-measure: record `lock_wait_s`, queue position, batch-eligible flag; derive per-item
   size and the overlap table into `ddflow metrics` so the figures above are reproducible.
3. Ship B (BG-record, BG-trigger, BG-ci, BG-fanout) with the knob off by default; turn on for
   the ddflow project itself; compare ci hours per completed item and cycle time against this
   document's baseline (0.86 gate hours per item, 0.52 review hours, first-try merge-ci green
   98.1%).
4. Ship C (BG-review) behind `[batch].review = "off"` (default), enable on the 50-999 line
   items first (the bucket with 460 of 551 review hours), keep tier:deep and glob-risk items
   on per-item review. Evaluate with the review replay corpus (RV-replay-corpus) for recall,
   and with triage precision per unit size.
5. Company floors: tighten-only limits (D-BG-8).

What would invalidate this:
- If the real ci queue wait is solved by FIFO plus capacity (A alone brings waiting to near
  zero) AND the suite parts of ci/unit_tests are small in hours, B's saving shrinks to its
  CPU part (BG-measure decides, not this document).
- If rounds per unit grow in proportion to lines (review time per item flat), C saves only
  the fixed price, about a third; if they grow faster than the fixed cost falls, C is a loss.
  The first month of C data decides.
- If most parallel items overlap on hot files so that batches rarely exceed 1 to 2 members
  (the 1 h table: 37% of windows had an overlap), the batch size would sit at ~2 and the gain
  is small; waves/lanes planning that gives items disjoint globs raises it.
- If reviewers cannot be matched to authors by family inside a unit, C degrades to per-item
  review.
- A model or host change that makes the review fixed cost small (low reasoning effort,
  RV-effort-gate) removes C's main source.

## 7. Planned work that overlaps

- fx-cilock / fix-B1fa77a6832: FIFO for the ci lock. Prerequisite; the batch run uses it.
- B-batch-integration (open): the same idea for ci only, from 2026-09-28; BG supersedes its
  scope (it needs P-unify and GR-itemgraph; BG-ci should not wait for P-unify). Decision D-BG-10.
- P-review-quality (RQ-metrics, RQ-verifier, RQ-confidence): precision and confidence per
  finding; inputs to unit sizing; RQ-metrics gives per-reviewer precision.
- RV-replay-corpus and the other follow-ups of `review-scaling.md`: recall measurement, the
  only way to verify that a unit size does not lose findings.
- fix-Bdfc7ed1299, fix-B4ab42dbedb, fix-Ba93aec8448: review round accounting and force/delta.
- GR-itemgraph (P-graph): effective scheduling edges; batch formation uses it, no second edge
  derivation.
- P-remote-approvals: an agent overriding a company floor (e.g. skipping the per-item review
  above X lines) needs a recorded single-use approval; BG-policy uses that engine.
- P-tool-diet (MTD-family-spec done, MTD-profiles open): `ddflow_queue` is mapped to the view
  family action `queue`; a batch option needs a family decision there (BG-mcp).
- Lanes and waves (`ddflow lanes`, B-gap-lanes done; wave labels in P-labels): the natural
  source of "who is working in parallel now"; a wave end is a batch trigger.
- P-orphan-recovery: a batch that crashed mid-run is an orphan; BG-record keeps the record
  in the log so recovery can see it.

## 8. Defects found while measuring

- A merge-gate failure dominated by "Your local changes to the following files would be
  overwritten" (69 of the 80 merge-gate failures since 2026-10-01): filed as bug B90939a78dc (fix-B90939a78dc).
- `gate.started` is written after the host lock is held, so lock queue time is invisible in
  the log and in `ddflow metrics`; filed as bug B1d51fb9da1 (also a BG-measure input).
- `ddflow metrics` has no per-item size and no overlap view, and "review rounds per item"
  counts runs of both gates; covered by task BG-measure (no separate record).

## 9. Limits of this study

Sizes are the item's final merge diff, not the diff each round saw (rounds see deltas):
the by-size findings and times are therefore per ITEM size, not per request size. "Findings
per run" counts findings reported, not triaged. Elapsed time is the reviewer's, on a fleet
with a 10x daily swing in decode speed (review-scaling 1.4). Independence of member failure
is assumed in 1.4. The web pages for GitHub, GitLab and Chromium were not reached.
