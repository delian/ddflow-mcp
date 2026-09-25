# Driver: `implement phase <NAME>`

**This file is the canonical, agent-agnostic driver.** Every agent — Claude Code, Gemini
CLI, Codex, Copilot, Kilo, Cursor, or a human — follows *this* document. Per-agent files
in `deltas/` contain ONLY the handful of things that genuinely differ (how to loop, how
to ask a question, how to spawn a subagent, how to reference a file). They are deltas,
never copies.

> Why deltas and not per-agent copies: a copied driver drifts. On the project this was
> extracted from, a reworded per-agent duplicate silently accumulated three instructions
> that were false at the time of writing, while missing four gates the canonical file
> had gained. A delta removes the surface that can drift instead of policing it.

Every command below is `ddflow ...`. If your harness exposes ddflow over MCP, the
equivalent tool is named in brackets — they are the same implementation.

---

## 0. Open the session (once)

```sh
ddflow doctor                                    # [ddflow_doctor]
ddflow recover                                   # [ddflow_recover]
SESSION=$(ddflow --json session start --model "<your model id>" --tool "<your harness>" | jq -r .session)
ddflow session prompt "$SESSION" --text "<the operator's message, verbatim>"
ddflow brief --phase <NAME>                      # [ddflow_brief]
```

`ddflow brief` replaces reading the project's rule files and lesson corpus. It returns
the rules pointer, the ready set, and the lessons *ranked against this phase's text*,
inside a token budget. Read it instead of the corpora, not in addition to them.

**If `recover` reports anything, deal with it before starting new work.** A crashed
agent's worktree frequently contains finished work that exists nowhere else. Inspect the
named tree, salvage what is real, then `ddflow release <id>`. Never delete first.

---

## 1. Phase-level research (once per phase)

Before any task starts, answer the phase's open questions and record them:

```sh
ddflow research --question "<what you needed to know>" \
  --claim "<the falsifiable claim>" \
  --falsifier "<the single observation that would kill it>" \
  --probe "<the command you ran>" --probe-output "<what it printed>" \
  --verdict CONFIRMED|REFUTED|THEORETICAL --sources "<urls you actually opened>"
```

Rules that make this worth doing:

- **Probe before you claim.** Run the cheapest test that could refute the claim. Tier 0
  is "does this symbol/file/flag even exist" and is the highest-yield rung.
- `CONFIRMED` and `REFUTED` **require** a probe; ddflow refuses them without one.
  `THEORETICAL` must say why no probe was possible.
- **A rejection is as valuable as an adoption.** "We evaluated X and rejected it, here
  is the measurement" stops the next session re-researching it.
- A pass with zero CONFIRMED/REFUTED labels is a literature summary. Label it one.

---

## 2. The task loop — repeat until the phase is empty

### 2a. Pick work

```sh
ddflow next --phase <NAME>                       # [ddflow_next]
```

- **exit 0** — one or more items are ready. Items in the ready set are *independent*:
  fan them out to parallel subagents if your harness supports it.
- **exit 2** — nothing is actionable. This is a **result, not an error**. Read the
  blocked list: it says whether each item waits on a dependency, on another agent's
  lease, or on a file conflict. Do not invent work.

Never take an item that `next` did not offer.

### 2b. Claim it

```sh
ddflow claim <ID> --globs "<paths this task will write>"   # [ddflow_claim]
```

- **exit 0** — you hold the lease and a git worktree was created. `cd` into it. Work
  ONLY there.
- **exit 3** — refused, and the message names the holder and lists what you could take
  instead. Re-order; do not wait, and never `--force` past another live agent.

Renew during long work: `ddflow heartbeat <ID>` (interval: `lease.heartbeat_s`).

If your task needs to write outside its declared globs, run
`ddflow update <ID> --globs "..."` **first**, so the conflict detector can see it.

### 2c. Run the task pipeline

```sh
ddflow gate status <ID>                          # [ddflow_gate_status]
```

It prints the pipeline, the next gate, and that gate's instruction. The default order is
the ten steps below; `.ddflow/gates.toml` changes it per project.

