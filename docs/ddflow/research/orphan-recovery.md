# Orphan, cancelled and crashed work: detect, triage, resume (research, 2026-10-18)

Read-only analysis of the ddflow checkout at worktree `bridge-cse_016cBDq3...` (HEAD d9f15b02). Facts come from reading code and running read-only CLI (`ddflow recover`, `doctor`, `brief`, `health --help`, `cadence`, `show`). Anything not checked is marked UNVERIFIED.

## 1. What exists today (with references)

### 1.1 Detection

| Capability | Where | What it does |
|---|---|---|
| `ddflow recover [--item X] [--apply]` | `ddflow/api/reporting/health.py::recover`, `ddflow/services/leases.py::scan/_measure/sweep` | Finds three kinds: `expired_lease` (holder stopped renewing), `orphan_worktree` (tree but no lease; REVIEW-state items excluded on purpose), `stale_running` (RUNNING, no lease). Measures the tree: dirty files, unmerged commits vs base, three-valued `salvageable` (True / False / None = could not measure). `--apply` only records `lease.expired` for expired leases measured clean (`salvageable is False`); it never touches a tree with work, never prunes a worktree, never resumes. Foreign-clone leases are never swept. |
| Brief "Recoverable work found" | `ddflow/api/lifecycle/brief.py` line ~270 (`L.scan`), rendered by `ddflow/views/markdown.py::recovery_band` | Every session's brief names recoverable items with the INSPECT FIRST advice. Today it is cut by `session.brief_max_tokens` (the live brief showed "6 more cut"; only a pointer to `ddflow recover` survives). |
| Health watchdog | `ddflow/core/health.py` (pure judge), `ddflow/services/healthwatch.py` (gather and correct), `ddflow/api/healthwatch.py`, `ddflow/config_sections/health.py`; task DX-health-watch (done 2026-10-17) | Findings IDLE, STALLED, UNRESPONSIVE, LOCKED, CRASHED, RUNAWAY, each with evidence. CRASHED = lease ran out over a tree with work, or live lease whose declared harness process is gone (`ddflow/infra/harness_identity.py::liveness`, `crash_s`=300) . Per-kind modes `off|propose|apply` (default `propose`). Apply moves: nudge idle, stop-request stalled, park-then-break unresponsive (`services/leasebreak.py`), auto-order for deadlocks, and for CRASHED only `L.expire` ("record the lane expired so `ddflow recover` can pick it up"). Never kills, never deletes. |
| Periodic trigger | `healthwatch.due` registered in `ddflow/services/cadence.py:210`; `ddflow health --run`; `ddflow cadence` currently prints `DUE: health_watch -- no pass recorded` | The pass is only "due". Nothing runs it unless an agent, loop, hook or cron invokes `ddflow health --run`. `[health].interval_s`=900. |
| Dead gate runs | `ddflow/services/gates/running.py` (marker `.ddflow/runs/<item>/<gate>.running.<pid>.json`), `ddflow/api/gates.py::sweep_dead_runs` called from `_resolve` and `status` | A killed `gate run` leaves a marker; the NEXT gate call records `unavailable` with reason and a rerun command. SIGTERM/SIGHUP are caught and recorded before exit. Gap: only runs when some gate call happens; SIGKILL of the whole group is only found later; reviews (`ddflow review`), `ci run`, `pins regenerate` markers: UNVERIFIED whether they use the same marker. |
| Background jobs | `ddflow/services/jobs.py` (`status`: running / exited / gone / elsewhere; `logged_exit` reads the `ddflow-job-exit:` trailer; launch via setsid, pidfile, proc start time) | Can tell a dead job from a live one and read its real exit code from the log. Nothing auto-ends them: the brief says "112 more unended jobs". |
| Merge queue | `ddflow/services/mergequeue.py` (entries under `.ddflow/local/mergequeue/`, integrator flock), `ddflow/api/mergequeue_run.py:225` | `requeue_orphans` puts entries left RUNNING by a dead integrator back in line, but only when the next integrator starts. |
| Cleanup | `ddflow/services/cleanup.py` (`survey`, `apply`, `sweep_finished`, `stranded_leases`), `ddflow/infra/worktree.py` | Removes merged/finished clean trees; protects adopted (harness-owned) trees; `stranded_text` names lost lease contests. |
| Lease semantics | `ddflow/config_sections/lease.py`: `ttl_s`, `heartbeat_s`, `heartbeat_during_command` (long gate/review/ci/pins command renews on its own thread), `reclaim_policy` report (default) | `auto`; `resume_own` (DX-lease-lapse-reclaim, done): the lapsed holder re-claims with a plain claim and full globs if nobody overlaps; `idle_max_s`/`break_on_timeout` break contended idle leases (D-lease-hold-limit, `leasebreak.py`). |
| Doctor | `ddflow doctor` | Lists `orphan_worktree` ("clean and fully merged, safe to remove: git worktree remove ...") and, in the recover section, `expired_lease` INSPECT FIRST. Live run: 55 situations, 5 salvageable expired leases (B-hx-migrate 6 commits, B-rm-data 3, B-tpl-refresh 2, DX-task-fold 15, fix-B26b5951f2b 5), the rest orphan trees, mostly adopted harness trees ("leave it to the harness"). |

