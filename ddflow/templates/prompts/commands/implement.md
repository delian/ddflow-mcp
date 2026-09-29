Drive {% if scope %}{{ scope }}{% else %}this project's ddflow queue{% endif %} to completion, unattended: one item per iteration, every gate, merged and completed, until nothing actionable is left.

The per-item steps are the canonical driver, `docs/ddflow/drivers/implement-phase.md`, plus this harness's delta in `docs/ddflow/drivers/deltas/` (`ddflow adopt` writes both; `ddflow help workflow` is the short form). This command adds only what running WITHOUT an operator needs: when to stop, how to keep going, and when to ask.

## Scope

{% if scope %}The operator named `{{ scope }}`. Its first word may be an item id; everything after it is guidance for the first iteration. If that word is a **phase** id, drive every task in it (`ddflow_next` with `phase`), then the phase close. If it is a **task** id, drive that one item. When `ddflow_next` does not offer it, `ddflow_show` says why: already completed → case 1; waiting on another agent's lease or on a dependency someone is working → `ddflow_wait` with that `item`, which returns the moment the holder lets go — a wake-up, not a stop; waiting on the operator → case 3. Never work around its dependencies. If the first word is not an id, the whole scope is guidance. Do not wander outside the named scope.{% else %}No scope was named. **Do not ask which** — drive whatever `ddflow_next` offers, highest priority first. Ask only if the ready set is empty AND the blocked list shows a decision only the operator can make.{% endif %}

## Continuation contract — the prime directive

The loop RUNS TO COMPLETION. An iteration may end without scheduling the next in exactly FOUR cases:

1. **Done** — `ddflow_next` exits 2 for the scope, nothing is in flight under your identity, and the termination checklist below holds.
2. **The operator says stop** — "pause", "stop", "hold": unconditional and immediate.
3. **Only the operator can unblock it** — an escalation case below AND no remaining ready item is independent of the answer. Independent items exist? Ask, then keep working on those.
4. **Unrecoverable environment** — repository corrupt, disk full, a git state you are forbidden to mutate.

Everything else — a pending review, an asynchronous reviewer still running, a flaky test, a denied tool call, a pull request awaiting approval — is a WAIT or a WORKAROUND, never a stop.

- **Waiting is a delayed wake-up, never a terminal stop.** A terminal stop is final; only the operator re-invoking `implement` resumes it.
- **Wake on the release, not on a timer.** When what blocks you is another agent — its lease, files it holds, a dependency it is working — `ddflow_wait` (or `ddflow wait` in a background shell) sleeps on the queue and returns the moment that agent completes, releases or lets its lease lapse. Exit 2 from it means the deadline passed or waiting cannot help, and says which: re-arm, or do what it says.
- **Arm the continuation before every turn ends**, and **disarm it before any deliberate stop** — including the operator's own "pause" — or the stop is undone minutes later. How to arm it is harness-specific; the delta says. Where the harness offers both, a background task that *finishes* is the reliable signal and a timer is the fallback.
- **A denied or blocked tool call is not a blocker.** Try a permitted alternative; else file it (`ddflow_task_add`), note it, continue. Escalate only if it gates ALL remaining work.
- **Drive as far as the turn allows.** Finishing one item and then idling until the next wake-up wastes the turn.

## Each iteration

1. **Re-orient.** FIRST read any operator message that arrived since the last iteration — a "pause" or "stop" there ends the loop before anything else runs. Then `ddflow_recover` — a crashed agent's worktree often holds finished work that exists nowhere else; salvage it before starting anything new. Then `ddflow_brief`.
2. **Pick.** `ddflow_next`{% if scope %}, within `{{ scope }}`{% endif %}. Exit 2 is a result, not an error: read the blocked list. Never take an item `ddflow_next` did not offer, and never the first unchecked box you happen to see.
3. **Claim.** `ddflow_claim`. Exit 3 means coordination said no: take one of the alternatives it lists rather than waiting. No alternatives → `ddflow_wait` on the item. Work ONLY in the worktree it returns. Heartbeat during long work.
4. **Satisfy every gate**, in the order `ddflow_gate_status` gives, following its per-gate instruction. The driver's binding rules and its rules for combining reviewers apply without exception.
   - Out-of-scope findings become new items (`ddflow_task_add`, `ddflow_bug_found`), never silent TODOs and never this item's commit.
5. **Land it.** Commit explicit paths only (never `add -A`, never `--no-verify`, never a quiet commit that hides a failing hook), then `ddflow_merge`, then `ddflow_complete`. Where `ddflow_flow_show` says integration is `pr`, `ddflow_merge` parks the item IN REVIEW and releases your lease: do not wait for approval and do not complete it — take the next item; `ddflow_next` syncs it later.
6. **Capture.** A bug found → `ddflow_bug_found` before fixing it. A surprise → `ddflow_lesson_add`. A settled design choice → `ddflow_decision_add`.
7. **Cadences.** `ddflow_cadence`: when a periodic pass is due (integration tests, duplication sweep, architecture review, mutation tests, lessons pass), run it and record it with `ran`. These are what a per-item gate structurally cannot do.
8. **Next.** When the last task of a phase completes, run the driver's phase close. Then name the next item in one line and arm the continuation.

## Escalation — ask the operator

**Do** ask for: a design or user-visible ambiguity; competing implementations whose trade-off affects the operator; the same step failing three times (`ddflow_loops` shows a retry loop); deleting or renaming a public API; abandoning an item; installing a companion tool; any destructive or outward-facing action (pushing, publishing, tagging).

**Do not** ask: "should I proceed?" (yes); which of two equivalent implementations; typos and confirmed findings; coverage you can add inline; which ready item to take (`ddflow_next` decides).

Ask with the harness's question tool, one decision per question, your recommendation first. While waiting, keep working on anything independent of the answer.

## Operator messages mid-loop

- **pause / stop / hold** — disarm the continuation, stop, one-line acknowledgement.
- **continue** — resume at the next unfinished gate.
- **skip this item** — `ddflow_release` it with the operator's reason as the note; take the next.
- **revert the last merge** — only on an explicit ask: `git revert`, never `reset --hard`.
- **anything else** — guidance: integrate it, record it verbatim (`ddflow_session_prompt`), continue. An unrelated question: answer it, then resume.

## Termination checklist

Complete and safe to stop only when ALL hold:

- `ddflow_next` exits 2 for the scope, and the blocked list names only items that wait on the operator or on a declared dependency outside the scope.
- Every item this run touched is completed, or released with a note saying why.
- Every gate on each completed item carries an outcome — `unavailable` recorded as such, never as passed.
- Every finding either changed source with a mutation-verified probe in the same commit, or is filed — THEORETICAL when no probe exists, or as its own item or bug when it is real but out of scope.
- Every due cadence ran and was recorded.
- No lease of yours is held, no worktree of yours is dirty, and the continuation is disarmed.
- The event log is committed.

Then report: what shipped (ids and commits), what was filed, what is blocked and on whom.

Complete and **blocked** when three consecutive iterations end in escalation, when a destructive action is needed without approval, or when the queue contradicts itself (`ddflow_doctor`). **Stopping in any state not listed here is a defect in the loop, not a judgement call.**

## Anti-patterns

- Ending a turn without arming the continuation.
- Using a terminal stop to WAIT.
- Recording a reviewer as passed when it never ran, or never reported.
- Batching a cleanup refactor into a feature commit.
- Completing an item on "it compiles".
- Merging an item whose gates have not run, "to tidy up".
- Editing in the primary checkout, or switching its branch.
- Asking the operator what `ddflow_next` already answers.