| # | Gate | Who runs it | How to record |
|---|---|---|---|
| 1 | `research` | you | `ddflow research ...` then `gate record` |
| 2 | `rules` | you | `ddflow brief --item <ID>` |
| 3 | `implement` | you | `gate record <ID> implement --outcome passed` |
| 4 | `rubber_duck` | a **different-family** model | `gate record ... --model <reviewer model>` |
| 5 | `critic` | a **different-family** critic | same |
| 6 | `standards` | tooling | `ddflow gate run <ID> standards` |
| 7 | `unit_tests` | tooling | `ddflow gate run <ID> unit_tests` |
| 8 | `bug_hunt` | you | `gate record` + a probe per finding |
| 9 | `dedupe` | you | `gate record` |
| 10 | `merge` | ddflow | `ddflow merge <ID>` |

Four rules bind across all of them:

1. **UNAVAILABLE is never a pass.** If a reviewer, endpoint or tool could not run, record
   `--outcome unavailable --reason "<why>"`. Recording it as passed is how an entire
   review silently disappears from a project's history.
2. **Evidence or it did not happen.** Gates in `gates.evidence_required` refuse a bare
   pass. Attach the command, its exit code and its output.
3. **At least one reviewer must be a different pretraining family than you.** Same-family
   agreement is not independent evidence — it measures shared priors. `ddflow complete`
   refuses without it. Pass `--model` on every review so ddflow can tell.
4. **A bug may only change source if a runnable probe demonstrates it**, and that probe
   ships as the regression test in the same commit. Then **revert the fix and watch the
   probe fail** — a probe that passes both ways proves nothing. No probe → record it as
   a `THEORETICAL` finding and change nothing.

Reviewers must be told to **refute, not review**: "find the input that makes this wrong;
if you are uncertain, report nothing." And a majority of reviewers may **kill** a
finding; it may never **promote** one.

### 2d. Close the task

```sh
cd <worktree> && git add <explicit paths> && git commit -m "<ID>: <what changed>"
ddflow merge <ID>                                # [ddflow_merge]
ddflow complete <ID> --model "<your model>" --sha "<sha>"
ddflow release <ID>
```

`complete` exits 3 and lists **every** unmet condition at once. Fix them; reach for
`--force` only with a reason you are willing to see in an audit (it is recorded).

Never `git add -A` — a parallel agent's unrelated file staged into your commit is very
hard to notice and very hard to undo.

### 2e. Capture what you learned

Any bug, any operator correction, any surprise:

```sh
ddflow lesson add --title "<the rule, as one line>" --rule "..." --why "..." --how "..."
ddflow bug fixed <BUG> --regression-test "<test that now guards this>"
```

`bug fixed` refuses without a regression test. That refusal is the mechanism that stops
the same bug shipping twice.

### 2f. Check the cadences

```sh
ddflow cadence                                   # [ddflow_cadence]
```

Exit 2 means none due. When one is due, run it and record it with `--ran <name>`.
These are the passes a per-task gate structurally cannot do: integration tests,
architecture review, mutation testing, a duplication sweep across files, lessons
compression.

---

## 3. Phase-level close

When `ddflow next --phase <NAME>` reports no remaining tasks:

```sh
ddflow gate run <NAME> unit_tests                # the WHOLE suite, not the task's slice
ddflow gate record <NAME> bug_hunt   --outcome passed --evidence "..."
ddflow gate record <NAME> dedupe     --outcome passed --evidence "..."
ddflow gate record <NAME> live_test  --outcome passed --evidence "<real run output>"
ddflow gate record <NAME> corrections --outcome passed --evidence "..."
ddflow complete <NAME> --model "<your model>"
ddflow render                                    # regenerate the human-readable board
```

`live_test` is the one most often skipped and the one most worth keeping: **a green unit
suite and a working feature are different claims.** Run the real thing on a small input
and paste what it printed.

---

## 4. Close the session

```sh
ddflow session end "$SESSION" --summary "<what shipped>"
ddflow render && ddflow doctor
git add .ddflow/events docs/ddflow && git commit -m "ddflow: session log"
```

**Commit the event log.** It is the source of truth and the only artefact from which the
project can be reconstructed. The index (`.ddflow/index.db`) is gitignored and
disposable on purpose.

---

## Standing rules

- **Claim before you edit.** An unclaimed edit can be destroyed by a parallel agent.
- **One task, one worktree.** Never work in the primary checkout; never switch its
  branch — that swaps files under any live session.
- **Exit codes are the contract:** `0` healthy · `1` real failure · `2` could not run /
  nothing to do · `3` coordination refused. Never treat 2 as 0.
- **Prefer `ddflow brief` over reading the rule and lesson files.** That is what it is
  for, and what keeps a session's opening cost roughly constant as the project grows.
- If something goes sideways, **stop and re-plan**. Do not keep pushing.
