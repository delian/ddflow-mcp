# Parallelism and coordination

Many agents, lanes, adaptive parallelism and one shared server. Part of the ddflow [README](../../README.md); the reference moved out of it by D-readme-split. Every heading here keeps the anchor it had in the README.

## Parallelism and coordination

```console
$ ddflow next --phase P1
Ready (3 ready, 0 running, 1 blocked):
  P1.T1  persistent store
      writes: shortener/store.py, tests/test_store.py
  P1.T2  base62 encoder
      writes: shortener/encode.py, tests/test_encode.py

These are independent — run them in parallel worktrees.
  (blocked) P1.T3: deps — P1.T1 is open; P1.T2 is open
```

`ddflow claim <ID>` leases the item and binds it to a worktree.

**What a claim holds is what you said it holds.** `claim --globs` is recorded on the item,
so no later heartbeat — live, or reviving a lapsed lease — resets the lease to the globs
the item had before; the claim prints the globs it recorded. Globs are comma-separated or
a JSON array, and the flag may repeat (every value is kept; over MCP a JSON array is read
whole). A glob mangled by quoting is refused with the form every surface reads. `update
--globs` **replaces** the item's globs, and a claimed item's lease with them; the result
names what it dropped, and `show` prints the lease's globs beside the item's. `--resources`
works the same way. `ddflow wait --item X --globs ..` judges the claim you are about to make
rather than the stored globs, so READY means that claim will not be refused. One `wait` blocks
for at most 1800 seconds on the CLI. Over MCP it is at most 1800 seconds when the call can
send progress (a `_meta.progressToken` and `[mcp].progress_s` on: each beat resets the client's
idle timer), and at most 1200 when it cannot — below the 1800 s a client gives a silent tool
call before aborting it (Bfc43dae5b9); a longer `--timeout` is shortened (and says so on
stderr), and you call again to wait longer.

`ddflow wait --notify <how>` registers how to reach the waiter when its wake fires, and the
holder's `release`/`complete` surfaces it in the `woke` list so an orchestrator can resume
the waiter: empty (default) means the waiter polls `ddflow wait`; `send_message:<session-id>`
names the harness session to resume; `watch:<url>` an inbound URL to POST the wake to.

**Files every item touches** — a changelog, a research log, a regenerated config — would
make every pair of items collide. Declare them, and many leases may hold them at once:

```toml
[lease]
append_only_globs = ["docs/CHANGELOG.md"]   # every item adds lines
shared_globs = ["configs/default.toml"]     # generated: regenerate after merging
```

