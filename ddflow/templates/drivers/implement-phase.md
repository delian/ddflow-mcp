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

**To run this unattended**, use the `implement` workflow command (the MCP prompt
`implement`; `ddflow prompts show implement` in a shell). It drives this driver one item
per iteration and adds what an unsupervised run needs: the only four cases in which the
loop may stop, keeping it running across turns, when to ask the operator, and the
termination checklist. Your delta says how your harness loops it.

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
- **exit 1** — failure: `--phase` names no phase or item. A typo, not an empty queue.

Never take an item that `next` did not offer.

**Model-tier hint (advisory).** An item tagged `tier:fast`, `tier:balanced` or `tier:deep`
shows it beside its title in `next` and in the brief's header; an untagged item shows
nothing. A harness that dispatches a subagent for the item may map it to its own model
choice: `fast` -- a cheap model for mechanical bulk work (renames, format sweeps, large
mechanical edits); `balanced` -- the everyday model for implementation; `deep` -- a
top-tier model for architecture trade-offs. It is advice only: ignoring it is always
correct, it never changes which reviewers count as independent, which gates run or what
`next` offers, and an unknown value (`tier:foo`) is ignored and noted by `ddflow doctor`.

### 2b. Claim it

```sh
ddflow claim <ID> --globs "<paths this task will write>"   # [ddflow_claim]
```

- **exit 0** — you hold the lease and a git worktree was created. `cd` into it. Work
  ONLY there.
- **exit 3** — refused, and the message names the holder (or, "reserved for <agent>",
  the waiter who is next in line for those files) and lists what you could take instead.
  Re-order, or `ddflow wait --item <ID>` for your place in line (first come, first
  served; a hot file no longer starves its longest waiter). Never `--force` past another
  live agent.

**Keep the claim short.** Claim when you are ready to edit, not when you start reading;
run the gates promptly; and do not sit on file globs while only a slow review or roborev
is pending -- record that gate `partial`, merge, and read the result later. `heartbeat`,
`release` and `brief` name who waits on you: that is your cue to finish. Releasing globs
before the merge is deliberately not a command: the next agent would edit files whose
unmerged changes then conflict at merge.

Renew during long work: `ddflow heartbeat <ID>` (interval: `lease.heartbeat_s`).

`claim` prints the globs it recorded -- check them. `--globs` takes a comma list, a
JSON array, or several flags; the claim's globs become the item's.

If your task needs to write outside its declared globs, run
`ddflow update <ID> --globs "<every glob, old and new>"` **first**, so the conflict
detector can see it. It REPLACES the list (and moves your lease to it), and prints
anything it dropped.

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
| 6 | `standards` | tooling | `ddflow gate run <ID> standards`; for roborev: `roborev review <sha>` (an explicit sha, never `HEAD` from a worktree), then `gate record ... --reviewed-sha <sha>` |
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

Launch independent reviewers **concurrently**, and **wait for every one to report** before
merging: a reviewer that has not reported yet is not a reviewer that found nothing. **No
reviewer sees another's verdict** — each gets the diff, the intent and your research
notes, nothing else. A reviewer shown a prior verdict stops being an independent sample
and becomes a vote on someone else's hypothesis.

