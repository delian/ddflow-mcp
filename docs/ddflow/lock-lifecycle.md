# Lock lifecycle and change suggestions

Design for phase P-lock-lifecycle. Governed by the decisions
D-lock-lifecycle-answers, D-handoff-peers and D-handoff-autoapply.
Evidence: the research notes R-lock-inventory, R-lock-landscape,
R-lock-landscape-verified and R-handoff-protocol. Every number below is
copied from one of those notes and tagged with it; nothing is estimated here.
Knob names in `[locks]` and `[handoff]` are proposed names; the existing
`[lease].hold_max_s` is real.

## 1. The measured problem

Two measurements exist, over different windows; they disagree on size, not
direction.

| Fact | Value | Note |
|---|---|---|
| Lease hold, all spans (126863 events, 1721 spans) | p50 65 min, p90 367 min | R-lock-inventory |
| Lease hold, 14 days, 490 completed tasks | p50 2.3 h, p90 21.4 h | R-lock-landscape-verified |
| Hold spent in gates and reviews | 45.4 percent (review 27.9, ci/unit 20.1) | R-lock-inventory |
| Hold: last commit to merge / claim to first commit | 21.5 / 20.4 percent | R-lock-inventory |
| Post-merge idle: lease-hours after merge | 33.5 percent | R-lock-landscape-verified |
| Merge to release (inventory window) | p50 0.8 min | R-lock-inventory |
| Wait spans (14 days) | 144 spans, 495 h, p50 1.5 h, p90 11 h | R-lock-landscape-verified |
| False serialization: waits between tasks whose actual changed files were disjoint | 38 of 108 classifiable spans, 145 h, 32 percent | R-lock-landscape-verified |
| Queue wait behind glob conflict | p90 5.6 h, n=1238 | R-lock-inventory |
| Declared globs: declared files never touched / actual files outside the globs | 50 / 45 percent | R-lock-landscape-verified |
| Tasks that wrote undeclared files | 54 of 100 | R-lock-landscape-verified |
| Declared-glob precision (inventory window) | 92 percent | R-lock-inventory |
| Tasks holding more than they change | 29 of 100, mean 2.0x | R-lock-inventory |

Outside evidence (R-lock-landscape-verified): Cursor reports lock-based
coordination reduced 20 agents to the throughput of 1-3; Brun et al. TSE 2013
report 16 percent textual, 1 percent build, 6 percent test conflicts over 5355
merges, and 33 percent of clean merges later failed build or tests. Locks cost
throughput; a clean textual merge is not proof of a good merge.

Conflicts are rare locally: 1 of 68 evaluable concurrent-overlap pairs
conflicted and 0 of 60 sampled in-branch main-merges needed manual resolution
(R-lock-landscape-verified). But 41 of 487 merges (8.4 percent) produced a
red-main bug, mostly lint, secrets and ratchet checks (same note). So the
merge-tree probe is necessary and not sufficient; ci on the merge result
stays mandatory.

## 2. The lifecycle

1. **Lock at first write.** No lease is taken at claim. The first Edit or
   Write on a file takes a per-file lease. A file held by another agent is
   refused (enforced from the start, D-lock-lifecycle-answers item 1).
2. **Hold to merge-ready.** The lease on a file stays until the item is
   merge-ready (gates other than merge done, tree committed).
3. **Release at merge-ready.** All file leases drop. This is early release,
   allowed by D-lock-lifecycle-answers item 2 for the lock lifecycle only;
   the rest of D-lease-hold-limit-r2 (time-limited locks, break after
   `hold_max_s`) stands.
4. **Re-acquire per file on re-edit.** If a review asks for changes, each file
   is re-leased on its first new write, after a rebase or sync onto main.
5. **Probe at each boundary.** A `git merge-tree --write-tree` conflict probe
   runs at first write (against main), at release, and at merge. Cost:
   median 16 ms on 27 branches, p90 1.3 s only for branches 11k+ commits
   stale (R-lock-landscape-verified); 6-9 ms on 5000 files in the throwaway
   experiment (R-handoff-protocol). ci on the merge result is mandatory at
   merge.

Reviews survive a rebase when the item patch-id is unchanged and main changed
none of the files the reviewers saw; otherwise only overlapping files are
re-reviewed (D-lock-lifecycle-answers item 3).

