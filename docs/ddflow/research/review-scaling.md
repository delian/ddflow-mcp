# Review time versus code size: cost model, levers, accuracy

Task RV-scale-research, agent fleet-res-review, 2026-10-19. All numbers below were
computed from the committed event log (`.ddflow/events`, 4 265 critic/rubber_duck/bug_hunt
review records with `elapsed_s`, 6 118 distinct findings, 5 464 triage verdicts), from
roborev's 110 finished job logs (ids 817..3104, `~/.roborev/logs/jobs`, read only), and
from the vLLM `/metrics` of both reviewer hosts. Labels: MEASURED (a number from this
data), ESTIMATED (derived from measured numbers by arithmetic), UNKNOWN (no data).

## 0. Verdict on the falsifier

The falsifier ("time is already linear in diff size and the delay is queueing/host load
only") is REFUTED on both halves, for a different reason than expected:

- Time is NOT linear in diff size. It is weakly SUB-linear: log-log exponent 0.26-0.41,
  R^2 0.17-0.41, linear R^2 0.00-0.21. Diff size explains 0-20% of review time.
- It is NOT queueing. vLLM queue time is 0.0004 s/request on the Pro host and 0.12 s on
  the Flash host; prefill is 0.1 s and 0.26 s. DECODE is 99.4% of request time on both.
  The delay is the number of tokens the model GENERATES (mostly reasoning, 77% of roborev
  output tokens) divided by the per-stream decode rate, and that count is a heavy-tailed
  random variable that diff size barely moves. Host load matters only through the decode
  rate (see 1.4), not through queueing.

So the lever is not "linearise a super-linear curve" but "bound the generated tokens per
request and the number of sequential requests", which makes the tail, not the mean, the
thing to fix.

## 1. Cost model

### 1.1 ddflow chunked review (critic / rubber_duck), 3 795 REVIEWED records with chunks

Chunks are `max_chunk_chars=5000` in the log (3 863 of 4 265 records), sent in ONE
parallel wave with hedge 2 (review.py `max_concurrency=0`, `hedge=2`), so a review
takes about the slowest chunk, not the sum.

| diff chars | reviews | chunks (median) | elapsed median s | p90 s | max s | s per kchar (median) |
|---|---|---|---|---|---|---|
| 0-5k | 1 090 | 1 | 171 | 548 | 1 506 | 68.5 |
| 5-10k | 838 | 2 | 310 | 837 | 1 696 | 44.3 |
| 10-20k | 658 | 3 | 366 | 814 | 2 173 | 25.3 |
| 20-40k | 652 | 6 | 490 | 1 101 | 2 558 | 18.1 |
| 40-80k | 310 | 9 | 589 | 1 215 | 1 882 | 10.9 |
| 80k+ | 247 | 14 | 490 | 977 | 2 186 | 0.55 |

Fits (all 3 795 records): elapsed = 426 s + 0.10 s/kchar, R^2 = 0.00; log-log exponent
0.27, R^2 0.19. Elapsed against chunk count: 328 s + 25 s/chunk, R^2 0.12. Full-diff
reviews (n=2 775) and delta rounds (n=1 020) fit the same way (exponent 0.26 / 0.28).
MEASURED. Reading: a fixed cost of about 3 minutes for one chunk, a slow rise to
about 8-10 minutes once there are many chunks, then a plateau. The plateau is the
parallel wave: more chunks cost more concurrent decode streams, not more wall time, until
the host saturates. Median seconds per chunk (elapsed / chunks) swung 29-304 s per day
(2026-10-13: 29; 2026-10-19: 304), a 10x spread with identical settings: that is the host
fleet load, see 1.4.

Failure share (MEASURED): 161 of 4 265 records (3.8%) did not review everything
(115 PARTIAL, 46 UNAVAILABLE); the median elapsed of those is 1 644 s, 3-10x a normal
review. Causes: 70 "TRUNCATED: the model consumed all N tokens", 12 off-contract
(answer without a STATUS block), 8 + others timed out or HTTP errors. Every one of them
is a reasoning blow-up or a wall-clock loss, not a size effect.

### 1.2 roborev (kilo agent loop), 110 finished jobs

roborev's job is an agent loop: each model step re-sends the whole transcript. Per job:
steps, input tokens summed over steps, output plus reasoning tokens (MEASURED from
`step_finish` events in the job logs).

| changed lines | jobs | duration median s | p90 s | steps (med) | input tokens (med) | output+reasoning (med) | s per step |
|---|---|---|---|---|---|---|---|
| 0-30 | 42 | 130 | 280 | 14 | 0.93 M | 9.7 k | 8.5 |
| 30-100 | 33 | 203 | 351 | 16 | 0.94 M | 12.2 k | 14.3 |
| 100-250 | 23 | 336 | 546 | 26 | 2.27 M | 18.0 k | 12.8 |
| 250-1000 | 11 | 489 | 678 | 35 | 3.99 M | 28.1 k | 13.8 |
| 1000+ | 1 | 576 | 576 | 28 | 2.13 M | 27.1 k | 20.6 |

Fits (n=110): duration = 215 s + 0.62 s/changed line, R^2 0.21 (log-log exponent 0.41,
R^2 0.41). Duration against output+reasoning tokens: R^2 0.51 (best single predictor);
against steps R^2 0.32; against input tokens R^2 0.26. Steps against changed lines:
16.5 + 0.029/line, R^2 0.22 (exponent 0.25).

Where the super-linearity is (MEASURED): input tokens against steps is 1.25-power
(log-log exponent 1.25, R^2 0.93; quadratic fit R^2 0.90). Because every step re-sends
the transcript, which grows 1.7 k tokens/step on a 59 k base, total input is
quadratic-ish in steps: 94.5% of all input tokens are re-sends (sum of
max-context / sum of input). But the host serves them from the prefix cache (92.5% hit on
the Pro host, 80.5% on Flash), prefill is 0.1-0.26 s per request, so input re-sending
costs tokens on a meter, not wall time. Wall time is decode: output+reasoning tokens at
24-87 tok/s. The only part that scales with code size is the step count
(0.03 steps/line), and that scales sub-linearly.

Summary of the shape: duration ~ steps x seconds-per-step, steps ~ roughly constant 14
plus slow growth, seconds-per-step ~ reasoning tokens in that step / decode rate. The
whole review is dominated by one or two long reasoning steps (#836: 1 of 24 steps took
906 of 1 288 s, from research Rfa535c6747), which is why the p90 is 2-3x the median at
every size.

### 1.3 Per-chunk reasoning blow-up (the actual super-linearity)

Research Rfa535c6747 measured the same 3 KB diff eight times: 64-391 s, 5.9k-41.6k
tokens (a 6.6x spread for IDENTICAL input), findings in 2 of 8 runs. This session's
vLLM averages agree: the Flash host generates a mean 1 154 tokens per request but its
mean request lasts 47.7 s and decodes at 24.4 tok/s per stream with 127 requests
running. A reasoning model's token count is not a function of the input size, so the
tail of the max over N parallel chunks (and over hedge copies) grows with N: parallel
chunking removes the SUM but leaves the MAX of N heavy-tailed draws. That max rises
with N, which is the 171 s -> 589 s step in the table (1 chunk -> 9 chunks). It is
super-linear only in the sense of the tail: the 1 % worst chunk becomes the median
review once there are 100 chunks.

### 1.4 Host load: decode rate, not queue

| host | role | requests | mean e2e s | mean queue s | mean prefill s | mean decode s | tokens generated per request | decode tok/s per stream | prefix hit | running now |
|---|---|---|---|---|---|---|---|---|---|---|
| host A (Pro: critic, roborev) | Pro, critic, roborev | 31 072 | 5.7 | 0.0004 | 0.10 | 5.5 | 480 | 86.7 | 92.5% | 0 |
| host B (Flash: rubber_duck) | Flash, rubber_duck | 9 354 | 47.7 | 0.12 | 0.26 | 47.3 | 1 154 | 24.4 | 80.5% | 127 |

The addresses of hosts A and B are in the git-ignored .ddflow/local/reviewers.toml.

MEASURED (cumulative counters read 2026-10-19). Decode per stream falls from 128 tok/s
alone to 74 tok/s at 16 concurrent (Rfa535c6747) and 24 tok/s at 127 concurrent: the
host is shared by the whole fleet, so a review's wall time inflates 3.5x between idle
and fleet-busy purely through decode-rate sharing. This is the 10x per-day swing in 1.1,
and it is the part of the falsifier that survives: host load matters, but through
per-stream decode rate (aggregate throughput still scales), not through queueing.

## 2. Levers

| # | lever | status | evidence |
|---|---|---|---|
| 1 | Parallel chunks, bounded fan-out | MEASURED, already shipped | `max_concurrency=0` one wave, hedge 2; a fixed 4 ran 1 301-1 465 s versus 1 122 s in one wave (bug B289bf87e8d). Aggregate throughput 128 -> 1 174 tok/s at 1 -> 16 concurrent while per-stream falls 128 -> 74. Remaining gain: none on idle hosts; on the busy Flash host it adds to the load that slows every other reviewer (24 tok/s at 127 running). A global cap across agents does not exist (UNKNOWN effect). |
| 2 | Hedge 2 (race two copies) | MEASURED benefit, ESTIMATED cost | Cuts the tail (Rfa535c6747: 6.6x spread). Doubles generated tokens and doubles concurrency on a shared host, which lowers everyone's decode rate. Net effect on a busy fleet: UNKNOWN; worth an A/B (follow-up F3). |
| 3 | Split-on-truncate (fix-B587e607ad9) | MEASURED problem, fix in flight | 70 of 161 non-reviewed records (43%) are TRUNCATED chunks at 32k-64k tokens, median record 1 644 s. Splitting turns a lost chunk into two chunks that fit. Recall kept: all (the chunk is still reviewed). |
| 4 | Per-chunk token cap, `max_tokens` | MEASURED trade-off | Qwen3.8-Flash: 6 000 tokens -> 0 chars of answer; 32 000 -> valid verdict after 28 381 reasoning tokens (review.py comment). A cap cuts the tail but converts it to a lost chunk unless the split/retry exists. Only safe together with lever 3. |
| 5 | Reasoning effort per gate | MEASURED, one point | Rfa535c6747 (Flash, 3 KB diff): `low` 13.8 s versus 18.1 s default, lost the one MEDIUM finding. This session (Pro, 3.5 KB real diff): see 2.1. Medium returns HTTP 400 on the Flash host. One diff each: that is NOT enough to rank it. |
| 6 | Temperature caps | MEASURED against | Temperature 0.3 on a reasoning model loops ("let me reconsider") until 64 000 tokens (lesson Lf3feebf5d3, bugs B568e9def3b, B769704d1dc); default sampling is the speed setting. REFUTED as a lever. |
| 7 | Delta rounds (review only changes since last head) | MEASURED, shipped | 1 020 of 3 795 reviewed records are delta rounds. They yield 439 confirmed of 922 triaged findings (47.6%, versus 53.7% for full reviews) and the same exponent 0.28. Delta rounds are about 4x smaller in chars but not faster in time (the 3-minute floor), because the time is the per-request reasoning tail, not the char count. So deltas save tokens and recall-noise, not wall time. |
| 8 | Risk tiers (D-phase-review) | UNKNOWN | Decision record `D-phase-review` does not exist in this log (`ddflow show` says no such item). No data on which tier a task has. ESTIMATED from severity: HIGH findings are only 22% confirmed (163 of 778 triaged), MEDIUM 53%, LOW 79%, so a tier that suppresses re-review of HIGH-only-by-reviewer noise is where precision, not time, improves. |
| 9 | Prefilter by lint/types/tests-reach | ESTIMATED | ruff/architecture ratchets already run in `ci` before the model gates. Of triaged confirmed findings, the share a linter/type check/test could have caught is UNKNOWN: not recorded in the triage events (no `class` field). Needs the follow-up F5 that tags the triage verdict with the cheaper tool that would have caught it. |
| 10 | Prefix caching | MEASURED, saturated | Hit rate 92.5% (Pro) and 80.5% (Flash), prefill 0.1-0.26 s per request, 0.7-5% of request time. Nothing left to gain in wall time. The residual 7.5% / 19.5% miss is cheap. Re-sent context (94.5% of roborev input tokens) is free on this host and a cost only on a metered API. |
| 11 | Pro host for roborev (shipped DX-roborev-speed) | MEASURED | roborev jobs on Pro (3086-3104, 6 jobs): 77-301 s, median 155 s, steps 5-11, versus the Flash-host medians in 1.2 (130-489 s, 14-35 steps). Moving host cut steps 2-3x and duration about 2x on the same kind of commit. |
| 12 | Whole-diff review (no chunking) | UNKNOWN, ESTIMATED against | roborev IS a whole-diff reviewer through tools: input 0.93-4 M tokens per job, 14-35 steps. A whole-diff single request needs the diff in the context window and decodes more reasoning on a larger input (research claim, not remeasured here). Not recommended. |

### 2.1 This session's probe (Pro host, 3 491-char real diff, 3 runs)

Three requests, one input (1 194 prompt tokens, a 3.5 KB commit message plus diff), the
same minimal reviewer prompt, sent concurrently to the Pro host (MEASURED):

| setting | wall s | completion tokens | finish | answer chars |
|---|---|---|---|---|
| default (no effort field) | 206.1 | 20 361 | stop | 33 (no findings) |
| default (identical copy) | 316.8 | 32 000 (all reasoning) | length: TRUNCATED | 0 |
| reasoning_effort=low | 48.5 | 4 726 | stop | 12 (no findings) |

Two identical default calls differ 1.6x in tokens and one of them never answers (the
truncation failure, 1 call in 3 here, 70 of 4 265 in production); `low` is 4.2x faster
than the answering default and 6.5x faster than the truncated one, at 97 tok/s. None of
the three reported a finding, so this probe says NOTHING about the recall of `low`: it
confirms the cost side (tokens and the truncation tail) only. Hedge 2 would have returned
the 206 s copy; a cap with split-on-truncate would have spent 316.8 s to get nothing.

## 3. Accuracy

Corpus: there is no review-recall corpus. `SE-eval-harness` (done) is a planted-fault
harness for workflows, with its own fixtures; it holds no past review findings, and no
other tests directory has one. The de facto corpus is the triage log: 6 118 distinct
critic/rubber_duck findings with digests, of which 2 823 are confirmed, 2 540 refuted
and 755 untriaged. It records WHAT the reviewer said and whether a human or agent
confirmed it. It does NOT record what a different setting would have said, so recall of
a faster setting cannot be computed from it: only the best case where a faster setting
loses nothing is bounded. Measured recall of a faster setting needs a replay: file
follow-up F1.

What the log does show (MEASURED):

| slice | findings | confirmed | refuted | precision |
|---|---|---|---|---|
| all (triaged) | 5 363 | 2 823 | 2 540 | 52.6% |
| severity HIGH | 778 | 163 | 615 | 21.0% |
| severity MEDIUM | 3 681 | 1 949 | 1 732 | 52.9% |
| severity LOW | 904 | 711 | 193 | 78.7% |
| full-diff reviews | 4 161 | 2 235 | 1 926 | 53.7% |
| delta rounds | 922 | 439 | 483 | 47.6% |
| round 1 of the item | 2 636 | 1 402 | 1 234 | 53.2% |
| round 2 | 1 394 | 778 | 616 | 55.8% |
| round 3+ | 150 | 55 | 95 | 36.7% |

Chunk position (reviews with 4+ chunks): first third 679 confirmed of 1 406 findings
(48%), middle 518 of 1 194 (43%), last third 406 of 852 (48%): there is no position
effect, so dropping later chunks is not a safe cheap lever.

Confirmed findings per reviewed kchar by diff size (full reviews): 0.086 (under 5k),
0.070 (5-15k), 0.043 (15-40k), 0.006 (40k+). Confirmed findings per review: 0.26,
0.65, 1.07, 1.78. The per-review yield grows 7x while the diff grows by at least 10x:
large diffs are reviewed with LESS depth per char. That is a COVERAGE finding, not a
speed one: the speed levers must not be bought by pushing even more chars through the
same per-chunk reasoning budget. (Caveat: large diffs also contain generated or doc
content with fewer defects per char; ESTIMATED, not separated.)

Ceiling on what a faster setting could keep: severity HIGH is 21% precise, so a setting
that missed half the HIGH findings would lose at most 82 confirmed findings out of 2 823
(2.9%) but those are exactly the ones an operator reads first: do not trade HIGH.
Round 3+ is 36.7% precise (55 confirmed of 150 triaged): a hard cap of 2 delta rounds loses at most 55 of 2 823 confirmed
findings (1.9%) and bounds the number of sequential rounds an item can accumulate. ESTIMATED (upper bound by arithmetic, no replay).

## 4. Ranked recommendation (gain per accuracy lost)

| rank | change | expected gain | recall at risk | confidence |
|---|---|---|---|---|
| 1 | Split-on-truncate plus a per-chunk token cap, together (levers 3+4) | removes the 3.8% of reviews that take a median 1 644 s (43% of those are TRUNCATED); cuts p90 | none: a truncated chunk is a chunk with NO review today | MEASURED problem; fix ESTIMATED |
| 2 | Run roborev and the critic on the idle Pro host; keep the Flash host for hedge-free bulk (lever 11) | roborev 2x faster and 2-3x fewer steps already measured | none observed (same model family) | MEASURED |
| 3 | Fleet-wide concurrency budget: hedge 1 when the host reports more than N running (lever 2) | restores decode rate, 24 -> 80+ tok/s per stream when the Flash host is busy | tail risk of one slow copy: ESTIMATED small, needs A/B | ESTIMATED |
| 4 | Cap rounds at 2 for an item, then prefer a new reviewer (lever 7) | removes the longest tail items, bounds gate time | at most 1.9% of confirmed findings | ESTIMATED (upper bound) |
| 5 | Effort `low` on the delta and rubber_duck passes only after a replay shows recall (lever 5) | 4.2x faster and 4.3x fewer tokens in the probe (48.5 s / 4 726 tokens versus 206 s / 20 361); 13.8 versus 18.1 s in Rfa535c6747 | lost the one MEDIUM finding in Rfa535c6747: unacceptable until measured | UNKNOWN: needs replay |
| 6 | Tag triage verdicts with the cheaper tool that would have caught them, then prefilter (lever 9) | model sees only the residual | none by construction if the prefilter only removes what the cheaper tool catches | UNKNOWN |
| 7 | More chunk depth for large diffs, not less | recall: 7x more diff gets 7x fewer findings per char | negative speed gain | MEASURED gap |

Not recommended: lowering temperature (refuted), medium reasoning effort (HTTP 400 on
Flash), dropping later chunks (no position effect), sequential chunks.

## 5. Follow-up tasks

| id | title | globs | needs | wave |
|---|---|---|---|---|
| RV-replay-corpus | Build the review replay corpus: for each confirmed finding in the triage log, store (diff at the reviewed head, finding digest) and a runner that re-reviews it at a given setting and reports recall and precision | tests/review_corpus/**, ddflow/services/review_replay.py | SE-eval-harness | wave:3 |
| RV-split-cap | Land split-on-truncate with a per-chunk reasoning cap, and test that a truncated chunk becomes two reviewed chunks (fix-B587e607ad9 extension) | ddflow/services/review.py, tests/test_review_split_nonconverging.py | fix-B587e607ad9 | wave:2 |
| RV-hedge-adaptive | Make hedge adaptive: hedge 1 when the reviewer host reports more than a configured number of running requests (vLLM `num_requests_running`), with an A/B record of wall time and findings | ddflow/services/review.py, ddflow/config.py, tests/test_review_hedge.py | RV-replay-corpus | wave:4 |
| RV-round-cap | Cap delta rounds per item at a knob (default 2) and record `round_capped` in the gate evidence | ddflow/services/review.py, tests/test_review_rounds.py | RV-replay-corpus | wave:4 |
| RV-triage-class | Add a `caught_by` field to review.triaged (lint, types, test, none) so the prefilter lever has data | ddflow/api/review_triage.py, tests/test_review_triage.py | none | wave:3 |
| RV-effort-gate | Per-gate reasoning-effort knob, default unset, decided by the replay corpus | ddflow/services/review.py, ddflow/config.py | RV-replay-corpus | wave:5 |
| RV-evidence-tokens | Record generated and reasoning tokens per chunk in review evidence (`tokens_out`, `tokens_reasoning`) so cost models stop inferring from time | ddflow/services/review.py, tests/test_review_evidence.py | none | wave:2 |

## 6. Gaps and honesty notes

- Triage `confirmed` means a person or agent confirmed it; it is a proxy for ground truth,
  and it counts repeated triage events of one digest only once (dedup by gate and digest).
- `diff_chars` in the review evidence is the reviewed diff, which for a delta round is the
  delta; it is not the whole item size.
- roborev changed-line counts come from `git diff <ref>^1 <ref>` for merge commits; a
  non-merge commit uses its first-parent diff, which is the same measure.
- The vLLM counters are cumulative since each server's restart; they average over all
  tenants, not just ddflow.
- No change was made to code, `~/.roborev` or `~/.config/kilo`; three probe requests were
  sent to the Pro host.