A path inside either is exempt from the overlap check (`claim`, `update`, `next`,
`wait`) and counts as covered at commit time for anyone holding a live lease. For each
append-only glob in the committed config ddflow **writes** `<glob> merge=union` to
`.gitattributes` — when the knob is set through `ddflow config --set` /
`ddflow_configure`, and again on `ddflow init` / `adopt` after a hand edit — and prints
the line; commit it with the config. A generated file never gets `union` (it would be
interleaved): `doctor` notes a shared glob git has no merge driver for (a document items edit by hand in sections, like `README.md`,
is merged right by git's default text merge: declare it with a `README.md merge=text` line in `.gitattributes` and the note stops), and reports an
append-only glob git does not union-merge. Git decides which driver a file gets
(`check-attr`), globs match as `.gitattributes` does, and a union line never overrides a
narrower driver the project set for a file inside the glob: it is written before that
line, so the narrower one keeps winning. Only the committed config writes union lines; a
`.ddflow/local` edit syncs nothing.

**A contended file is served first come, first served.** An agent refused for an overlap
used to have no place in line: it polled, and whoever polled first after the release took
the file, so a hot file (the one every change touches) starved its longest waiter for
hours. Now a live `ddflow wait --item X`, or a `claim` that was refused and is asked AGAIN,
is a place in line (kept in `.ddflow/local/waits/`, this machine's, never committed). While
the oldest waiter could claim its item right now, a younger or unqueued claim of an
overlapping file is refused with exit 3: `TC is reserved for agent-b (waiting since
14:02:11Z for TB, which needs the same files); their place is held for 300s after they could
claim`. `next` lists such an item as blocked instead of offering it, and `wait --item` stays
blocked on it. The waiter's own claim passes, and it spends the place. This is a
best-effort queue, not a lock: the registry is checked when a claim is asked, not inside the
lease transaction, so two claims landing in the very same instant at a release can still race.

The rule cannot hold anyone up for long: a waiter whose process died or whose wait deadline
passed is dead, and blocks nobody; a waiter that was woken and does not come back loses its
place `[lease].waiter_reservation_s` seconds later (default 300; `0` turns the queue off); a
waiter still behind another holder, or waiting for files disjoint from yours, reserves
nothing against you; and the order is strict (older first), so two waiters cannot reserve
against each other. A refused claim keeps its place as long as it is asked again within the
window; a SINGLE refusal reserves nothing (an agent that takes the alternative the refusal
offers never comes back, and must not hide the item from `next` or block its owner). The holder is told who it holds up: `heartbeat` and `release` name the waiters, and
`brief` adds a "Waiting on you" section. Releasing globs while only gates are pending is
deliberately not offered: until the branch is merged another agent editing the same files
would only meet the conflict at merge. The honest remedy is a short claim: claim when you
are ready to edit, run the gates promptly, and do not sit on a lease waiting for a slow
review (record `partial` and merge instead).

**When two clones disagree.** A merge can bring in a rival definition of one id (two
clones added it) or two claims on one item whose windows overlapped. ddflow records the
contest instead of letting the later event silently win: `show` prints a `CONTESTED` block
with every rival whole, `next` withholds the item, and `doctor` names it. A person settles
it with one event:

```console
$ ddflow resolve P1.T1 --keep alpha            # a claim: by its holder, or its event id
$ ddflow resolve P1.T1 --keep 3fa9c2 --refile-as P1.T1b   # a definition; the loser is re-added
```

Keeping a claim releases every claim that overlapped it and starts the kept claim's lease
window **now**: a claim that had already lapsed is not stretched back over the gap, so a
claim another agent legitimately made in the meantime is not retroactively contested. A
holder's own lapsed claim, every displaced claim (no cap), and a contestant's late renewal
are all kept in the record. An unresolved contest always displays one of its claims (the
latest, which may itself have lapsed -- `show` gives its time left): when a
holder who took over one contestant's lapsed claim releases, that contestant is displayed
again, and `show` (on its lease line) and `status` (on whichever list holds it -- usually
`Blocked`, as a contested item) mark it `previously taken over, first by <holder>`; `status
--json` lists it under `taken_over` (`id`, `holder`, `taken_over_by`, the first taker -- the
record keeps one entry per claim, so a later takeover of the same claim is not named). Its
holder may have moved on, so settle it with `resolve`. `--keep` may also name the current holder when it met no part
of the contest. An item that is not contested is refused.

**More than one person or clone: [the multi-user model](../../docs/ddflow/MULTI-USER.md).** The log
is committed with the code, one shard per writer, so distinct writers merge without a
conflict; a forge's merge button may not honour `merge=union` (unverified; it only matters
when two branches append to one shard, and a local `git pull --no-rebase` clears it);
offline divergence is *detected* (`contested`), not prevented, unless the opt-in remote
claim lock (B192; not shipped yet, so claims are local-only today) is on; and committed session prompts, notes
and evidence are readable by everyone with access to the repository.

**A wrong worktree binding can be corrected.** `ddflow update <id> --worktree PATH` rebinds
the item, and your live lease on it, to that linked worktree and the branch checked out
there. The field edit lands first and the rebind under the same lock, from a fresh read; an
empty path is refused, and `complete`'s tree check then follows the new tree.

**If you are already in one, it adopts that one.** Agent harnesses — Claude Code, Cursor
— often isolate the agent themselves. Claiming from inside a linked worktree binds the
item to *that* tree and branch rather than building a rival and telling you to leave the
one holding your uncommitted work:

```console
$ ddflow claim T1            # run from inside the harness's own worktree
claimed T1 (lease 1800s, renew every 300s)
  worktree: /work/agent-tree  (adopted — you were already in it)
  branch:   agent-work
  Carry on where you are.
```

ddflow never needed to have *created* the tree — it needs to know *which* tree an item
is worked in, so `recover` can find stranded work and `merge` knows what to merge. An
adopted tree is recorded as adopted, not created, so `remove_on_merge` will never delete
something ddflow did not make. A tree already bound to another open item is refused: two
items in one tree cannot be merged or recovered separately — unless that item has let go
of it: merged, lease released, and nothing left in the tree (no uncommitted change, a
`HEAD` its target already contains). `worktree.adopt_existing = false` restores the old
behaviour; `--no-worktree` skips binding entirely. `[worktree] local_files` copies
git-ignored, machine-local files (a `.roborev.toml`) from the primary into every tree a
claim binds — one it creates, the harness tree it adopts, or the item's own tree on a
re-claim — and never overwrites a file already there.

An item claimed `--no-worktree` has no branch of its own, so `merge` and `review` take
one: the branch checked out in the worktree you run them from, or `--branch <branch>`
from anywhere, and `gate run` runs in the worktree you stand in. Two places are not the
item's and are refused rather than guessed: another open item's worktree, and — with
worktrees on — the primary, whose working tree is nobody's in particular (other agents'
event logs, anyone's uncommitted edit); there `merge` refuses, and `review` and `gate
run` record UNAVAILABLE and say what to pass. A lone agent working in the primary says
so with `[worktree].enabled = false`.
The borrowed tree is never removed; uncommitted work in it is refused as for any merge;
and paths the merge lands outside the item's globs are listed (`outside_globs`), since a
borrowed branch can carry another item's commits too. The listing is exact for renamed
files (both paths) and non-ASCII names; when git cannot list the landing at all,
`outside_globs_unknown` is true and the CLI says so, rather than an empty list passing for a
clean scope (it is null for an item landing its own worktree, whose paths are not listed).

```console
$ ddflow merge B-fix --branch agent-work
  no worktree of its own: 'agent-work' was landed, no tree touched.
merged B-fix (1c2d3e4f) into main
```

**What `merge` reports and what it leaves alone.** `sha` is the commit the base points at
after the landing — the merge commit, or the branch head on a fast-forward; the forge's
merge commit in PR mode — and `branch_head` is the merged branch's own head, which under a
squash is not on the base at all. A tree you are standing in is kept (its directory is your
shell's cwd; deleting it fails your next command), and `ddflow cleanup --apply` removes it
once the item is complete. Every removal of a worktree — `merge`, the PR flow, `cleanup --apply`
and the onboarding sweep — is one guarded, logged step (`cleanup.dispose_tree`): under the log's
lock with the leases re-read, a tree another live lease holds or an adopted one is kept and named,
git's own check refuses uncommitted files and unmerged commits unless the caller has proof it may
force, and a removal that worked appends `worktree.removed`. A merge that fails is **aborted**, so the primary checkout is
never left mid-merge (`doctor` flags one that was left, with the `git merge --abort` that
clears it). A branch with nothing ahead of its target is refused — it would record the item
merged with nothing landed — unless `--allow-empty`.

A second agent is refused, and told what to take instead:

```console
$ ddflow claim P1.T4 --agent gamma
P1.T4 writes 'shortener/store*.py' which overlaps 'shortener/store.py' held by alpha on P1.T1

You could take instead: P1.T2, P1.T5
```

Exit codes are the contract, and agents branch on them:

| Code | Meaning |
|---|---|
| `0` | healthy |
| `1` | real failure |
| `2` | could not run / nothing to do — **never** collapsed into 0 |
| `3` | coordination refused |

"Nothing is ready" and "everything is fine" are different facts. An agent that cannot
tell them apart invents work.

Over MCP the code is the result's `_meta.exit`, from the same table the CLI exits with
(`core.outcome.exit_for`): a refusal raised anywhere -- a lease held by someone else, a
reviewer only a person may add, a log written by a newer ddflow -- is `3` with its
remedy, not "bad arguments" or an internal error.

**The critical path is reported**, because it, not the task count, sets the wall-clock
floor — adding a fifth agent to a phase whose runtime is a four-deep chain buys nothing.
It walks nested sub-tasks: an umbrella's open sub-tasks count as steps before it, and a
phase another depends on contributes its chain. Items held back only by a cap
(the parallelism limit, `worktree.max_parallel`, or a resource's capacity) are counted by `status` and `brief`, and the cap's message says when a slot
is free and, under auto, what limits it: "N more are ready but held by the parallelism cap
(auto: limit 6, limited by load per core)". Both print one line about the limit in force,
`parallel: 6 (auto: ceiling 8; limited by load per core)` or `parallel: 4 (fixed)` (`status
--json` and `ddflow_status` carry it as `parallel`); "limited by independent work" means the
offer already holds every ready item that can run beside what is in flight. A signal is named
only while it is over its `low` mark (the one that lowered the limit, only while it still
reads: a null one holds nothing); with every signal healthy the line says "limited by
growth step (next in Ns)" while in flight has reached the limit (it rises one step per
`adapt_up_after_s`), and "limited by demand (N in flight)" while it has not. In fixed mode the
cap's words are `schedule.max_parallel_tasks=N` when the fixed knob holds, and, lowered under
load, `fixed: limit N, limited by <signal>`. A
shrink never touches a running lease: in flight above the new limit simply leaves no slot
until enough finish; the limit is never below 1, and a critical signal pauses new
admissions for that evaluation only. `[schedule].reserved_slots` keeps slots of the limit for
bug fixes and tasks tagged `reserved_tags`: with limit 3 and one reserved, two features run,
the third is held though a slot is free, and a bug fix still starts. A bug fix in flight fills
the reserved share first and may overflow into an ordinary slot; an idle reserved slot stays
free rather than go to a feature. `worktree.max_parallel` keeps the same share of its trees.
The offer does this, as with the cap: `claim` of a named item applies neither. `next`, `status`
and `brief` say it (`parallel: 3 (fixed); 1 of 3 slot(s) reserved for bug fixes and tags
ddflow-gap`). What `next` offers is conflict-aware: the free slots are filled in priority order, but an item whose globs overlap one already offered in the same answer is held back as a `conflict` naming that item ("globs overlap X ... offered in this plan"), and the next independent item takes its slot; `status` counts these apart from the cap-held ones ("N overlap an offered item" vs "N held by the parallelism cap"). `ddflow wait` sleeps until something is ready, but when every blocker needs a An item tagged `no-worktree` (a review, a research task) is not held by `worktree.max_parallel` and is claimed without a tree. With `[flow].claims = "remote"` a claim also takes `refs/ddflow/claims/<id>` on the remote by compare-and-swap, so two clones that cannot see each other cannot both claim an item while online: the second is refused naming the holder, release and completion delete the ref, a heartbeat extends it, a lapsed one is replaced, and an unreachable remote refuses the claim rather than claiming locally.
person — a dependency cycle, an expired lease under `reclaim_policy = "report"` — it
refuses at once rather than sleeping to its timeout.

---

### Lanes: who is working and what to do next

`ddflow lanes` is the coordinator's ledger of agent lanes (an agent identity is a lane). It
is a projection of the event log, so it needs no registration and cannot drift, and it never
starts, stops or resumes an agent and runs no daemon (D-sched-no-daemon). Acting on a lane
(starting or resuming it) is a separate command, still to come.

```console
$ ddflow lanes
LANE     HARNESS      MODEL    ITEM  HEARTBEAT  STATE                 FOR
holder   claude-code   opus     X     2m ago     working               14m
waiter   -            -        Q     8m ago     waiting-on-glob       8m
  waiter: blocked by holder
$ ddflow lanes advise
1. [stall] Y is blocked by lane holder (X: globs overlap X held by holder (f.py vs f.py)); idle: waiter. Ask holder to finish, narrow its globs, or release
   run: ddflow wait --item Y
```

States: `working`; `stale` (it holds its lease but has not beaten for `lanes.stale_after_s`,
default `lease.ttl_s`); `waiting-on-glob` (names the holder) and `waiting-on-dep`;
`idle-with-ready-work`; `stopped (done)`, `stopped (session ended, item unfinished)` and
`stopped (reason unknown)`. The last is what a lapsed lease with no `session.ended` reads:
the log does not say whether the context ran out or the process died, and `lanes` does not
guess. `--watch` redraws when the log changes or every `lanes.watch_interval_s`.

`ddflow lanes fleet` is the durable registry: every agent the log has seen, past the lane
window, with role, harness, model, items, lease and last stop reason, classed `failed`,
`stalled`, `working`, `idle` or `done` in that order. `[lanes] fleet_max_rows` cuts the listing
and says how many more there are. `ddflow lanes resume <lane>` answers how to resume a stopped
lane, or who took its item over; it starts nothing.

`ddflow lanes brief <lane>` renders what a coordinator would otherwise write by hand as the
lane's first prompt: the objective (the item's title and body), its phase, tags, needs and
globs, the open lease and worktree, and the lane's previous run (its last session summary
and notes). Every text an agent wrote is fenced as recorded data. `ddflow lanes advise`
lists the moves, most urgent first (at most `lanes.advise_max`): a stopped lane with
unfinished work, an item blocked by a named lane, lanes idle beside ready work, more working
lanes than the adaptive parallelism limit. Over MCP it is `ddflow_lanes` (`view` = `state`,
`brief` or `advise`; `lane`), in the full tool tier. `[lanes]` knobs: `window_s`,
`stale_after_s`, `watch_interval_s`, `advise_max`, `brief_notes`, `brief_text_chars`.

`ddflow lanes lead` is the lead's channel to the lanes: the same ledger plus, for a lane
whose item sits on the schedule's critical path, an honest **ETA** — a floor in open items
still after it, never an invented number of minutes — and the lead's own pending asks.
A lane's question reaches the lead as a message in its inbox; the lead relays the
operator's answer back with `ddflow session message <lane> --text … --item <id>`
(`ddflow_session_message` over MCP), and the lane withdraws its ask when its next wait,
watch or heartbeat delivers the reply. Both are one write to one inbox (LC-wake), a
message is data, never an instruction, and it starts, stops or resumes nothing.

### Adaptive parallelism: configuration

How many items may be in flight adapts by default (decision D-adaptive-flow-accepted). In
`[schedule]`:

| Knob | Default | Meaning |
|---|---|---|
| `parallel` | `auto` | `auto`: the limit adapts inside `[max_parallel_min, max_parallel_max]`; `fixed`: `max_parallel_tasks` is the ceiling the signals may lower (to `max_parallel_min`) under load, memory, disk, reviewer-latency or gate-failure pressure, and restore when calm -- never exceeding the number |
| `max_parallel_tasks` | `4` | the start value in auto (the limit begins and resets here), the ceiling in fixed |
| `max_parallel_min` | `2` | the floor: auto and fixed never go below it (at least 1) |
| `max_parallel_max` | `8` | the ceiling: auto never exceeds it; machine sizing, so set it with `--local` |
| `adapt_up_after_s` | `600` | healthy seconds, with the limit binding, before +1; no increase for twice this after a decrease |
| `adapt_cooldown_s` | `300` | minimum seconds between two decreases |
| `signal_interval_s` | `60` | seconds between two samples (taken by next, brief, claim and heartbeat; no daemon) |
| `reserved_slots` | `0` | slots of the limit only bug fixes and `reserved_tags` work may take; ordinary work holds at most limit minus this, an idle reserved slot stays free, and one slot of a cap always stays ordinary |
| `reserved_tags` | `["ddflow-gap"]` | tags that make a task reserved work, beside bug fixes |
| `[schedule.signals]` | all enabled; `load_per_core` low 0.15, high 0.75 | `enabled = [...]` switches signals; per signal `low` / `high` / optional `critical` marks, higher worse |

The shipped marks (decisions D-adaptive-flow-accepted and D-unify 8); `high` shrinks the limit,
`critical` pauses admission, and at or under `low` the signal counts as healthy again:

| Signal | `low` | `high` | `critical` | In words |
|---|---|---|---|---|
| `load_per_core` | 0.15 | 0.75 | -- | load average per core |
| `memory_pressure` | 0.75 | 0.85 | 0.95 | free memory under 15% shrinks, under 5% pauses |
| `disk_pressure` | 0.85 | 0.90 | 0.97 | free disk under 10% shrinks, under 3% pauses |
| `reviewer_latency_ratio` | 1.5 | 2.0 | -- | reviews taking over twice the project's usual time shrink |
| `gate_failure_ratio` | 1.5 | 2.0 | -- | gates failing at over twice the project's usual rate shrink |

`gate_failure_rate`, `merge_failure_rate`, `loop_findings` and `independent_ready` ship with
no marks: they are read and recorded but never move the limit until marks are set. A
`[schedule.signals]` table in a config FILE that is not valid (an unknown signal, a missing or
non-numeric mark, `low` over `high`) does not stop ddflow: it is reported on stderr and by
`doctor`, and the strictest table applies instead -- every signal enabled, each mark the lower
of the layers below and the shipped one, the bad table's own numbers ignored
(D-enum-fallback-strict). `config --set` and `ddflow_configure` still refuse it.

`worktree.max_parallel` defaults to `0`, which **follows the schedule limit** -- it is not
unlimited; there is no unlimited setting, the ceiling always bounds it. A nonzero value stays
an independent hard cap on worktrees. `ddflow init` and `adopt` no longer write the old
explicit `max_parallel = 4` / `max_parallel_tasks = 4`; a project that still carries them
loads unchanged (`parallel` absent means auto, and `max_parallel_tasks = 4` is then the
start value), and `ddflow doctor` notes an explicit `worktree.max_parallel = 4` -- it caps
auto at 4 worktrees -- with the remedy `ddflow config --set worktree.max_parallel 0`.

Every knob is set from the CLI (`ddflow config schedule.parallel fixed`, `ddflow config
--local --set schedule.max_parallel_max 6`) and MCP (`ddflow_configure`); the local layer
wins over the committed one. A mark is a nested key: `ddflow config --set
schedule.signals.load_per_core.high 0.8` keeps the `low` mark, because the table merges
layer by layer, mark by mark. In auto the range must hold `max_parallel_min <=
max_parallel_tasks <= max_parallel_max`: an edit that breaks it is refused (exit 3); a file
that already breaks it still loads, the controller clamps it, and `doctor` says so. An
unknown signal name is refused with the list of signals. A signal with no marks (all but
`load_per_core`, as shipped) is read but never moves the limit until marks are set for it.

### Adaptive parallelism: the sample ring and the derived limit

The adaptive limit is **derived and local, never committed**. There is no daemon: every
command that loads the project -- `next`, `brief`, `claim` and `heartbeat` among them, and
heartbeats already run every `lease.heartbeat_s` on every platform -- appends at most one
sample per `schedule.signal_interval_s` to `.ddflow/local/flow/samples.jsonl`: the host
signals (below), the log-derived rates, how many items were in flight and whether the
limit was binding. The ring lives under the git-ignored `.ddflow/local/` (which also
ignores itself, for a project whose `.ddflow/.gitignore` predates it), keeps the last six
hours, and is folded through the controller with the `[schedule]` parameters whenever the
limit is asked for, with the log-derived rates re-read at that moment. `next`, `brief`, `status`
and `wait` plan with that limit. A corrupt or
truncated line is skipped (and the file rewritten on the next sample), a clock that went
backwards drops the sample, a short `O_EXCL` lock file keeps concurrent samplers to one
sample per interval (the file's integrity does not depend on it: whole lines are appended
and rewrites are atomic renames), and a directory that cannot be written leaves the limit at
its start value with the reason. `fixed` mode is sampled the same way: the fixed
`max_parallel_tasks` is the ceiling the signals may lower, never raise. `ddflow doctor` notes a
ring that cannot be read or written and a `.ddflow/local/`
that git does not ignore.

### Adaptive parallelism: host signals

The adaptive parallelism controller steers by the host as well as by the log.
`ddflow/infra/signals.py` samples three host signals with the standard library only; each
one a platform cannot supply is **unavailable** (`None`), never `0`, because a zero would
read as a real measurement. An unavailable signal is neutral: it never lowers the limit
and never justifies raising it, and with no host signal at all the limit holds at its
start value. A sampler that raises or times out is unavailable too, with a one-line reason.

| Signal | Linux | macOS | Windows | Elsewhere |
|---|---|---|---|---|
| `load` (1-minute load average per core) | `os.getloadavg()[0] / os.cpu_count()` | same | unavailable (no `getloadavg`) | same as Linux where `getloadavg` exists |
| `memory_free_frac` (available / total memory) | `MemAvailable / MemTotal` from `/proc/meminfo` | `vm_stat` (free + inactive + speculative pages) over `sysctl -n hw.memsize`, each with a 2 s timeout | `GlobalMemoryStatusEx` | unavailable |
| `disk_free_bytes`, `disk_free_frac` | `shutil.disk_usage` of the worktree root, falling back to the repository root | same | same | same |

The controller reads them as `load_per_core`, `memory_pressure` (`1 - memory_free_frac`) and
`disk_pressure` (`1 - disk_free_frac`), so that for every signal it sees higher is worse.
`ddflow doctor` names each host signal that is unavailable on this machine, with its reason,
as a note, not a problem.

### Log-derived flow signals

Besides the host, the adaptive parallelism controller takes what the project's own event
log shows as input signals (the module computes them; the controller's sampling path that
consumes them is a separate piece of the adaptive-flow work). `ddflow/core/flowsignals.py` computes each as a pure function of the log and an
injected clock, against baselines taken from the same project's history, so no number is
specific to a machine or a repository. Each ends at "now"; a signal with too little history
is unavailable (`None`), which is neutral: it never lowers the limit and never justifies
raising it.

| Signal | Definition | Window | Unavailable when |
|---|---|---|---|
| `reviewer_latency_ratio` | how long the reviewer takes to answer: median `elapsed_s` that `ddflow review` records, per wave of chunks it sent, over the project's median for the 7 days before the window (so a large diff, or a triage gap before a hand-recorded outcome, is not a slow reviewer) | last 30 minutes | under 5 recent or under 20 baseline reviews, or fewer than 3 reviews in flight at once (slow alone is not busy) |
| `gate_failure_rate` | `gate.failed / (passed + failed)`, review gates (`rubber_duck`, `critic`) left out: a failed review found issues, it is not saturation | last 60 minutes | under 10 outcomes |
| `gate_failure_ratio` | `gate_failure_rate` over the project's own rate for the 7 days before the window | last 60 minutes | under 10 recent or 20 baseline outcomes (a clean baseline counts as one failure; a single recent failure counts as at most 1.0, the usual rate) |
| `merge_failure_rate` | failed merge-gate outcomes over merge attempts | last 2 hours | no merge attempt |
| `loop_findings` | findings of `ddflow loops` | now | never (0 when none) |
| `independent_ready` | ready items overlapping neither anything in flight nor each other, by the same selection `ddflow next` offers (parallelism caps lifted) | now | never (0 when none) |

A fresh project simply reports the first four as unavailable until it has history; nothing
needs configuring. A merge git tried and failed (a conflict) is recorded as a failed `merge` gate
outcome, which is what `merge_failure_rate` counts; a merge refused before git tried (target
checked out elsewhere, another merge in progress) is not. Reviewer latency is the `elapsed_s` that `ddflow review` records
in the gate outcome's evidence: the reviewer's own time, not the agent's.

## Many agents, one server: identity, state and sharing

Several agents and subagents sharing one queue is the case this tool is for. Here is
exactly how that works, because each of these has a wrong answer that looks right.

### Is it stateless?

**The queue is. The connection is not, in exactly one respect.**

The append-only event log is the sole source of truth, and every read re-derives state
from it — `fold(read_all())`, from scratch, on every call. Nothing is cached between
requests, so there is no stale projection, no invalidation, and no divergence between
two agents' views. Restart the server mid-task and nothing is lost: it never had
anything the log did not.

The one piece of per-connection state is **who you are** (below). It is deliberately not
in the log, because it is a property of the caller, not of the work.

### Who is calling?

By default, identity is derived from the working tree. That is right for one agent per
worktree, and **silently wrong for several agents in one tree** — they all resolve the
same path to the same name, their events merge into one stream, `brief` answers with a
sibling's task, and reviewer-independence compares an agent with itself and passes.
Nothing errors. There is no signal that can tell them apart, so identity is **declared**:

| How | When |
|---|---|
| `as_agent` argument (MCP, every tool) | A **subagent sharing its parent's connection** — Claude Code's subagents do. Per call; the connection's identity is untouched. |
| `ddflow_identify` (MCP) | An agent announcing itself on its own connection. Call it first. |
| `DDFLOW_AGENT` env var | A harness that spawns agents and knows their names. Process-wide. |
| `--agent` (CLI) | Scripts and one-off commands. |
| tree-derived default | One agent per worktree. Reported as *undeclared*, so you can see it. `{host}-{tree}-{clone}`: the last part is a random suffix kept in `.ddflow/local/clone-id`, so two clones of one repository never write one shard even on same-named machines. |

Under a declared identity a call uses what *that* agent holds, on every surface: an MCP
tool called with a foreign `as_agent` asks from the primary and `ddflow_claim` creates that
agent's own tree instead of adopting the connection's harness worktree, and `ddflow --agent
X claim` does not adopt a tree another identity is working in -- one it has worked in under
its tree-derived name, or one it ADOPTED (`worktree.adopted` names the adopter, so a parent
that always declares `--agent` still owns its tree once it has claimed from it; a parent that
has never claimed from its tree is not yet detectable, and the first identity to adopt a
tree owns it). A reviewer written through
`ddflow_configure` is recorded under the per-call `as_agent`. `ddflow_setup` writes into
the tree the server stands in, as `adopt` does; `adopt` run from a linked worktree writes
the project's tracked files into **that worktree** (the event log and hooks stay shared).

A name you set yourself is never suffixed, so the same `DDFLOW_AGENT` in two clones is
still one agent to ddflow; `ddflow doctor` notes a shard whose clock goes backwards, which
is what that leaves behind after a merge.

Innermost wins. `ddflow_identify` is idempotent, persists for the connection, and
refuses a name that could not be a log filename — it becomes one, and refusing at
declaration time means the caller reads the reason rather than discovering it at the
first write.

`ddflow_identify` also reaches the same harness's **shell**: the name is recorded (in the
primary checkout's `.git/ddflow-identity/`, keyed by the harness's pid and start time), and
a CLI run below that harness that names no identity (`--agent`, `DDFLOW_AGENT`) takes it,
so a `ddflow claim` in the shell and a `ddflow_heartbeat` on the connection are one agent,
and so is the commit hook. Linux only; `ddflow_identify` with no name withdraws it.

**If more than one agent works one tree at once, declare identity.** Everything that
attributes work depends on it. A subagent must not call `ddflow_identify` on a shared
connection — that renames its parent — and passes `as_agent` instead; without it, two
subagents claiming the same file are one holder, and a holder's own leases never
conflict with each other.

### Can one server serve several projects?

**No — one server process serves one repository**, fixed at start from `--repo`,
`DDFLOW_REPO`, or the working directory. No tool takes a repo argument, and a test
asserts none ever does. Point a second agent at a second project by running a second
server; they are cheap, and the isolation is the point.

**One project shared by many agents is the supported case** — and the one that needs no
special setup beyond declaring identity:

- **Writes never conflict**, and they never block readers. Each agent appends to its
  **own log shard**, so there is no shared file to overwrite and no merge conflict to
  resolve. Writers *do* serialise briefly: one repo-wide lock is held across the clock
  allocation, the append and its `fsync`. Short, but not nothing — per-agent shards
  remove *file* contention, not lock contention.
- **Reads take no lock at all**, so a read-heavy agent cannot be starved by a write-heavy
  one, and a reader can never block a writer.
- **File ownership is coordinated by `globs`.** `claim` refuses an item whose writes
  overlap one already held, and names what to take instead — exit `3`, not a failure.
- **Lessons, decisions, research and bug history are shared** by construction: they are
  events in the same log, so one agent's finding is immediately visible to every other.

### Locking, contention and measured cost

Measured on this machine, single process, full `read_all()` + `fold()`:

| Events | read + fold | per event |
|---:|---:|---:|
| 500 | 11.5 ms | 22.9 µs |
| 2,000 | 32.6 ms | 16.3 µs |
| 5,000 | 46.0 ms | 9.2 µs |
| 10,000 | 89.2 ms | 8.9 µs |
| 20,000 | 174.7 ms | 8.7 µs |

Linear, converging on **~8.7 µs/event**; the higher figure at small sizes is fixed
per-call overhead, not the fold. A project with 20,000 events pays ~175 ms for a
state-reading call. Search and recall do **not** pay this — they run off a SQLite
projection rebuilt only when the log's head moves.

`tests/test_mcp_load.py` runs 12 concurrent agents through the real MCP surface and
asserts no deadlock, no lost append, no repeated Lamport value within an agent, and
correct attribution for every event — not for a sample. Its thresholds are environment
variables (`DDFLOW_LOAD_AGENTS`, `DDFLOW_WRITE_LATENCY_BUDGET_S`,
`DDFLOW_GROWTH_TOLERANCE`, …) because a load test with a hardcoded budget either flakes
on a shared runner or is too loose to fail.

The deadlock bound is a **hard timeout**: a wedged lock does not fail, it hangs, and an
unbounded hang reads as a broken CI runner rather than as a bug.

---