### Why NOT release at review start

84 percent of tasks commit again after the first review starts, a median of
91 minutes later (the 84 percent is in R-lock-landscape-verified; the 91
minutes is from the task brief LK-design-doc and is not in the four notes). Releasing at review start would hand the files
to another agent just before the owner edits them again, trading a
wait for a forced rebase. Release at merge-ready, when no further edit is
expected, is the chosen point.

## 3. The suggestion protocol

Besides the existing rules of D-context-sync, an agent may send a SUGGESTION
to the agent holding the lease on a file it needs (D-handoff-peers item 1). A
suggestion is data (a patch pointer), never an instruction or an approval.
Transport (R-handoff-protocol): a hidden ref `refs/ddflow/suggest/<item>/<id>`
in the shared object store, applicability checked with merge-tree, delivered
as a pointer message in the existing per-agent inbox; applied with
`cherry-pick -x` so authorship is kept; applied state detected by patch-id.

States and who moves them:

| State | Entered by | Moved by |
|---|---|---|
| sent | suggester | suggester, on creation |
| delivered | inbox | ddflow, at the owner's next wait, watch, heartbeat |
| accepted (applied) | owner | owner applies it, or policy under auto-apply |
| rejected (with reason) | owner | owner; a counter-suggestion is allowed |
| expired into task | ddflow | ddflow, when the owner is gone with it open |
| withdrawn | suggester | suggester |

Once a suggestion is resolved (accepted or rejected) the suggester builds on
the outcome and may send more.

### Limits (all knobs in `[handoff]`)

- Up to **3 files** and **60 lines** per suggestion. Bigger changes are
  ordinary tasks.
- Up to **5 open or sent** suggestions per owner and file.
- A loop guard: one open suggestion per sender-owner pair per file set.
- At the limit the suggester waits for release, or opens a new task for the
  remainder and continues with the rest of its work when it can.

### The owner must answer

Every suggestion must be answered (accept, reject with a reason, or counter).
Consequences (D-handoff-peers context):

- `merge` and `complete` of the owner item refuse while any suggestion to it
  is unanswered. The refusal names the exact next call in CLI and MCP form.
- If the owner lease expires or is released with suggestions open, they are
  re-addressed to the next claimant, or expire into ordinary tasks that need
  the owner item.

### Auto-apply (off by default)

D-handoff-autoapply narrows the no-auto-apply clause. ddflow applies a
suggestion for the owner, recorded as accepted by policy, only when
`[handoff].auto_apply` is on (default off, org-lockable) and ALL hold:

1. 1 file and 10 lines or fewer (knobs);
2. kind is fix or doc;
3. merge-tree is clean on the owner's current tip;
4. ci and the affected tests are green on the merged result BEFORE the commit;
5. the commit keeps the contributor's authorship, with a Suggested-by trailer
   and the suggestion id.

Afterwards the owner sees it in next_actions with the exact revert call. The
applied change is a new delta, so its reviews re-run for those files. A red
result sends the suggestion to the owner as an ordinary one. Accept rate,
ci-red-after-apply and owner revert rate are measured; a revert rate above a
threshold knob switches auto-apply off with a visible note. Metrics carry
counts and ids only, never patch or prompt text.

## 4. Failure modes

| Failure | Response |
|---|---|
| Hook or probe could not run | Recorded `unavailable`, fails open to the commit-time check, never `passed` |
| Cached lease snapshot stale | Hook is cheap by design (a full-log fold costs 3.7 s per call, R-lock-inventory); a stale snapshot is caught by the commit-time check |
| Clean merge-tree but broken (rename vs new call gave a NameError, R-handoff-protocol) | ci on the merge result at merge |
| Red-main bug (8.4 percent of merges, R-lock-landscape-verified) | Mandatory ci on the merge result; revert-and-alert per existing merge rules |
| Re-edit after release collides | Per-file re-acquire is refused with holder and options; rebase first |
| Circular wait through suggestions or leases | Healed by added needs edges (r-lock-cycle-selfheal); no polling, no hold-while-waiting |
| Owner absent or crashed | Suggestions re-addressed or expired into tasks; lease break after `hold_max_s` keeps working |
| Suggestion flood | Per-owner-file limit of 5, one per pair and file set |
| Suggestion carries instructions | It is data; never an approval or order |
| Auto-apply keeps being reverted | Switched off automatically past the revert threshold |
| Refs not fetched by a plain clone (R-handoff-protocol) | Push and fetch refspec for `refs/ddflow`, or the bundle form |