**Review rounds are budgeted.** Each later round of one gate finds fewer defects than the
last, so ddflow allows `[review].max_rounds` (default 2) rounds per gate per item, full
and delta alike: round 1 on the finished diff, round 2 for after you fixed a confirmed
HIGH/MEDIUM defect. A third round is refused (exit 3). Settle the rest with `ddflow review
triage` (always allowed) -- a confirmed finding's probe names the test that now passes --
and the triage that settles the last finding records the gate passed on refutation
(flagged in `gate status`); one you cannot settle goes to the operator. `--force --reason "..."` is the operator's
recorded exception; `ddflow config review.max_rounds N [--local]` (0 = unlimited,
`review.on_exceed = "warn"` to warn only) changes the cap. Once a gate has a recorded
review, a plain `ddflow review <ID> --gate G` is a FULL re-review: the item's whole diff plus
the previous findings and your triage of each, so the reviewer checks each fix or probe and
looks for new issues (decision D-gate-economy). `--delta` sends only the commits since the
reviewed head (the output says "delta review of N commits since <sha>", the findings merge
into the gate's record and earlier triage stays), for a diff too large to send twice;
`ddflow config review.delta_default true [--local]` makes every plain re-review a delta
(`--full` then still asks for a full round).

**Tests: the relevant ones while you work, all of them before the merge, always in parallel.**

```sh
ddflow tests --item <ID>                         # [ddflow_tests] after EACH change
```

- **While you work**, run what `ddflow tests` prints after each change: the tests your
  diff reaches, derived from the import graph and the changed files, and one command that
  runs them in parallel. Do not reason about which tests matter — that is guessing, and
  the derivation is cheaper than being wrong. A regression test you are writing is in the
  set as soon as its file exists.
- **At the gate**, commit first (`git add <paths> && git commit -m "<ID>: ..."`: ci tests the
  committed HEAD), run `ci` -- it runs the **whole** suite on the branch merged with the
  base -- then `ddflow gate run <ID> unit_tests`; never record unit_tests from a run of
  your own. Once ci has passed on the clean commit the tree holds, a bug fix or a small
  task runs only the selection there (decision D-gate-economy 1; the evidence lists the
  tests and why); anything else, or an edit after ci, runs the whole suite. A targeted run says your
  change is fine and nothing about what was already broken; the full run is where
  standing breakage surfaces.
- **Always in parallel.** Run pytest with `-n auto` (pytest-xdist) or the project's
  configured worker count; a serial run of a large suite is the slowest step in this
  loop. If `ddflow workflow` says the test command runs on ONE core, fix the command
  before the next gate, or record why serial is deliberate (`-p no:xdist`).

### 2d. Close the task

```sh
cd <worktree> && git add <explicit paths> && git commit -m "<ID>: <what changed>"
ddflow merge <ID>                                # [ddflow_merge]
ddflow complete <ID> --model "<your model>" --sha "<sha>"
ddflow release <ID>
```

**Update the README in the same task.** A task that changes what a user or agent sees
(a command, flag, MCP tool, config knob, event kind, gate behaviour, default, refusal
message or documented workflow) edits the README section that describes it, before the
reviewer gates so they see it. If your diff changes code under `[enforce].readme_code_globs`
(default `ddflow/**`) and not `README.md`, `complete`, `ddflow gate status` and `ddflow
brief` all say `README not updated`: name the section you changed, or, for a change with
no visible effect (a refactor, a fix the README never described), record why with
`ddflow gate skip <id> docs --reason "..."`. Test-only and docs-only changes and event-log
commits are never reported. `[enforce].readme_with_code` is `warn` by default; `block`
makes `complete` refuse and `off` silences it.

`complete` exits 3 and lists **every** unmet condition at once. Fix them; reach for
`--force` only with a reason you are willing to see in an audit (it is recorded).

Never `git add -A` — a parallel agent's unrelated file staged into your commit is very
hard to notice and very hard to undo.

**Check a completion you doubt** (yours, or one you inherit): `ddflow verify <id>` re-derives
what `complete` claimed from the log and git; `ddflow verify --all` ranks every completion by
suspicion. A completion that FAILS goes back with `ddflow verify <id> --reopen`; a task that is
still open but whose work landed is named by `ddflow verify <id>`. `ddflow help verify` has the
rest.

### 2e. Capture what you learned

Before you file anything -- a bug, task, lesson, decision, research or memory -- run
`ddflow similar "<the text>"` and look at what is already there. Every add runs the same
check, and one that reads like an existing record is **refused** (exit 3, `refused:
possible duplicate`) with the candidates and the commands that answer it: `--new` (a
different record), `--extends ID` / `--duplicate-of ID` (the same thing) or `--related ID`
(linked both ways). Prefer extending an **open, unclaimed** record -- the text is appended
to it and no new id is made; a record somebody has claimed, or that is closed, gets a new
record linked to it instead. `--check` shows the candidates without writing anything.

Any bug, any operator correction, any surprise:

```sh
ddflow lesson add --title "<the rule, as one line>" --rule "..." --why "..." --how "..."
ddflow bug found --summary "..." --item <ITEM>                   # files fix-<BUG> in the queue
ddflow bug fixed <BUG> --regression-test "<test that now guards this>"
```

`bug fixed` refuses without a regression test. That refusal is the mechanism that stops
the same bug shipping twice.

A bug is an item in the queue: `bug found` files its fix task `fix-<BUG>` (tagged a bug
fix, on the named item's files, under its phase) so the next free agent claims it before
new features and a feature on the same files waits. Record the bug FIRST, then: a bug
inside your claimed files that blocks your work is fixed in your item -- file it with
`--no-task`; anything else is left to the fix task. `complete fix-<BUG>` refuses while
the bug is open; `complete fix-<BUG> --regression-test <test>` closes it and completes.

### 2f. Check the cadences

```sh
ddflow cadence                                   # [ddflow_cadence]
```

Exit 2 means none due. `complete <phase>` refuses while a phase-counted pass
(architecture review, mutation tests, lessons) is overdue. When one is due, run it and record it with `--ran <name>`.
These are the passes a per-task gate structurally cannot do: integration tests,
architecture review, mutation testing, a duplication sweep across files, lessons
compression.

---

## 3. Phase-level close

When `ddflow next --phase <NAME>` reports no remaining tasks:

```sh
ddflow cadence                                   # run every phase-counted pass that is due,
                                                 # then `cadence --ran <name>` (or record a
                                                 # skip: `--ran <name> --note "skipped: ..."`)
ddflow gate run <NAME> unit_tests                # the WHOLE suite, in parallel, not a slice
ddflow gate record <NAME> bug_hunt   --outcome passed --evidence "..."
ddflow gate record <NAME> dedupe     --outcome passed --evidence "..."
ddflow gate record <NAME> live_test  --outcome passed --evidence "<real run output>"
ddflow gate record <NAME> corrections --outcome passed --evidence "..."
ddflow gate record <NAME> docs       --outcome passed --evidence "<docs updated, or: no user-visible change, because ...>"
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
- **A refusal that begins "REFUSED: this project's log has been worked on by ddflow X" is
  the skew guard:** the running one is older than the one that already worked on the
  project's log, and writing could drop what the newer one recorded. Upgrade ddflow-mcp to >= X (in a source checkout: merge main), restart
  the MCP server and retry; reads still work. If you cannot, ask the operator — only if they
  insist, rerun with
  `--allow-older-version --reason "<why>"` [MCP: the `allow_older_version` argument]. It is
  recorded (`skew.overridden`), marks that session's events as written by an older ddflow
  and covers this session only. Never add the flag on your own initiative.
- **Prefer `ddflow brief` over reading the rule and lesson files.** That is what it is
  for, and what keeps a session's opening cost roughly constant as the project grows.
- **Before compressing or rewording an instruction file**, run `ddflow pins <file>`
  [ddflow_pins]. A sentence that reads like rationale is often a rule a test asserts; it
  names the suites to re-run afterwards and the text no test holds.
- **Review a project document with `ddflow export <doc>`; never edit a generated file.**
  `ROADMAP.md`, `BUGS.md`, `CHANGELOG.md` and the other exported documents carry a
  `ddflow:generated` header and are regenerated from the log: a hand edit is detected and
  refused (and the pre-commit check names it). Change the source (the log, or a template
  from `ddflow export eject <doc>`), then `ddflow export <doc> --update`. Printing writes
  nothing; `ddflow export` lists what is selected and each document's state.
- If something goes sideways, **stop and re-plan**. Do not keep pushing.
