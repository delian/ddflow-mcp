# Handoff — where this work stopped and how to resume it

Written 2026-09-27 at the end of a long session, for an agent starting with **no context**.
Read this, then `docs/BACKLOG.md` for the item you pick. Everything here is verifiable from
the repository; nothing depends on remembering the conversation.

---

## 0. The integration branch (`worktree-bridge-cse_01QyuNgytTMVWo5zwfbF8CNS`)

A separate session, 2026-09-27, asked one question: can ddflow — over MCP — replace the
hand-rolled agent workflows of **run_nemo_run** and **home-simulator** (tasks, lessons,
lessons-summary, OptMem, journal, reviews, long runs), and if not, build what is missing.
Both are driven by `claude remote-control` services (`~/.config/systemd/user/claude-rc@.service`).
This branch is the answer. It is **not merged**; it branches from `4f54455` (main at the
time) and every commit message states its own verification.

**What it adds** (README sections of the same names; each MCP tool has its CLI twin):

| Gap in those workflows | Now |
|---|---|
| Subagents share their parent's MCP connection; `ddflow_identify` renamed the parent, and one holder never conflicts with itself | `as_agent` on every tool but `ddflow_identify` |
| Import offered every deferred/refuted/declined box as work (1,179 on run_nemo_run vs its picker's ~120) | dispositions (item, heading, `**STATUS**`), `archive_globs`, `unblock` (phase = section); `next` finds the same 120 actionable (ready, or withheld only by the parallelism cap) |
| 176 numbered lessons imported as 24 date-groups; `docs/LOG.md` journal never read; no lessons-summary | `_id_entries`, `LOG.md` + Index skip, `Lesson.summary` + `LESSONS-SUMMARY.md`, `*_globs` knobs |
| OptMem store (`scripts/memo`) read FIRST each session, by prose | `memory add/list/forget`, shown in `brief`, searched by `recall`, imported from `LOG.txt` |
| Nothing puts the brief into a session after compaction | `hooks install --claude` (SessionStart; drift, due cadences, external deps) |
| GPU runs, vLLM fleets, multi-hour jobs | `resources` on claims (`[schedule] resources`), `job run/add/list/end` |
| home-simulator waits on trainer items in run_nemo_run | `needs = ["run_nemo_run:132.D"]`, `external sync` |
| `Phase:` trailers; critic exit 2/3; roborev exit 0 with findings; post-merge review | commit-msg hook + `item_trailer_keys`; gate `unavailable_exits`/`partial_exits`/`require_output`/`fail_output`; `review --commit` |
| "Weekly bug hunt" in prose | `[cadence] every_days` |

**How it was checked.** Dry and applied imports of throwaway clones of both projects
(`import --apply`, `next`, `brief`, `render`, a live MCP stdio session driving every new
tool). All three reviewers of §5 ran: roborev on every commit (824–833, 835 on the whole
branch), the cross-family critic on each feature commit, and a different-model
rubber-duck; every CONFIRMED finding is fixed in a commit titled after it, two are kept
deliberately and pinned by tests (`tests/test_rubber_duck_integration.py`). Verified on
`4328648` in a clean snapshot worktree: ruff clean, `pytest tests/ -q -m ""` **1447 passed,
3 skipped**, `demos/run_all.py` **6/6, 219 assertions**. The critic run on `afd9755`,
`bdc8ddf`, `6093a1b`, `12487c8` had not reported when this was written (its logs were in the
session's scratchpad) — those commits were reviewed by roborev and the rubber-duck only.

**Integrating** (not done — it changes those repositories' workflows; operator decision):

```toml
# run_nemo_run/.ddflow/config.toml
[importer]
archive_globs = ["docs/todo.md", "docs/todo/archive/*.md"]   # legacy: driven only when named
[enforce]
require_item_trailer = true
item_trailer_keys = ["Phase", "Phase-ships"]
[schedule]
resources = ["gpu=8"]
max_parallel_tasks = 8   # both caps default to 4; `next` withholds the rest as "cap reached"
[worktree]
max_parallel = 8
[cadence]
every_days = ["bug_hunt=7", "dedupe_sweep=7"]
[gate.critic_cmd]      # scripts/critic_review.py, as a command gate
command = "scripts/critic_gate.sh"   # a wrapper passing --dirty and the item's intent
unavailable_exits = [2, 143]
partial_exits = [3]
require_output = '^STATUS:'
```

home-simulator: defaults import it faithfully (`docs/LOG.md` is its journal); add
`[schedule] repos = ["run_nemo_run=../run_nemo_run"]` and `resources = ["gpu=8"]`. Its
lessons corpus has two duplicated ids (L52, L103): they import as `L52`, `L52-2`, and
summary bullets citing them become consolidated lessons. Both: `ddflow adopt --agents
claude`, `ddflow hooks install --claude`, and `ddflow import --max-tasks <n> --apply`
(run_nemo_run needs `--max-tasks` > 1,125). The pre-commit framework both use owns their
git hooks: add `ddflow hooks check-commit` / `check-msg "$1"` as local hooks there.

**Dogfooding (B7) was evaluated, not done**: in a throwaway copy, `adopt` + the MCP
surface work; `docs/BACKLOG.md` has no checkboxes, so it imports nothing until converted
(~30 lines of conversion gave 23 open tasks, matching its audit). Seven defects that run
found are fixed on this branch (`afd9755`). Still B7's operator decision (§8).

---

## 1. Read these first, in this order

| # | File | Why |
|---|---|---|
| 1 | *(none yet)* | **This repo has no `AGENTS.md` or `CLAUDE.md`** — adopting ddflow into itself is B7, an operator decision (§8). Earlier versions of this file said to read them |
| 2 | `docs/BACKLOG.md` | 170 entries, with `✅ CLOSED` markers. The **top** section is a 2026-09-27 audit |
| 3 | `docs/RESEARCH.md` | R15 is the agent-config research; §R-log covers the event log |
| 4 | `docs/ARCHITECTURE.md` | the layering the AST test enforces |
| 5 | `README.md` | the user-facing surface. It has ratchets pointing at it — see §4 |

**The house rules that are not negotiable** (they live HERE, since there is no `CLAUDE.md`;
every one of them caught a real defect — see §6):

* **No source change without a runnable probe that fails before the fix and passes after.**
  Then MUTATION-VERIFY it: revert the fix, watch the test go red, restore. A test that
  passes both ways proves nothing, and is the single commonest failure here. Run every
  mutation check with `PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX=$(mktemp -d)` (§6.6).
* **`uv run pytest tests/ -q -m ""`** — the `-m ""` is load-bearing. The default `addopts`
  is `-m 'not slow'`, so a bare run silently **deselects 21 end-to-end scenarios**. A commit
  touching any surface must run with `-m ""`.
* **`uv run python demos/run_all.py`** — 6 scenarios, 219 assertions, ~5 minutes. These
  caught a break that 1,071 unit tests missed.
* Never add `Co-Authored-By` trailers to commits.

---

## 2. State of the tree right now

```
branch: main          (a standalone repo; the parent run_nemo_run tree is unrelated)
HEAD:   the commit that carries THIS version of this file — `git log -1 -- docs/HANDOFF.md`
        (written this way on purpose: a hard-coded sha here went stale the moment it was
        committed, twice)
```

**Verified.** `0777084` — the previous session's tip, which it had NOT fully run — was
re-run on 2026-09-27 in a clean worktree: **1281 passed, 3 skipped, ruff clean**. The commit
on top of it (Kilo, B21, B18) is verified in its own commit message; if that message does
not say "full suite green", it was not.

```sh
uv run ruff check . && uv run ruff format --check .
uv run pytest tests/ -q -m ""        # ~21-25 min.
uv run python demos/run_all.py       # ~2.5-5 min. Expect 6/6, 219 assertions.
```

**Run a full suite in a separate worktree, not the one you are editing.** Tests spawn
`python -m ddflow` from the working tree, so editing during a 20-minute run silently mixes
two states into one result. `git stash create` + `git worktree add --detach` is the
snapshot — and it does NOT carry untracked files: copy new test files in, or the run
quietly omits them. Both happened on 2026-09-27.

**Open follow-ups from the 2026-09-27 adversarial review** (real, small, not done):
* Projects adopted for Kilo before the fix still hold a dead `mcpServers.ddflow` block.
  Kilo ignores it (probed); re-running `adopt --agents kilo` adds the working `mcp` entry
  but does not remove the dead one.
* `enforce._out_hint` names no `--out` when stale views span several directories.
* **CI's `quality` job had failed on every run since it was written**, three failures deep,
  each hiding the next: `ddflow --version` did not exist; bandit had 12 unreviewed findings;
  the "no runtime dependencies" assertion contradicted the deliberate Jinja2 dependency.
  All three fixed 2026-09-28. **Only `gitleaks` was not run locally** (the binary is not
  installed here) — it is the one step of that job still unverified.
* **`demos/run_all.py` hardcodes `/tmp/ddflow-demos` and `rmtree`s it.** Two sessions
  running demos at once wipe each other's repos mid-scenario: on 2026-09-27 a combined-tree
  run failed `mcp-orchestration` (AGENTS.md vanished after `ddflow_setup` succeeded) while
  another session's two demo runs were live; re-run with a private base dir it passed 6/6.
  A per-run base (env var or `tempfile.mkdtemp`) fixes it. Until then, run demos alone.
* **Seven git path listings still read without `-z`**, so a non-ASCII name comes back
  C-quoted (wrong, not a crash): `services/gates.py:610,707,763` (the gate TREE
  FINGERPRINT — B21's `tree_sha` — hashes paths from these), `infra/worktree.py:246,378,407`,
  `surfaces/commands/setup.py:416`. The reader to use is `infra.worktree.git_paths`
  (`--porcelain` needs `-z` too, but its records are `XY path`, so it wants its own parse).

**Queue roborev on whatever you commit** — every run on this series has found something
real. On `7216f5e` (job 817) it found two CONFIRMED defects the adversarial subagent had
missed, both fixed in the commit after it: the view check compared the STAGED view with
the log ON DISK (a view ahead of its committed log passed), and `adopt` printed "adopted"
and exited 0 after SKIPPING an MCP registration — the wrote-nothing-reported-success class,
reintroduced by the very commit that fixed it for Kilo. On THAT fix (`43c2034`, job 818) it
found three more, fixed after it: the staged-log probe ignored git's exit status (a failed
`git` read as "clean"), `--exclude-standard` hid a partially ignored shard, and the refusal
had no MCP parity test. On THAT (`8b167e9`, job 819): the printed remedy for an ignored
shard (`git add`) stages nothing, so following it was refused forever — fixed, and the test
now RUNS the printed `git add` lines and commits. **Test a remedy by executing it.** On
THAT (`40950c9`, job 820): git C-quotes non-ASCII paths unless given `-z`, so the new
`git add -f` named no file. Every git path listing in `enforce.py` now uses `-z`. On THAT
(`4f54455`, job 821): `-z` emits RAW bytes, and `text=True` decoded them strictly, so one
non-UTF-8 filename made every commit raise. Read `-z` output as bytes and `os.fsdecode` it
(`W.git_paths`). On THAT (`e8543f9`, job 822): a THIRD caller, `inventory._candidates`,
had the identical bug, and my test of the log-probe decode never reached the probe. All
three now share `infra.worktree.git_paths`; each call site is pinned by its own test.
**Correction to `18cae1a`'s commit message:** it says its full run was on a snapshot
"identical to this tree". Another session landed R16 (`165b84b`) on `main` while that run
was going, so `18cae1a` sits on a base the run never saw. The commit after it was run on
the COMBINED tree. **Before committing, check `git log -1` is still the base you tested.**
On `18cae1a` (job 823): `staged_paths` collapsed git's failure into `[]` — "nothing staged"
— so a damaged index passed the lease and view checks. It now returns None and both refuse.

## 3. What was finished in the previous session

Shipped in `9234cdb`. Three backlog items, all closed in `docs/BACKLOG.md`, all
mutation-verified (31 mutations across the four slices).

**B19 — pickability audit.** `core/schedule.py::unpickable()`, surfaced by `doctor`.
New knob `[schedule] empty_phase` (note | problem | off). Tests: `tests/test_pickability.py`.

> Two hypotheses about how a *task* could become invisible to `ddflow next` were probed and
> **both refuted**: a task parented to a nonexistent phase id is still reachable, because
> `State.descendants()` walks the parent FIELD rather than the items; and a foreign `kind` is
> impossible, because `_h_added` takes the kind from the event kind and ignores `data`. So the
> property already held for tasks, and `test_every_live_task_is_offerable` pins it. The real
> gap was a **phase** with nothing pickable under it.

**B24 + B25 — do our own mechanisms fire?** New module `services/rates.py`.
`gate_rates`/`failing_gates` (a gate that says no to nearly everything) and
`cadence_rates`/`never_fired` (fired versus scheduled, which is EXACT here because cadences
count completions, not wall-clock: `expected = completed // every`). New knobs
`[gates] rate_min_runs`, `[gates] rate_max_fail`, `[cadence] max_missed`. Tests:
`tests/test_rates.py`. Both report as **notes, not problems** — a defect in the machinery
that checks the work must not block the work, and that is itself mutation-tested.

**B20 — inventory ratchets, not count ratchets.** New module `services/inventory.py`,
`Lesson.pattern/globs/sites`, `api.lessons_verify`, CLI `ddflow lesson verify`, MCP
`ddflow_lesson_verify`. Tests: `tests/test_inventory.py`.

> A lesson names the mistake in code; filing it SCANS and stores WHICH sites match; verify
> re-scans and names the ones that reappeared. Exit 1 lists them; **exit 2 (no lesson has a
> pattern) is explicitly not a pass.** A site is `<path>: <matched text>` and deliberately
> NOT `path:line` — line numbers churn on edits above a site and would invent paired
> new/fixed findings from unrelated changes, which is how a ratchet earns the reputation
> that got the original one ignored.

**Plus five roborev findings on `3040d4b`, all CONFIRMED and fixed.** The one that mattered:
`adopt --agents aider` on a repo whose `.aider.conf.yml` held an INLINE list wrote
`read: [CONVENTIONS.md, AGENTS.md]` — correct YAML that both the writer's idempotency guard
and `rules_status` matched only in block form. So `doctor` failed a project that had just been
adopted correctly, and each re-adopt appended again, growing the file without bound. A trailing
comment was worse: `read: [CONVENTIONS.md]  # our docs, AGENTS.md]` is unparseable and took the
operator's own entry with it. Every form now normalises to the block form, so the reader and
writer cannot disagree. Also removed `AgentTarget.rules`, a field written for six agents and
read by nothing, which had already drifted from `NATIVE_RULES`. Tests appended to
`tests/test_unified_rules.py`; 4 mutations verified.

All of it is in `9234cdb`; `git show 9234cdb` has the full reasoning in its message.

---

## 4. What to do next: B16–B26, in order

The operator's instruction was to work **B16–B26**, in six groups, in this order. Groups 1
and 2 are done (§3). **Resume at group 3.**

### Already resolved before you start — do not rebuild these

* **B21 (verification-sandbox integrity) — CLOSED 2026-09-27, and this file was wrong about
  it.** An earlier version of this handoff said it was "fully implemented, merely unmarked".
  A probe refuted that: `PYTHONDONTWRITEBYTECODE=1` stops a gate *writing* a `.pyc` but not
  *reading* a stale one, so a gate executed old code. Fixed with `PYTHONPYCACHEPREFIX`; see
  the B21 entry. The lesson for you: **a claim of "already done" is a claim — probe it.**
* **B15** is closed (`gates.verify` mutation-verifies a gate).

### Group 3 — the doc-integrity trio. B18 done; START at B17.

These three share a "what does this diff touch?" helper; build it once.

* **B18 — CLOSED 2026-09-27.** `enforce.check_views` in the pre-commit hook; knob
  `[enforce] generated_views`; one view map (`views.markdown.VIEWS`). See its backlog entry.
  It did NOT need a "what does this diff touch?" helper — the staged paths were enough — so
  that helper is still unbuilt; B17 is where it earns its keep.
* **B17 — doc-surface sync driven by the diff.** For every identifier/knob/default the diff
  removes or renames, grep the doc globs and fail on stale hits outside the diff.
  *The real instance that was waiting is FIXED (2026-09-27)*: the WRITER was wrong. Kilo reads
  `mcp` (opencode's shape), and a probe against Kilo 7.2.20 showed the `mcpServers` file ddflow
  wrote was silently ignored — so `adopt --agents kilo` never registered anything. The delta
  doc was nearly right (its `timeout: 120` is milliseconds in Kilo). Two claims in one repo
  disagreeing is B17's class exactly; `DOCUMENTED_SHAPES` missed it because it is keyed by
  shape, not by agent. See RESEARCH R15's 2026-09-27 note.
* **B22 — prose-pin coverage before editing an instruction file.** Which sentences in a
  rulebook are pinned by a test? Compressing one without knowing deleted nine rules silently
  on the source project.

### Group 4 — B16

* **B16 — diff-derived test selection plus a full-suite cadence.** A path→test index by grep.
  Keep the cadence **advisory, never blocking** — the entry is explicit, and a targeted sweep
  hid 12 pre-existing failures on the source project.

### Group 5 — B23

* **B23 — stale-rulebook / behind-count gate.** *Partly there*: `infra/worktree.py:200
  behind()` exists and `services/cleanup.py:101` reports it. Missing: the gate. Note the
  asymmetry the entry specifies — **the session hook INFORMS (always exit 0), the commit gate
  BLOCKS**.

### Group 6 — B26, and read this before starting it

* **B26 — test-polluter bisect.** Its own entry says *"only once ddflow owns test execution
  rather than shelling out to a project's own command."* **That precondition is still false**
  — gates shell out to the project's command (`services/gates.py::run_command_gate`). So
  either build the precondition first (large, and a design decision the operator should
  approve) or record B26 as blocked WITH the reason. Do not quietly skip it.

### Also open, outside B16–B26

`B7` dogfooding (**needs operator approval** — adopting ddflow into its own repo installs a
commit hook and writes `AGENTS.md`/`CLAUDE.md`/`.mcp.json`), `B12` partial, `B13` Windows,
`B52`, `B95`, `B109`–`B112` (deliberately deferred design thread), `B114`, `B148`, `B149`,
`B166`–`B169`.

---

## 5. Review stack — run all three, they find disjoint things

This is not ceremony. On this session each reviewer found defects the other two missed.

```sh
# 1. cross-family critic (different pretraining family — the only independent reviewer)
cd /ai/delian/src/run_nemo_run/.claude/worktrees/<a-worktree>
uv run scripts/critic_review.py --dirty --repo /home/delian/src/ddflow \
    -c configs/review_critic.toml --intent "<what the change does>"
#   NOTE: this script lives in the PARENT repo, not in ddflow. Give it --repo.
#   Budget >1500s. A SIGTERM/timeout is exit 143 = UNAVAILABLE, which is NOT a pass —
#   record it as unavailable in the commit body.

# 2. roborev (same family; complements, does not replace the above)
roborev review <sha>        # never HEAD if HEAD is a merge: a merge's diff is empty
roborev list               # it is ASYNC — poll for `done`, then:
roborev show <job-id>

# 3. a subagent rubber-duck, told to attack the specific failure mode
```

Do NOT run `roborev init` or install `agent-hook`.

---

## 6. The traps these sessions actually fell into

Every one of these cost real time. They are the reason for the rules in §1.

1. **A test that passes both ways.** Five of this session's tests were vacuous, each for a
   different reason, all found by mutation:
   - a round trip through `place_server`/`get_server` proved only that *two of my own
     functions agree with each other* — four planted mutations left it green. Fixed by
     asserting the **literal documented JSON** (`DOCUMENTED_SHAPES`) as an external contract.
   - a knob test that covered only the cache's *write* side.
   - a git test warmed on the longer branch, so a shrink check caught the mutation for an
     unrelated reason; then warmed on a branch that had merely *appended*, making one file a
     literal prefix of the other so even broken code read correctly. Only **divergent**
     branches expose it.
   - two detectors that could not be shown to work because the tree held no instance to
     miss. Both now take **planted input**.
2. **A substring assertion is not a semantic one.** An Aider test passed while the writer
   appended a *second* `read:` key — both names present in the text, while YAML resolves
   duplicate keys to the last, so the operator's entry was present and gone from the parsed
   config. Count the keys and parse the value.
3. **"X changes the inode" was a claim, not a fact.** A cache validated on `st_ino` because
   "git merge writes a temp and renames". Git rewrites tracked files **in place** — verified
   with `stat`. Check filesystem and tool behaviour before building on it.
4. **Widening an `except` converts a loud failure into a silent one.** `FileNotFoundError` →
   `OSError` made an unreadable shard drop a whole agent's events while reporting success
   with `skipped_lines == 0`.
5. **A search-result snippet is a pointer, never a source.** Secondary write-ups say Kimi
   Code reads Claude's `.mcp.json`; its official docs say `.kimi-code/mcp.json`. Trusting the
   snippet would have written the wrong file and reported success. **Fetch the primary doc.**

6. **Mutation testing is itself exposed to B21's hazard.** A mutant that swaps two lines
   keeps the file's size; restored within the same second, it keeps its mtime too, and
   CPython goes on running the MUTANT's cached bytecode. A test then "fails" on correct
   code — or, the other way round, a mutant is never loaded and "survives". Run every
   mutation check with `PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX=$(mktemp -d)`.
   This cost real time on 2026-09-27.

And one that is structural rather than a mistake: **evidence already in the repository beats a
new probe.** The pointer-stub design for B170 was refuted by a sentence already in
`templates/drivers/deltas/kilo-cline.md` — *"a link is only followed if the agent chooses to
follow it."* Grep before designing.

---

## 7. Things that will bite you mechanically

* **`-m ""`** (§1). Eleven runs in this session read "21 deselected" before anyone noticed.
* **The README has ratchets pointing at it.** `test_the_readme_knob_counts_match_the_config`
  pins the knob count (currently **75 across 15 sections**) and
  `test_the_readme_names_each_agents_real_config_path` pins every agent's config path. Add a
  knob or an agent and the README must change in the same commit.
* **`test_every_supported_agent_is_named_where_a_user_would_look`** requires every agent in
  the `--agents` help, the `ddflow_setup` MCP spec AND the README. The first two are now
  GENERATED from the registry; do not re-hardcode them.
* **Adding one MCP tool means satisfying FIVE separate ratchets.** Adding
  `ddflow_lesson_verify` tripped every one of these in turn, so budget for it:
  1. `test_every_cli_SUBCOMMAND_is_reachable_over_mcp` — the tool name must match the CLI
     verb (`lesson verify` → `ddflow_lesson_verify`, **not** `ddflow_lessons_verify`).
  2. `test_every_tool_is_explicitly_json_or_explicitly_prose` — add it to `PROSE_TOOLS`
     (`tests/test_mcp_parity.py`) with a reason, or make it return JSON.
  3. `test_every_typed_tool_has_a_wire_shape_row` — add a row to `MIGRATED_WIRE_SHAPES`
     (`tests/test_api_layer.py`) giving the equivalent CLI argv.
  4. `TEXT_BODIED` in the same file, if it returns prose — otherwise the parity test tries
     to parse the document as JSON.
  5. Every `Outcome` branch must carry the key named by the tool's `payload`. The exit-2
     branch of `lessons_verify` omitted `text` and raised `KeyError` out of `Outcome.body`;
     nothing else called that branch, so only the wire-shape test found it.
* **Adding a field to a command means editing three places** — argparse, the MCP input schema
  and the event payload. `api/decisions.py::Draft` documents this and the answer: a named
  record. `api/knowledge.py::LessonDraft` is the second instance.
* `scripts/critic_review.py` is in the **parent** repo (`run_nemo_run`), not here.
* The suite takes ~24 minutes with `-m ""`. Run it in the background and do something else.

---

## 8. Open question for the operator

**B26's precondition** (§4, group 6): building it means ddflow owns test execution instead of
shelling out to the project's command. That is a significant architectural change and should
be an explicit decision, not an inference from a backlog entry.

**B7 (dogfooding)** likewise: it changes this repo's own workflow.