### 1.2 What survives which event

- Event log (`.ddflow/events/*.jsonl`, committed, plus uncommitted shards that doctor warned about today: only git-committed shards survive disk loss, not reboots) , worktrees and branches on disk (`.ddflow/worktrees/<item>`), lease state (folded from the log): all survive reboot and MCP restart.
- Machine-local, git-ignored: `.ddflow/runs/` markers, `.ddflow/local/mergequeue/`, waits, harness identity records (`harness_identity`, pid based), job pid and logs. These survive a reboot as files but every pid in them is dead afterward; `jobs.status` copes (`gone`).
- Lost on MCP server crash/restart: per-connection identity declared via `ddflow_identify` (UNVERIFIED how it is re-derived; stateless revision requires `as_agent` per call), any in-process heartbeat thread of a running gate/review (the `heartbeat_during_command` thread dies with its process, the lease then lapses after `ttl_s`), the in-process live-run registry that DX-gate-run-same-commit-live plans (not built; today's marker file is the substitute). Lease heartbeats themselves are agent/hook driven, not MCP-resident, so an MCP restart alone loses no lease. UNVERIFIED: whether the MCP server holds any other in-memory caches (the index auto-rebuilds, per doctor).
- Not built yet: launcher (`ddflow/services/launcher.py`, `continuation.py`, `services/dispatch/`, `parking.py` do not exist; P-agent-parity.launcher, DX-park-and-resume, DX-dispatch-supervise are open), daemons (D-daemons accepted; no `daemon.py`), scheduler executor (P-schedule open). Decisions: D-dispatch-modes (per-project: propose only by default, kill switch, `schedule.max_parallel_tasks` cap), D-no-daemon-r2 and D-agent-parity (ddflow may start an agent only through the lane launcher with an operator-authored template; headless launch with spend and turn caps), D-daemons (child process, backoff cap 3; lane supervisor and serve operator-only).

### 1.3 Known gaps already filed (open)

- DX-health-watch-liveness: pid/host/session on heartbeats, repeated-gate RUNAWAY, UNRESPONSIVE/STARVED completion, dispatcher consumer. (Note: `Lane.gone/pids` and `crash_s` already exist via harness_identity, so this task is partly done; re-scope.)
- DX-health-orphan-wait: "waiting on a gate/review whose process is gone". Needs DX-gate-run-same-commit-live (live-run registry).
- DX-park-and-resume, DX-dispatch-supervise (relaunch via launcher), DX-lapsed-lease-merge-deadlock, DX-lease-resume-own is not an id (the work shipped as DX-lease-lapse-reclaim).

## 2. Failure matrix

Legend: S = SAFE_AUTO (idempotent, loses nothing), P = PROPOSE (needs a new agent or a decision recorded as a proposal), O = OPERATOR.

| # | Situation | Detection signal | Safe automatic action | Waits for operator | Evidence recorded |
|---|---|---|---|---|---|
| a | Agent cancelled/killed, lease held, unmerged commits | lease past TTL+grace (or `crash_s` with `gone` harness); `recover` shows `expired_lease` with `unmerged_commits>0`, clean tree | S: record `lease.expired` (already `mode_crashed=apply`); NEW: write a `recovery.found` record per item with commit count, last gate state, branch head sha, and which gates are still owed. P: `ddflow claim <item>` by the next agent (resume). Item keeps its worktree and branch. | Whether to resume at all, or abandon (`ddflow abandon`). Never auto-delete. | `lease.expired` (actor ddflow:health), recovery record with measured counts, resume attempt counter. |
| b | Agent finished (all gates done) but never ran merge/complete | item RUNNING/REVIEW, gates all satisfied or only `merge` owed, lease lapsed, commits ahead | S: none that lands code. P (strongest case): `ddflow queue add <item>` + `queue run` is mechanical, but D-merge policy is the operator's; propose as "ready to land". NEW detector: `ready_to_land`. | Landing on main. If `[flow].integration=pr`, a person approves anyway. | `recovery.found` kind `ready_to_land`, gate status snapshot. |
| c | Gate/review/ci run orphaned | marker file in `.ddflow/runs/` with dead pid (`dead_runs`); job with `status in (exited, gone)` not ended; job log has `ddflow-job-exit:` | S: record `unavailable` (exists, but only lazily); NEW: if the log holds a finished review/test result and exit 0, parse and record it via the normal record path (or just `job end` with the exit code) ; S: re-run the gate on the SAME commit when sha unchanged and attempts < cap (idempotent: a rerun yields a fresh outcome). | A review whose rerun costs money beyond a configured cap; any gate flapping (doctor already counts `gate_flapping`). | gate outcome `unavailable` with `run_killed`, pid, started_at; `job.ended` with exit code; counter. |
| d | MCP server crash/restart | next tool call new process; heartbeats missing only if agent relied on server thread | S: nothing lost in the log. Identity: agent re-identifies (`as_agent` or `ddflow_identify`). In-flight `gate run` inside the server dies: becomes case (c). | Nothing unless the restart killed a merge (h). | None needed beyond (c). UNVERIFIED: whether the server writes a boot record; propose `server.started` event so the next pass knows runs older than boot are dead. |
| e | Whole-system reboot | every pid dead; all leases lapse after TTL; `harness_identity.liveness` returns gone for every lane; jobs `gone`; markers dead | S: run the health pass once at first ddflow contact after boot: expire all lapsed clean leases, sweep dead markers, mark jobs ended, `requeue_orphans`. P: one summary "N items interrupted by reboot" in brief listing resumes. O: trees with uncommitted files. Detection of "a reboot happened": compare `/proc/sys/kernel/random/boot_id` stored in `.ddflow/local/boot` (UNVERIFIED not implemented). | Which items to resume and the order (queue priority decides by default). | `recovery.pass` event with boot id change, counts. |
| f | Lease lapsed but agent still alive (reviews of 15-40 min; 2026-10-18) | harness liveness `alive` AND lease lapsed; `heartbeat_during_command` off or thread died | S: NEVER treat as crashed. Action is the opposite: the holder resumes with `resume_own` (exists). Improve: a `ddflow wait`/review long-run should heartbeat even when the foreground command is a child reviewer (verify the thread covers `ddflow review`). Health should classify "lapsed, harness alive" as `lapsed_alive`, notify the holder via nudge, and not offer the lease to others unless a waiter exists. | Breaking a lease for a waiter is already explicit (`claim --break`). | `health` finding with `alive` pids; no expire record while alive. |
| g | Worktree has uncommitted files after crash | `dirty_files>0` or `None` (could not measure) | S: none to the tree. P: a safe snapshot commit onto a `ddflow/rescue/<item>-<ts>` ref in the worktree (`git stash create` equivalent, no checkout change) so the files are in git objects even if the tree is later removed. That is a write to git but not to the user's branch; needs a decision. | Everything else: commit or discard. Rule today and still right: dirty trees are never swept. | rescue ref sha in the event. |
| h | Merge interrupted midway | `MERGE_HEAD` in the item worktree or main (the repo already probes it for the commit-msg hook, `api/setup.py:549`); queue entry `running` with dead integrator; merge commit present on base but no `merge`/`complete` events (`_landed_anyway` handles git-said-failure-but-landed in `api/lifecycle/merge.py:298`); pins half regenerated | S: `requeue_orphans` (exists, on next integrator run); S: if the base contains the branch head, record `merge` and `complete` (idempotent, verify via `git merge-base --is-ancestor`). O: an actual conflicted merge left in a tree (`MERGE_HEAD` + unmerged paths): never `merge --abort` without the owner; pins half-written: `ddflow pins regenerate --check` then regenerate (post-merge note already exists). | Conflict resolution; whether to abort. | `recovery.found` kind `merge_interrupted` with git state. |

## 3. Design of the loop

### 3.1 One engine, many triggers

A single pure function `triage(facts) -> [Action]` in `ddflow/core/recovery_plan.py` (no I/O, fake clock, same style as `core/health.py`) consumes what `leases.scan`, `healthwatch.gather`, `gates/running.dead_runs`, `jobs.status`, merge queue state and git probes already measure. It returns one `Action` per item with class SAFE_AUTO, PROPOSE or OPERATOR, the exact command, the evidence, and the item's recovery attempt count. `services/recovery.py` applies SAFE_AUTO, records, and renders the rest. Do not build a second detector: extend `core.health` (new kinds `ready_to_land`, `lapsed_alive`, `merge_interrupted`) and let `recover` consume the same findings, so `doctor`, `brief`, `recover`, `health` can never disagree (the repo already learned this lesson: `schedule.interrupted` shared with `scan`).

Triggers (all call the same pass; each is throttled by `[health].interval_s` using the last `cadence.ran`):
1. `ddflow brief` and SessionStart hook (cheapest, always happens at every restart): SAFE_AUTO only, bounded in time (budget 2 s; skip git measure for already-recorded items).
2. `ddflow next` and `claim` (an agent about to pick work should not pick work that was interrupted): same pass.
3. `ddflow health --run` through `ddflow cadence` (agents and loops already honour DUE lines); documented cron line / systemd timer printed by the existing emitters of P-schedule for headless projects.
4. Post-merge hook (cheap: after a merge, expire/prune what that merge made stale, requeue orphans).
5. Later: D-daemons "scheduler tick" daemon (harmless kind, project-listable) calling the same pass; no new logic.

### 3.2 Triage

- SAFE_AUTO (idempotent, loses nothing, provable from facts): record `lease.expired` for lapsed leases when the tree is measured clean OR the harness is provably gone and commits exist (work is in git; expiry only frees the claim); record `unavailable` for dead gate markers; `job end` with the logged exit code for jobs whose log has the exit trailer; `requeue_orphans`; remove a tree that is measured clean AND fully merged AND not adopted (`git worktree prune` after); re-run an orphaned gate on an unchanged sha under the attempt cap; record `merge`/`complete` when the branch head is an ancestor of base. Each SAFE_AUTO action re-checks its precondition under the lock immediately before acting (time-of-check gap).
- PROPOSE: resume = claim the expired item for a new agent. Written as a `recovery.proposed` record and a top-of-brief line with the exact command. With the launcher: a dispatch lane applies it if the operator enabled launch mode.
- OPERATOR: dirty or unmeasurable trees, a conflicted merge, an item whose attempt counter hit the cap, anything cross-clone, anything on an adopted tree.

### 3.3 Resume without a launcher

The brief is the dispatcher. Change the brief so recoverable items are never cut by the token budget: they take a reserved slice (a few lines: item, "N commits, gates owed: x,y", and `ddflow resume <item>` command). Add `ddflow resume <item>` (= recover show + claim with the old worktree and branch + a "resume brief": last gate states, commits since base, the session notes, the failing gate output tail). Any agent session that runs `ddflow brief` then picks the oldest interrupted item before new work. `ddflow next` should rank interrupted items first (it has `schedule.interrupted` already) and print the resume command, not only the claim. Cost: small; this is the highest-value, lowest-risk step because it needs no new actor and today's six cancelled agents are exactly this case.

### 3.4 Resume with the launcher (later)

Needs P-agent-parity.launcher (templates the operator wrote; credentials from env), DX-park-and-resume (the parking record and resume brief) and DX-dispatch-supervise (refill). Per D-dispatch-modes default is propose-only; in `launch` mode a dispatch lane takes PROPOSE actions: spawn the template with the resume brief as the prompt, bounded by `schedule.max_parallel_tasks`, a per-item attempt cap, a project-wide spend and turn cap, and the kill switch (a file `.ddflow/local/STOP` or `[dispatch].enabled=false` checked before every launch and every tick). The launched agent must `ddflow_identify` with a stable name derived from the item so lineage is traceable.

### 3.5 Guards against loops

- `recovery_attempts` per item, a fold of `recovery.resumed` events; cap `[recover].max_attempts` (default 2). On the cap the item becomes OPERATOR and is `block`ed with the reason (not abandoned).
- A resume only counts as success if progress happens (a commit or a gate outcome within `[recover].grace_s`); else the attempt is spent.
- Circuit breaker: if more than N resumes fail within a window (default 3 in 60 min) the pass switches itself to propose-only and writes one operator notice. Reset only by the operator.
- No resurrection of items the operator abandoned or blocked; no resume of an item with a failed human gate.
- Budget: the launcher cap (cost per item and per day) refuses with exit 3, not silently.
- Quiet by default: one notice per item per state change (dedupe by `(item, kind, sha)` as the health findings already do via `Finding.key`).

### 3.6 `ddflow recover --all --plan`

Dry run: prints, per item, class (SAFE_AUTO/PROPOSE/OPERATOR), the evidence, and the exact commands, plus "would record: ...". `--apply` executes only SAFE_AUTO (a superset of today's `--apply` which executes only the clean-expiry case). `--plan --json` for dispatch. Exit codes follow the project: 0 nothing needed or applied, 2 nothing to do, 3 refused (kill switch, breaker open), 1 failures while applying.

### 3.7 Idempotency across a crash of recovery itself

- Every action is an event append with a deterministic key `(item, action, subject_sha_or_marker_id)`; folding twice is a no-op (`L.expire` already behaves this way: "folding two expiries is one").
- Order: precondition check, then the effect (the external effect last when it is irreversible, e.g. worktree removal), then the event, with the event naming the effect so a crash between effect and event is detected on the next pass by observing the effect already done (tree gone, ancestor true) and recording the event only. Same pattern `merge._landed_anyway` uses.
- One pass at a time through a flock in `.ddflow/local/recovery.lock` (the merge queue integrator lock is the model); a dead holder's lock is released by the OS.
- Resume itself is a claim, already arbitrated by the lease flock; two agents resuming the same item: one wins, the other gets the existing refusal.

## 4. Honest limits

- A cancelled agent's in-context knowledge is gone. The resume agent has only the commits, notes, gate records and event log. The quality of the resume brief decides whether the continuation is good; unverifiable without trying.
- Harness liveness only exists for agents that called `ddflow_identify` (`harness_identity.liveness` returns `None` otherwise); others fall back to the lease TTL (30 min). Cancelled subagents of a coordinator are often NOT identified, so the fast signal is often absent. UNVERIFIED how many of today's six had identity records.
- "Alive but lapsed" and "dead" cannot be told apart without liveness or a started-marker; a wrong guess either resurrects a live agent (two agents on one tree) or leaves work idle. The lease lock still prevents double claiming, but a live agent that lost its lease gets a refused commit hook and loses time.
- Uncommitted work: only a rescue snapshot can protect it, and that is a git write that needs a decision.
- A resume agent can redo or contradict half-done work; reviews need a fresh round on the new commits. Nothing here guarantees convergence, hence the attempt cap.
- Cross-clone leases (`foreign`) are invisible from here and stay with their owner.
- Cannot recover state that was never recorded: the review log of an uncommitted event shard (doctor today reports 3 uncommitted shards and "lost events" warnings for three shards) is a separate integrity issue that makes any recovery from the log fragile. File it separately.
- Reboot detection and an MCP-boot record are UNVERIFIED as absent; I did not grep the server start path.

## 5. Ranked options (cost / risk)

| Rank | Option | Cost | Risk | Needs |
|---|---|---|---|---|
| 1 | Brief reserves room for recoverable items; adds exact resume command; `ddflow next` ranks interrupted first; `ddflow resume <item>` with a resume brief | S-M | very low (read-only plus claim, existing lease rules) | nothing |
| 2 | Run the pass automatically: SessionStart/brief/next/post-merge triggers call a throttled safe pass (`recover --apply` extended: dead markers, job end from logged exit, requeue orphans, merge-recorded-after-ancestor, clean merged tree prune) | M | low (only SAFE_AUTO, preconditions rechecked) | nothing |
| 3 | `recover --all --plan` and the pure `core/recovery_plan.py` triage; unify with health findings | M | low | #2 |
| 4 | New detectors: `ready_to_land`, `lapsed_alive`, `merge_interrupted`, reboot via boot id, MCP boot record | M | low | none |
| 5 | Rescue snapshot ref for dirty trees on crash | S | medium (new git write; ref namespace must not leak into merges) | operator decision |
| 6 | Counters, circuit breaker, `[recover]` knobs | S | low | #3 |
| 7 | Launcher-based auto resume (dispatch lane applies PROPOSE) | L | high (spend, runaway, double work) | P-agent-parity.launcher, DX-park-and-resume, DX-dispatch-supervise, operator consent |
| 8 | Scheduler tick daemon that runs the pass without any agent | M-L | medium (a process to babysit) | D-daemons work (DM-*) , P-schedule executor |

Feasible now: 1, 2, 3, 4, 6 (detection, safe actions, better brief). Needs the launcher: 7. Needs the operator: 5, the launch-mode switch, caps and the policy for ready_to_land.

## 6. Proposed tasks (not filed; real layout)

1. `DX-recover-brief-slice` Brief keeps recoverable work visible: reserved slice, exact resume command, never cut by `brief_max_tokens`. needs: none. globs: `ddflow/api/lifecycle/brief.py`, `ddflow/views/markdown.py`, `ddflow/config_sections/session.py`, `tests/test_brief_recovery_slice.py`.
2. `DX-resume-command` `ddflow resume <item>` plus resume brief (commits since base, owed gates, last failing output, session notes); `next` ranks interrupted items first. needs: DX-recover-brief-slice. globs: `ddflow/services/resume.py`, `ddflow/api/lifecycle/resume.py`, `ddflow/surfaces/declared/lifecycle.py`, `ddflow/surfaces/parsers/lifecycle.py`, `ddflow/api/lifecycle/__init__.py`, `tests/test_resume.py`.
3. `DX-recovery-plan` Pure triage `core/recovery_plan.py` (SAFE_AUTO/PROPOSE/OPERATOR) over scan, health, dead runs, jobs, queue; `recover --all --plan [--json]`. needs: none. globs: `ddflow/core/recovery_plan.py`, `ddflow/services/recovery.py`, `ddflow/api/reporting/health.py`, `ddflow/surfaces/declared/reporting.py`, `ddflow/surfaces/parsers/reporting.py`, `tests/test_recovery_plan.py`.
4. `DX-recovery-safe-pass` Apply SAFE_AUTO from the triggers (brief, next, SessionStart, post-merge, `health --run`): dead gate markers, job end from the logged exit, requeue orphans, record merge when ancestor, prune clean merged non-adopted trees; flock, rechecks. needs: DX-recovery-plan. globs: `ddflow/services/recovery.py`, `ddflow/services/gates/running.py`, `ddflow/services/jobs.py`, `ddflow/services/mergequeue.py`, `ddflow/services/healthwatch.py`, `ddflow/api/gates.py`, `tests/test_recovery_safe_pass.py`.
5. `DX-recovery-detectors` Add `ready_to_land`, `lapsed_alive`, `merge_interrupted`, reboot boot-id, MCP boot record. needs: DX-recovery-plan. globs: `ddflow/core/health.py`, `ddflow/services/healthwatch.py`, `ddflow/surfaces/mcp.py`, `ddflow/infra/worktree.py`, `tests/test_recovery_detectors.py`.
6. `DX-recovery-guards` `recovery_attempts` fold, `[recover]` knobs (max_attempts, grace_s, breaker window), circuit breaker, kill switch. needs: DX-recovery-plan. globs: `ddflow/config_sections/recover.py`, `ddflow/core/model.py`, `ddflow/core/events.py`, `ddflow/services/recovery.py`, `ddflow/templates/upgrade/unreleased/knob_added.recover.*.toml`, `tests/test_recovery_guards.py`.
7. `DX-recovery-rescue-ref` Optional rescue snapshot of a dirty crashed tree onto a private ref. needs: operator decision, DX-recovery-detectors. globs: `ddflow/infra/worktree.py`, `ddflow/services/recovery.py`, `tests/test_recovery_rescue.py`.
8. `DX-health-watch-liveness` re-scope (pid/identity part is partly shipped): remove done parts, keep RUNAWAY on repeated gate failures and UNRESPONSIVE/STARVED. Existing item; only a note.
9. `DX-recovery-launch-lane` Dispatch lane applies PROPOSE resumes with the launcher, caps and kill switch. needs: P-agent-parity.launcher, DX-park-and-resume, DX-dispatch-supervise, DX-recovery-guards. globs: `ddflow/services/dispatch/recover.py`, `tests/test_dispatch_recover.py`.
10. `B-events-uncommitted-shards` Separate defect: doctor shows 3 uncommitted event shards and "lost events" for three shards. File as a bug if not already. globs: `ddflow/infra/log.py`, `ddflow/services/eventcommit.py`.

## 7. Operator decisions needed

1. May the pass auto-run at session start and `next` (SAFE_AUTO only), or only via `health --run`/cron?
2. `[health].mode_crashed` is already `propose` by default and `apply` writes only the expiry: raise to `apply` for this project?
3. Policy for `ready_to_land` (finished, never merged): propose only, or auto `queue add`?
4. Rescue snapshot refs for dirty crashed trees: allowed?
5. Launch mode: enable the launcher resume lane at all, with what caps (attempts per item, daily spend, parallel), and kill switch location?
6. Reaction to the five expired leases found today (B-hx-migrate 6 commits, B-rm-data 3, B-tpl-refresh 2, DX-task-fold 15, fix-B26b5951f2b 5): resume by new agents in the order of the queue, or abandon some. Plan: `ddflow resume` once it exists; today `ddflow claim <item>` after inspecting `git diff main`.
7. Raise `[lease].ttl_s` or confirm heartbeats cover 15-40 minute reviews (`heartbeat_during_command` is on per config doc; verify it actually covers `ddflow review` on this install).

## 8. UNVERIFIED

- Whether `ddflow review`, `ci run`, `pins regenerate` write the same started-markers as `gate run` (only `gates/running.py` and `api/gates.py::sweep_dead_runs` read).
- How the MCP server re-derives identity after restart and whether it writes a boot record.
- Whether the six cancelled agents had identity records (only `harness_identity` files tell; not inspected).
- That `lease.reclaim_policy=auto` does what its doc says in practice (not exercised; the default `report` is in force).
- The SessionStart hook body (only its declaration at `ddflow/surfaces/declared/hooks.py` was read) and whether it can afford a pass within its latency.
- Runtime behaviour of any proposed action: nothing was executed, only read.
