# Batched gates: decision proposals

Status: **PROPOSAL** (nothing here is decided; the operator answers in task `BG-decide`).
Evidence: `docs/ddflow/research/batch-gates.md` (section numbers refer to it). Phase
`P-batch-gates` (prefix BG-). Governing records: `D-gate-economy-r3` (full CI per batch;
review depth = larger of size and risk; delta re-review), `D-review-rounds-4`,
`D-mcp-tool-families` / `docs/ddflow/decisions/mcp-tool-diet.md` (no new tool),
`D-config-layer-order`, `D-org-config` (company floors, tighten-only), `r-everything-configurable`,
`r-company-workflows`, `D-failed-review-gate`, `D-reviewer-trust-r2`, `D-shared-engines-r2`.

Operator request (2026-10-22, verbatim): "Can you research for an option of execution that
ddflow shall support where several tasks or bugs are implemented in parallel and then they
wait for the common roborev and other gates to be checked based on the size of the changes
(so review, critic per X changed lines) so we can speed up the development and make the gates
and review steps more rare and supposedly faster? Is this make sense? ..."

## D-BG-1 Two execution modes; per-item stays the default

`[batch].mode`: `off` (default, today's behaviour), `ci` (option B: batch the full-suite
checks), `full` (option C: also batch the model reviews, sized by lines). A project, a company
or one invocation can pick; a company floor can forbid `full` (D-BG-8). Recommended
rollout: `ci` on for ddflow itself once BG-ci is green, `full` only after the numbers.

## D-BG-2 What is per item and what is per batch (never moves)

Per item, always, before an item is batch-eligible: research, rules, implement, the
regression-test proof (fails on the unfixed code), lint/build/security hooks and the affected
tests, `bug_hunt` and `dedupe` (until a later decision). In mode `full` also the reviews of
any member on a risk glob or tier:deep (D-gate-economy-r3 (b)).
Per batch: the full CI command set once on the merge of the members (the queue already
regenerates pins and runs it per entry today), `unit_tests` full, `standards` (roborev range
job over the batch range), and in `full` mode `rubber_duck` and `critic` per review unit.
`merge` and `complete` stay per item.

## D-BG-3 The batch is a record in the log

