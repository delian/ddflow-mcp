# Handoff — where this work stopped and how to resume it

Written 2026-09-27 at the end of a long session, for an agent starting with **no context**.
Read this, then `docs/BACKLOG.md` for the item you pick. Everything here is verifiable from
the repository; nothing depends on remembering the conversation.

---

## 1. Read these first, in this order

| # | File | Why |
|---|---|---|
| 1 | `AGENTS.md` (and `CLAUDE.md`) | the managed work-queue block: claim before you edit |
| 2 | `docs/BACKLOG.md` | 170 entries, with `✅ CLOSED` markers. The **top** section is a 2026-09-27 audit |
| 3 | `docs/RESEARCH.md` | R15 is the agent-config research; §R-log covers the event log |
| 4 | `docs/ARCHITECTURE.md` | the layering the AST test enforces |
| 5 | `README.md` | the user-facing surface. It has ratchets pointing at it — see §4 |

**The house rules that are not negotiable** (they are in `CLAUDE.md`, and every one of them
caught a real defect during this session — see §6):

* **No source change without a runnable probe that fails before the fix and passes after.**
  Then MUTATION-VERIFY it: revert the fix, watch the test go red, restore. A test that
  passes both ways proves nothing, and is the single commonest failure here.
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
HEAD:   9234cdb       "B19 + B20 + B24 + B25: ddflow checks its own machinery"
pushed: yes — origin/main == 9234cdb
tree:   CLEAN — nothing uncommitted, nothing in flight
```

**Verified green at that commit**, and worth re-running before you trust it:

```sh
uv run ruff check . && uv run ruff format --check .
uv run pytest tests/ -q -m ""        # ~27 min. Expect 1280 passed, 1 skipped.
uv run python demos/run_all.py       # ~2.5 min. Expect 6/6, 219 assertions.
```

If any of that is red, the failure is **new information** — read it before assuming this
document is wrong. Nothing was left half-finished, so a red suite means something changed
after 9234cdb.

`roborev review 9234cdb` (job 816) found **five findings, all CONFIRMED, all fixed** in the
commit after it — including a real defect in the B25 work: `expected = completed // every`
assumed every run happened at its scheduled point, so a cadence that fired early and then
stopped had `ran > expected`, the shortfall floored to zero, and `doctor` stayed silent while
`ddflow cadence` called the pass DUE. The measure is now `since` (completions since the last
run), the same quantity due-ness uses. **Queue roborev on whatever you commit** — every run
on this series found something real, including one bug that reached a pushed commit.

---

## 3. What was just finished (uncommitted)

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

* **B21 (verification-sandbox integrity) is already fully implemented.** Verified against the
  source: `tree_sha` in gate evidence (`services/gates.py`), `PYTHONDONTWRITEBYTECODE=1` in
  the gate env, and the verdict taken from `returncode` — never parsed from stdout. Its entry
  was simply never marked. **Mark it closed and move on.**
* **B15** is closed (`gates.verify` mutation-verifies a gate).

### Group 3 — the doc-integrity trio. START HERE.

These three share a "what does this diff touch?" helper; build it once.

* **B18 — regenerate-and-diff guard for generated files.** *Partly there already*:
  `views/markdown.py:383` regenerates every view and its docstring CLAIMS "same state in,
  same bytes out". Nothing proves it. Build the check that regenerates and asserts
  byte-identical output, and wire it into the commit gate. This is B18's natural first case.
* **B17 — doc-surface sync driven by the diff.** For every identifier/knob/default the diff
  removes or renames, grep the doc globs and fail on stale hits outside the diff.
  **A real instance is waiting for you**: `templates/drivers/deltas/kilo-cline.md` documents
  Kilo's MCP entry as `{"type": "local", "command": [...]}` while `_register_mcp` writes
  `{"command": ..., "args": [...]}` for `SHAPE_MCP_SERVERS`. One of the two is wrong — check
  Kilo's own docs (R15's method), fix whichever, and let B17 catch the class.
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
uv run scripts/critic_review.py --dirty --repo /ai/delian/src/run_nemo_run/ddflow \
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

## 6. The five traps this session actually fell into

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

And one that is structural rather than a mistake: **evidence already in the repository beats a
new probe.** The pointer-stub design for B170 was refuted by a sentence already in
`templates/drivers/deltas/kilo-cline.md` — *"a link is only followed if the agent chooses to
follow it."* Grep before designing.

---

## 7. Things that will bite you mechanically

* **`-m ""`** (§1). Eleven runs in this session read "21 deselected" before anyone noticed.
* **The README has ratchets pointing at it.** `test_the_readme_knob_counts_match_the_config`
  pins the knob count (currently **74 across 15 sections**) and
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
