# Swarm pilot: the toy phase and baseline runner

Task SW-pilot-toy, for SW-pilot-local. Decisions: D-swarm-pilot-2026-11-05 (sandbox repo, agent-hours
instead of dollars), D-swarm-answers Q4 (targets). Status: the harness is built and proven with stub
workers. No live model has run here, so **nothing below is a pilot result**.

## What exists

| Piece | Where |
|-------|-------|
| The toy phase: 12 doc-and-test items, disjoint globs, chain of 4, one item its gate sends back once | `demos/swarm_pilot_toy.py` |
| The sandbox: creation, opening, guards, clean environment | `demos/swarm_pilot_toy.py` |
| Lanes, the stub worker, the report | `demos/swarm_pilot_run.py` |
| The figures of one log | `ddflow metrics swarm --phase TOY` (`services/swarm_metrics.baseline`) |
| The headline rows in the summary | `ddflow metrics`: `swarm.merged_per_hour`, `swarm.rework_rate`, `swarm.agent_hours_per_item` |

The baseline is the same runner with one lane, so a pair differs in nothing but lanes:

    python3 demos/swarm_pilot_run.py setup DIR1
    python3 demos/swarm_pilot_run.py setup DIR3
    python3 demos/swarm_pilot_run.py run DIR1 --lanes 1 --work-s 2
    python3 demos/swarm_pilot_run.py run DIR3 --lanes 3 --work-s 2
    python3 demos/swarm_pilot_run.py report DIR1 DIR3

A lane pulls the way a real lane does, through the real CLI: `next`, `claim`, work in the worktree,
`gate record` and `gate run`, `merge`, `complete`. One run per sandbox, so one log is one figure.

## The sandbox never touches the host

Red main here is why the sandbox exists: an item must complete on its gates, never through the forced
path. Each of these is checked and tested:

- Created only apart from the host (neither inside it nor holding it) and outside any other work
  tree, in an empty directory.
- Opened only apart from the host and with its manifest (`.ddflow/local/swarm-pilot.json`), as a repository of its own: not a
  linked worktree, no remote.
- Every process gets an allow-listed environment: no `GIT_*` redirect, no `DDFLOW_*` setting, no
  provider key, no `PATH` entry in the host, and a `HOME` and temp directory of its own beside the
  repository.
- Only the verbs a pilot needs can be called: `review`, `lanes`, `config` and `reviewers` are refused,
  and so is `--force`. Git takes four verbs, none of them `config`, and only inside the sandbox.
- The report checks the head of the sandbox is unknown to the host and counts overrides. A side that
  fails either, merged fewer items than it holds, or differs in worker, work time or item count is
  reported NOT COMPARABLE, and no target is judged.

## Cost: agent-hours, not dollars

The 1.3x cost target of D-swarm-answers Q4 is stated in dollars, and the pilot reports agent-hours
(D-swarm-pilot-2026-11-05, point 3). So `swarm_metrics.compare` still gives the agent-hours ratio as
`cost_x` with `cost_basis = agent_hours`, but `targets.cost` stays None and `not_measured` says why: only
dollars judge that target. Before this task the ratio judged it silently. The report prints
`cost target 1.3x: NOT MEASURED`. Agent-hours are lease-held time plus recorded review time: the
author's lease and the reviewer as a second agent working inside it, so both count. The window from
`merge` to `complete` is in neither.

## What the stub runs show

Host under heavy load from other lanes (load average 75 to 117), one pair each, 12 of 12 merged, no
override, all completions on their gates. Stub time is the only work, so these figures measure ddflow's
own overhead and the harness:

| Stub work per attempt | Merged per hour, 1 lane | 3 lanes | Ratio | Rework | Agent-hours per item |
|-----------------------|------------------------:|--------:|------:|-------:|---------------------:|
| 2 s | 387.88 | 973.01 | 2.51x | 8.33% both | 0.0018 to 0.0017 |
| 0 s | 459.52 | 1248.92 | 2.72x | 8.33% both | 0.0014 to 0.0013 |

Two things follow. ddflow's coordination is not the bottleneck at three lanes: the ratio sits at 2.51x
to 2.72x of a ceiling of 3x (three lanes, and a chain of 4 over 12 items is no tighter). And a lane
costs about 8 s of ddflow verbs per item on this host, which real work of minutes will dwarf. One pair
each on a loaded host is a sanity check, not a distribution.

Not measured here, and left to SW-pilot-local: the host contention of a real suite (the sandbox gate is
a one-test unittest), review time (no review gate, independence off), undetected lease violations and
reviewer independence coverage. Those are the invalidators of research section 7.6, so a stub ratio
says nothing about them.

## For SW-pilot-local

- Real lanes run in a sandbox from `setup`. Its `.ddflow/local/config.toml` is the stub profile: no
  review gates and `reviewer_independence = "off"`. Replace both, and set `worker`, `lanes` and
  `live_model` in the manifest, which `report` reads.
- **B-merged-uncompleted-claimable must be fixed first.** Between `merge` and `complete` an item is
  still offered, and another lane's claim reworks it and wipes its gates. The first full 3-lane run hit
  this within about 30 seconds. The stub lanes avoid it through a board they share; real lanes share none.