`batch.formed` (id, members with tip shas and path manifest, trigger, base sha),
`batch.gate_started|finished` (gate, command digest, tree sha, elapsed, queue wait),
`batch.culprit` (member, search runs), `batch.landed`, `batch.dissolved` (reason). Members get
ordinary `gate.passed|failed|unavailable` events through ONE transaction, with evidence
`{batch, batch_gate_run, covered_by: "batch", members, tree_sha}` (same shape as the existing
`covered_by` for unit_tests). A gate that did not run for a member is `unavailable`, never
`passed`. Machine-local queue state stays in `.ddflow/local/mergequeue/` (the batch is the
queue's group), the log is the source of truth.

## D-BG-4 Triggers (the "X"): count, lines, age, wave end

A batch closes at the first of: `max_items` ready eligible items (default 4), `max_lines`
changed lines in total (default 800), `max_age_min` since its oldest member was enqueued
(default 45), the end of a wave/lane when one is declared, or `ddflow queue run --now`. A
single eligible item at the age limit runs alone (mode degrades to per-item, never waits
forever). Members are chosen oldest first among items whose path sets are pairwise disjoint
(lease overlap rule; generated pin files excluded and regenerated once). Rationale:
merges arrive ~3.7 per hour (1.5), so count 4 alone would take about an hour; the age trigger is
what keeps latency near today's median cycle time.

## D-BG-5 Failure: find the culprit, land the rest

Red combined run: ddmin over the members (`services/bisect.ddmin`, probe = the CI command set
on the merge of a subset), flaky guard by repeat (`command_probe(repeat=)`). The culprit
set (one member, or an interacting pair) is dropped from the batch, recorded failed on those
members only, returned to its owner with the failing output; the others re-run once on the
reduced set unless the culprit search already proved that subset green. A second red on the same
reduced set is a flake report (`services/flakes`), not a loop: the batch dissolves into
per-item merges. A batch never retries more than `max_culprit_runs` (default 8).

## D-BG-6 Review units sized by lines (mode full)

The batch diff (excluding `exclude_globs` and pin files) is cut into review units of at most
`review.unit_lines` changed lines (default 400, the upper end of the SmartBear/Cisco
200-400 range and the measured knee, 1.2). Cuts fall on member boundaries first, then file
boundaries inside a member; a member larger than the unit limit is reviewed alone as today
(chunked). Each unit is reviewed by rubber_duck and critic, or by the one combined request
when the unit is under `[review].combined_under_lines`. Every chunk carries the manifest
{path: member}; a finding is attributed to the member owning its `location` path
(path-disjoint members make this a lookup; a path outside every member is attributed to the
batch and blocks the batch). A confirmed finding returns ONLY its owner to fixing; the
owner's fix is a delta review over the owner's files (existing delta machinery), not a new
unit. Round budget: `[review].max_rounds` counts per unit and gate; a unit that spends it is
dissolved into per-item review for the members it still holds findings on.
Size policy (the `review per X lines` semantic): tiny total (< `skip_below_lines`, default 0)
may rely on ci + standards only when no floor forbids it; medium (one unit) one combined
review; large chunked units. Default for skip is 0 (never) until measured.

## D-BG-7 The two enforced rules, per item, unchanged

- Different model family: a review unit is valid for member m when at least one of its
  reviewers' families differs from m's author family; the unit's reviewer set is chosen to
  satisfy every member, else the unit is split by author family. Evidence per member records
  reviewer, family and `batch`. A unit that cannot satisfy it is `unavailable` for those
  members.
- Regression test: `bug fixed`/`complete --regression-test` are refused per item exactly as
  now; a bug fix is batch-eligible only after its proof is recorded (the proof runs in the
  item's own tree, never on the combined tree).
- "Every gate carries an outcome": fan-out writes an outcome on every member for every batch
  gate, including `unavailable` when the batch run could not run.

## D-BG-8 Config and company floors

Section `[batch]`: `mode`, `max_items`, `max_lines`, `max_age_min`, `review_unit_lines`,
`skip_below_lines`, `max_culprit_runs`, `risk_globs_alone` (members that always review alone).
Layering as every section (D-config-layer-order); four ways to set each (file, env, CLI
flag, MCP argument), logged (`r-everything-configurable`). Tighten-only floors for a
company: `mode` may be forced no looser than `ci`; `review_unit_lines` has a maximum;
`skip_below_lines` a maximum (0 allowed); `risk_globs_alone` can only grow; a lower
`max_items` is stricter. Overriding a floor for one batch goes through the approvals engine
(P-remote-approvals), recorded and single-use.

## D-BG-9 Surfaces: no new MCP tool

CLI: extend `ddflow queue` (`queue add --batch`, `queue run --batch [--now]`, `queue status`
shows batches, positions and the next trigger) and `ddflow metrics batch`; no new top-level
command unless BG-cli finds the queue verb overloaded, in which case `ddflow batch ...`
(the name is taken by multi-edit `batch` today: rename risk, so avoid). MCP: the `queue`
entry already exists (`ddflow_queue`, mapped to family `view`/action `queue` in
mcp-tool-diet.md); batch options are arguments of it, and a mutating `run` in a view family is
settled by MTD-profiles (BG-mcp). No tool is added. Prompt-based management: the implement-phase
driver and a plain-prompt "batch these" workflow; `ddflow brief` says which items are
batch-eligible and when the next batch closes.

## D-BG-10 Relationship to B-batch-integration

`B-batch-integration` (open, 2026-09-28) is this idea for ci only and is subsumed by BG-ci /
BG-fanout; its tests (a red second member is named and the first lands) become BG-e2e cases.
Proposal: fold it into BG-ci (`ddflow task fold`) when BG-ci starts; do not run both.

## Open questions for the operator (BG-decide), with recommended defaults

1. Mode default: `off` now, `ci` on for ddflow after BG-ci, `full` later? Default yes.
2. Triggers: max_items 4, max_lines 800, max_age 45 min? Default yes.
3. Review unit size 400 lines? Default yes (tune with replay corpus).
4. May a tiny batch skip the second reviewer (`skip_below_lines`)? Default no (0).
5. Risk globs (security, engine, hooks, lease) always review alone? Default yes.
6. Fold B-batch-integration into BG-ci? Default yes.
7. Should bug_hunt and dedupe move to the batch in `full` mode? Default no.
8. Company floor: forbid `full` for security-regulated projects? Default: allowed, floor knob exists.
9. Bisect: drop culprit and land the rest, or all-or-nothing? Default drop and land.
10. Retention of the batch record in the log: forever (it is the audit)? Default yes.

## What would change this proposal

See research section 6, "What would invalidate this": measured queue wait near zero after
the FIFO fix and small suite hours (B shrinks), rounds growing with unit size (C loses),
overlap-dominated windows (batches of 1 to 2), no reviewer of another family for the author
mix (C degrades).