Revert condition (falsifier, R-lock-landscape-verified): if observed-file
leases released at merge give red-main bugs or textual merge-tree conflicts
above about 5 percent of merges, or rebase churn exceeds the waiting saved,
the design is reverted. R-handoff-protocol adds: if more than a few percent of
probe-clean suggestions give a red ci on the owner's result, or most owners do
not answer in time, build no further than notify-only.

## 5. Knobs

All are knobs settable four ways (default, project config, environment,
command line), logged, and org-lockable (r-everything-configurable).

`[locks]` (proposed names):
- `mode`: `enforce` (default), `log`, `off` (D-lock-lifecycle-answers item 1).
- `release_at`: `merge_ready` (default) or `merge`.
- `probe_boundaries`: which boundaries run the merge-tree probe.
- `snapshot_ttl_s`: lifetime of the cached lease snapshot used by the hook.
- Existing and unchanged: `[lease].hold_max_s`.

`[handoff]` (proposed names):
- `enabled`.
- `max_files` (3), `max_lines` (60), `max_open_per_owner_file` (5).
- `auto_apply` (off), `auto_max_files` (1), `auto_max_lines` (10),
  `auto_kinds` (fix, doc), `auto_revert_threshold`.

Each refusal and notice names the exact next call in CLI and MCP form
(r-tell-dont-discover). Git is reached only through `infra.git` (r-adapters).

## 6. What each harness can enforce

The project-level hook matcher today covers Bash only (R-lock-inventory);
installers must be extended to the write tools of each harness
(D-lock-lifecycle-answers context).

| Harness capability | Enforcement |
|---|---|
| Blocking pre-tool hook on write tools (for example Claude Code Edit and Write, opencode) | Write-time refusal naming holder and options |
| No blocking pre-tool hook | Commit-time fallback: pre-commit check refuses or warns; the harness is told this is the only door (P-parity) |
| Hook present but the check could not run | `unavailable`, fall through to the commit-time check |

The commit-time check is always on as the backstop. Bash writes (sed, redirects)
bypass write-tool matchers and are caught at commit time.

## 7. Implementation constraints

Extend the existing lease, wait, drift and inbox code (r-reuse-core): no
second lock system and no daemon (D-sched-no-daemon). New files stay small
(r-small-files). No new tooling that needs a shell; every acceptance works
over MCP (r-no-shell). Definitions (suggestions, limits) are managed records
(r-everything-managed).

## 8. Hype list (claims not adopted, or not yet verified)

- "Release at review start is safe": refuted locally, 84 percent re-commit.
- "Conflicts are the main cost": locally textual conflicts are rare (1 of 68
  pairs); the cost is waiting (32 percent false serialization) and red-main
  (8.4 percent).
- "Clean merge-tree means safe": 33 percent of clean merges later fail
  (Brun), and a local NameError case exists.
- "Merge queues remove the need for any lock": they are the model for the
  gate stage, but they do not govern concurrent editing.
- Unverified in R-lock-landscape-verified: Uber SubmitQueue 2019 paper
  itself, Accioly and Ghiotto per-merge rates beyond 10-20 percent, conflict
  probability versus branch lifetime, Perforce +l, Crystal and Palantir
  effect sizes, Meta PTS test share, Google TAP skip fraction,
  pytest-testmon miss rate, OpenAI Codex, Devin and OpenHands conflict
  handling; hunk-level overlap was not computed.
- Unverified in R-handoff-protocol: owner response rates, token cost per
  suggestion, per-harness door behavior beyond the secondary notes.
- Mergiraf-style structured merge: solves only 428 of 7415 (5.8 percent) of
  Linux kernel conflicts fully; not a basis for dropping the probe.
- AgenticFlict reports 27.67 percent of 107k agent PRs conflicting; a general
  agent figure, not this repo's (1 of 68).
