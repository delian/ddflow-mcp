# Backlog — findings accepted but not fixed in this pass

Each entry is a real finding from the review stack that was judged out of scope for the
initial build. None is a silent TODO in the source: they are all here, with the analysis
that produced them, so the next session does not rediscover them.

In an adopted project these would be Orchard tasks (`orchard task add ...`). They live in
markdown here because Orchard is not yet dogfooding itself — which is itself the first
item below.

## Structural

- **B1 — ~~`fold` is a 150-line `if/elif` ladder~~ — DONE.** Replaced by
  `model.HANDLERS: dict[str, Callable]`, and `events._kinds()` now *derives* the
  vocabulary from it rather than declaring a second copy. A kind that nothing
  interprets can no longer exist. *Found by: roborev architecture (C4). Retired in the
  same session; 104 tests passed unchanged across the refactor, which is the evidence
  it was behaviour-preserving.*

- **B2 — Lifecycle classification is split across `schedule` and `lease`.** `lease.scan`
  treats "RUNNING with no lease" as `stale_running` needing recovery; `plan()` hands the
  same item to another agent as ready, with no mention that someone was mid-flight. Both
  behaviours are defensible; deciding them in two modules that never consult each other
  is not. *Found by: roborev architecture (C6).*

- **B3 — The live-lease dict is computed three times** (`State.active_leases`, inline in
  `plan`, and formerly in `_alternatives`). Route all of them through
  `State.active_leases`. *Found by: roborev architecture (C6).*

## Performance

- **B4 — The coordination path holds the write lock across an O(all-events) read.**
  `lease.acquire` does `read_all()` + `fold()` *inside* the flock. Folding is cheap
  (407k events/s measured) but the I/O is not, and on the NFS mount this was designed
  against that read is ~36× the local cost. Move the read before the lock and
  re-validate only the affected item inside it. *Found by: roborev architecture (C9).
  Not urgent: measured contention is far below the point where it matters.*

- **B5 — `Store.stale()` reads everything to decide whether to read everything.** It
  calls `log.read_all()` to compare event counts. `EventLog._highest_lamport()` already
  demonstrates the cheap pattern — a per-shard tail read, O(shards). Add
  `EventLog.head() -> (shards, highest_lamport, total_bytes)` and compare that instead.
  *Found by: roborev architecture (C9).*

## Declared but unimplemented

- **B6 — `log.compacted` is vocabulary without a mechanism.** The event kind exists,
  `PROVENANCE_KINDS` documents what compaction must preserve, and `fold` handles the
  marker — but nothing implements it. Either implement (rewrite shards keeping every
  `PROVENANCE_KINDS` event plus the last state-bearing event per subject, then append
  the marker) or remove the kind. Declaring a retention policy the system cannot execute
  is worse than declaring none. *Found by: roborev architecture (C10). Not urgent: at
  407k events/s the growth ceiling is far away.*

- **B7 — Orchard does not dogfood itself.** This backlog should be an Orchard queue, and
  this project's own development should run through its own gates. The reason it does
  not yet is bootstrapping order, not principle.

## Mechanical cleanups

- **B8 — `renew` / `release` / `expire` share ~85% of one body** (`lease.py`); they
  differ only in a guard and a payload. *Found by: roborev duplication (D5).*
- **B9 — `"no such item" → stderr → FAIL` appears 5× in `cli.py`**, and the same
  condition is phrased two further ways in `gates.py` and `lease.py`. *(D6)*
- **B10 — `phase add` and `task add` argparse blocks are re-typed**, though the
  `record`/`skip` pair right below them is already loop-generated. *(D7)*
- **B11 — `cli._csv` and `config._coerce`'s list branch are the same expression.** *(D9)*

## Coverage gaps

- **B12 — Cross-family review: PARTIALLY RETIRED.** A local
  `Qwen/Qwen3.8-Flash-Next-FP8` (Alibaba family) was found on vLLM at
  `127.0.0.1:8000` and is now wired in as a first-class reviewer — see
  [R8](RESEARCH.md). The LAN endpoint originally configured remains unreachable. The
  review of the *core package* with the corrected reasoning-model settings is the
  remaining work; until it completes and its findings are triaged, treat this as open.
- **B13 — Windows is untested.** `fcntl.flock` is POSIX-only; `events.py` would need an
  `msvcrt.locking` branch. The rest is portable. No probe was run, so this is
  `THEORETICAL`.
- **B14 — Multi-machine clock skew is reasoned about but unprobed.** The Lamport
  ordering is designed so wall-clock skew cannot reorder history, and lease expiry has a
  `grace_s` knob for it — but no two-machine probe was run. `THEORETICAL`.


## From mining this project's source repository's lessons corpus (2026-09-24)

A subagent read `docs/lessons-summary.md`, the 25 newest `docs/lessons.md` entries and
the standing rulebooks of the project Orchard was extracted from, looking for
capabilities born of repeated failure. Four were implemented immediately (they are gone
from this list); these are the rest, ranked as it ranked them.

- **B15 — `orchard gate verify <gate>`: mutation-verify that a gate CAN fail.** Orchard
  has ten gates and nothing proves any of them is capable of going red. A gate declares
  `mutations = [{file, old, new}]`; the runner asserts `src.count(old) == 1` *before*
  patching (a mutation that did not apply is not a passed mutation test — the suite goes
  green and reads as "the check cannot detect this", the opposite conclusion), requires
  a non-zero exit, restores. A gate with zero registered mutations is itself a failure.
  *This is the highest-value item on the list: it is the anti-vacuous-pass check applied
  to Orchard's own checks.*

- **B16 — diff-derived test selection plus a full-suite cadence.** "Choosing which tests
  to run by reasoning about the change is guessing — derive it." And targeted sweeps hide
  standing breakage: on the source project a full run found 12 pre-existing failures
  invisible to every targeted gate. Build a path→test index by grep; keep the cadence
  advisory, never blocking.

- **B17 — doc-surface sync check driven by the diff.** For every identifier, knob or
  default the diff removes or renames, grep the doc globs and fail on stale hits outside
  the diff. The rule exists in prose in most projects and is therefore skipped; a stale
  page is worse than a missing one, because a reader who finds nothing reads the code
  while a reader who finds a wrong default trusts it.

- **B18 — regenerate-and-diff guard for generated files.** Any file marked generated is
  regenerated in pre-commit and must come back byte-identical. Orchard's own
  `docs/orchard/*.md` views have exactly this hazard.

- **B19 — pickability audit.** After filing a task, assert `orchard next` can actually
  offer it. On the source project 37 follow-ups — including four confirmed reviewer
  findings — were filed where the picker could not see them, and nothing failed: the
  counts reconciled and the audits exited 0.

- **B20 — inventory ratchets, not count ratchets.** When a lesson is filed, require a
  scan and store *which sites*, not *how many*. A count-based clone ratchet on the source
  project sat red for ~350 commits: advisory, so it never blocked, and every reader
  learned to skip it — 24 new clones arrived through that gap. "A count says 'worse' and
  never 'which'."

- **B21 — verification-sandbox integrity.** Three real hazards for a system that runs
  parallel agents in worktrees: a patch can forge a `PASSED` line on stdout; same-second
  same-size edits leave valid-looking stale `.pyc` so the post-patch run re-executes the
  old code; and a probe is evidence about the tree it ran on, which a concurrent agent
  can move underneath it. Remedy: gate results carry a `tree_sha`, the runner sets
  `PYTHONDONTWRITEBYTECODE=1`, and verdicts are never parsed from uncaptured stdout.

- **B22 — prose-pin coverage before editing an instruction file.** Compressing a
  rulebook without checking which sentences are pinned by tests deletes rules silently;
  one by-eye pass on the source project broke nine pins, because "every sentence picked
  read like rationale and was a rule". Orchard ships a driver and a rulebook block.

- **B23 — stale-rulebook / behind-count gate for long-lived branches.** Rulebook edits
  landing on the default branch are silently ignored by sessions that loaded them at
  start, and drift compounds: a 98-commits-behind branch had to be hand-ported. Orchard
  creates long-lived worktrees and has no staleness half. Note the asymmetry the source
  project settled on: the session hook *informs* (always exit 0), the commit gate
  *blocks*.

- **B24 — per-gate fire-rate tracking.** "A gate that fails on everything is worse than
  no gate: it trains the next reader to skip it." Orchard has the event log to compute
  this from; a gate firing on ~100% of changes should be auto-flagged for repair.

- **B25 — cadence fired-vs-scheduled counter.** "The mechanism you did not measure is the
  one that is not running" — on the source project three wakeups were scheduled and zero
  ever fired, producing a 12-hour stall while an undesignated mechanism did the work.

- **B26 — test-polluter bisect.** Delta-bisect the test file that makes another fail only
  in full-suite order. Generic and high-value, but only once Orchard owns test execution
  rather than shelling out to a project's own command.

## B27–B34 — from the 2026-09-24 review pass

Filed rather than fixed, each with why it is not urgent.

- **B27. `critical_path` ignores inherited dependencies.** Readiness now consults an
  item's ancestors (`schedule.inherited_deps`), but the critical-path calculation still
  walks `needs` alone, so a phase-level dependency does not lengthen the reported path.
  The number is advisory — it sets an expectation, not a decision — and it is currently
  *shorter* than the truth, which is the harmless direction for a figure nobody gates on.
  *Found by: writing the inheritance fix.*

- **B28. The parallelism cap counts leases, not worktrees. ✅ CLOSED.** The two knobs
  were combined with `min()`, which is one number pretending to be one statement:
  `schedule.max_parallel_tasks` says how many items may be IN FLIGHT, `worktree.
  max_parallel` says how many trees may EXIST. Under the old form a machine allowed one
  tree could not run a second `--no-worktree` task, and a queue allowed four in flight
  silently became one — the silent-knob-drop class. Now two independent checks, both
  counted across the whole queue, each naming itself when it is the one that refused.
  `tests/test_rubber_duck_findings.py` mutation-verified.

- **B29. Companion detection has no cache.** `orchard companions` probes on every call,
  and an `npx`-based probe can take seconds on a cold cache. `adopt` pays this once, and
  `--no-probe` exists, but a session-start hook that called it would feel it. A cached
  result with a short TTL in the gitignored index would fix it. *Found by: the first
  `adopt` run after companions landed.*

- **B30. `gates.enforce_order` defaults to "warn" and nothing measures how often it
  fires.** If the warning is routine it is noise and the default should move to "off"
  for that project; if it is rare it should probably be "block". Neither can be argued
  without a fire-rate, which is the same gap B22 names for gates generally.

- **B31. No mutation test for the inherited-dependency rule at the CLI level.** The unit
  tests mutation-verify `inherited_deps` and `plan_blocker`. The scenario asserts the
  behaviour end to end but is not itself mutated, so a regression that only manifests
  through the MCP path would be caught by the scenario failing rather than by a
  demonstration that it *can* fail.

- **B32. `companions.is_installed` is two-valued.** A probe that times out is reported
  as not-installed with the timeout in the detail, which reads correctly to a human but
  collapses "absent" and "could not tell" for any caller reading the boolean. `lease`
  already solved this shape with a three-valued `salvageable: bool | None`; this should
  follow it rather than invent a second convention.

- **B33. The `full-lifecycle` scenario is not run by `pytest`.** Neither is any other
  scenario — `demos/` is invoked separately and is not in the publish workflow. The
  scenarios have found most of the real bugs in this project, so the one suite that
  matters most is the one CI does not run. (Ceremony note: they take ~2 minutes and
  spawn processes, so they want their own marker and job, not inclusion in `tests/`.)

- **B34. `orchard history` does not exist as one view. ✅ CLOSED.** One
  reverse-chronological timeline over the log, filterable by `--item`, `--kind`
  (families or exact kinds), `--since` and `--limit`, on both surfaces. The MCP tool was
  missing on the first pass and `tests/test_mcp_parity.py` caught it, which is what that
  ratchet is for.

## B35–B40 — from roborev's architecture pass, 2026-09-24

Structural debt rather than defects. Filed with what it costs today, because the point
of recording it is to stop the next reading re-deriving it.

- **B35. There is no application layer; the protocol adapter depends on the
  presentation layer.** `mcp_server → cli` is the only edge and it is carried by
  **strings**: typed MCP arguments are flattened to argv, re-parsed by argparse, and the
  result is recovered by scraping stdout plus an exit code. The costs are already
  visible in the code — `_opt(..., clearable=True)` exists only to re-create the
  "absent vs empty" distinction argv erased, and `_run_cli` swaps process-global
  `sys.stdout`/`sys.stderr` for each call, which is not reentrant and forecloses
  concurrency. Parity is held by ratchets where types would hold it structurally, and
  those ratchets catch a *missing* flag, not a *changed* encoding. The fix is a real
  application layer (`orchard/api.py`) that both surfaces call; it is a large change
  and the ratchets make the current shape safe, so it waits for a reason rather than a
  free afternoon.

  **`services/outcome.py` was the groundwork and shipped DEAD** — 91 lines, zero
  importers, committed in 89386614 and never wired into any of the ~60 command
  functions. Deleted 2026-09-24 rather than left in place: an unreferenced module that
  ships in the package is worse than a backlog entry, because it is importable,
  untested and reads as live API to the next person. `git show 89386614:orchard/orchard/services/outcome.py`
  has it when B35 is actually done. *Found by: reading the change surface before
  committing.*

- **B36. `cli.py` is a god module — 3,041 lines.** ~60 command functions, a 430-line
  parser builder, an embedded starter-TOML string, and real **domain policy**:
  `cmd_complete` IS the completion rule-set (required gates, open descendants,
  `require_outcome`, inert requirements, reviewer independence) and is reachable only
  through `main(argv)`. Those rules deserve to be callable and testable without argv.
  Same for `cmd_claim`'s loop refusal. Subsumed by B35 if B35 happens.

- **B37. Dual presentation per command, hand-kept in sync.** `render.py` is the
  presentation layer, yet `cmd_status` builds its JSON and its human view inline and
  independently: 39 `c.out(...)`, 20 hand-rolled `if c.json` branches, 25 `json.dumps`,
  204 bare `print(`. This package has already shipped the bug that produces — twice in
  one function, and the comment is still there: *"it used to print only in human mode,
  so an agent driving over MCP was never told that a gate had not run."*

- **B38. Latent circular dependency `cli ↔ mcp_server`,** held apart only by a
  function-local import. Hoisting it to module level reproduces:
  `ImportError: cannot import name 'main' from partially initialized module
  'orchard.cli'`. Harmless today; it is a tell for B35.

- **B39. `State` has no parent index, so `children()` is a full scan** and
  `descendants()`/`ancestors()`/`_is_umbrella()` call it per node per candidate.
  Measured (1 phase + N tasks): `n=100 → 2.0 ms`, `n=400 → 18.8 ms`, `n=800 → 46.1 ms`;
  cProfile at n=800 attributes 74% of `plan()` to 1600 calls into `children`. Roughly
  quadratic, harmless at realistic sizes, and a cheap fix (build `parent → [child]`
  once per fold). Related: every call refolds the whole log.

- **B40. TRUNCATED-completion diagnostics are written three times** in `reviewer.py`
  (openai, anthropic, gemini), and have already drifted: the openai copy names
  `max_chunk_chars` and reports reasoning-token counts, the gemini copy names neither.
  Same class as the four duplications fixed in this pass, just lower blast radius —
  it degrades a message rather than a decision. *Found by: roborev duplication (D6).*

## B41–B51 — the importer against a real 400-day corpus, 2026-09-24 ✅ ALL CLOSED

Eleven defects, none of which any fixture test could see. Full measurements, before and
after, in [docs/RESEARCH.md §R11](RESEARCH.md#r11--the-importer-against-a-real-400-day-corpus-not-a-fixture);
all eleven ship with a mutation-verified regression test in
`tests/test_import_real_project.py`.

- **B41. A numeric-dotted id was not an id.** `### 142.A` — the shape this project has
  used for two hundred phases — was slugged, because the pattern required a leading
  letter. Broke 39 of 47 declared dependencies at once.
- **B42. An id inside a spanning bold was not an id.** `**DRIVERFIX.1 — step 1 …**`
  matched neither the delimited nor the bare form.
- **B43. The child-prefix rename overruled a declared heading id**, renaming the phase
  out from under every `Needs:` pointing at it.
- **B44. Derived task ids voted in that rename**, which is circular, and one of them was
  enough to empty the common prefix.
- **B45. 42 pairs of ids collided** — silent data loss, because the second event folds
  over the first.
- **B46. One `##` research entry became five**, its `###` sub-parts promoted to siblings.
- **B47. `docs/adr/README.md` imported as a decision.**
- **B48. Every journal entry was dated the day of the import.**
- **B49. The fold dropped `seq`, `ident` and `source` from notes**, so the idempotency
  check compared `""` with `""` — a projection silently deciding a field does not exist.
- **B50. Every memory printed twice**, its excerpt concatenated with the line it was an
  excerpt of.
- **B51. Over the cap, 790 phases were proposed with zero tasks**, and the human preview
  listed five of the eight kinds.

Two things the scan now REPORTS and deliberately does not resolve: a phase heading that
says `SHIPPED` over unticked boxes (32 of them, corroborated by the same repository's
own OptMem store), and a dependency on an id nothing produced.

## B52 — the worktree cap cannot be applied per item

`plan()` now counts the two caps separately — `schedule.max_parallel_tasks` over every
live lease, `worktree.max_parallel` over the leases that actually made a tree — but it
still emits ONE `slots` number for all ready items, so a full tree cap withholds a task
that would have taken no tree.

It cannot do better today: `--no-worktree` is a flag on `claim`, not a field on `Item`,
so at planning time nothing distinguishes the two. The fix is an item-level declaration
(`needs_worktree`, or deriving it from a tag or the absence of globs) — a model change
that wants a reason rather than a free afternoon, and the current form is a strict
relaxation of what it replaced, so nothing regressed.

*Found by: the cross-family critic, 2026-09-24, raised as THEORETICAL. Tier-0 probe —
`[f.name for f in fields(Item)]` — confirmed there is no such field, which is what keeps
it a granularity gap rather than a counting bug.*

## B53–B56 — the 2026-09-24 review stack on the import work

Three reviewers, disjoint findings, which is the whole reason all three are run.

- **B53. `orchard merge` acted on an item REMOVED from the queue. ✅ CLOSED.** It was
  the one mutating command not routed through `_require_item`. Removal is a FLAG on an
  item that still folds, so `st.items.get()` found it and only the flag said it was
  gone — and `orchard merge T1` landed the branch of work the operator had explicitly
  dropped, printing `merged T1 (0638b5c6) into main`. Probe first, fix second; the
  probe ships as `tests/test_roborev_findings.py::test_merge_refuses_an_item_that_was_removed_from_the_queue`
  and was mutation-verified. Swept the class: three other `st.items.get()` sites in
  `surfaces/` are read-only helpers that tolerate a missing item, so this was the only
  one. *Found by: `roborev analyze duplication`, as a side-effect of the duplication it
  was actually asked about.*

- **B54. `EventLog.head()` read its two components in two separate walks. ✅ CLOSED.**
  Size from one pass over the shards, Lamport high-water mark from another — so an
  append landing between them, carrying a clock value at or below the current high,
  was invisible to BOTH: the size walk had already read that shard, the tail walk saw
  no higher clock, and the fingerprint came back byte-identical to the pre-append one.
  That is precisely the interleaving the byte count was added to catch, defeated by the
  read order. Now one pass, size and tail adjacent per shard. Two regression tests, one
  structural (`head()` walks the shard set exactly once) and one behavioural (a second
  agent's shard with a LOWER clock still moves the fingerprint). *Found by: the
  cross-family critic, raised THEORETICAL.*

- **B55. `State._child_index` is cached with no invalidation. ✅ CLOSED as REFUTED,
  with a ratchet.** The critic's reading was right about the shape — a `children()`
  call during the fold would freeze the index against a partial item set, and
  `descendants()`/`plan()` would silently return a smaller queue with nothing raised.
  An AST probe over all 23 `_h_*` handlers found zero index-consuming calls, so it is
  not reachable today. The probe ships as
  `tests/test_layering.py::test_no_fold_handler_reads_the_child_index`, because the
  invariant was held only by a docstring and a docstring has never once stopped a
  handler being added.

- **B56. The three `##`-section scanners were three copies of one loop. ✅ CLOSED.**
  Collapsed into `_scan_sections(repo, globs, kind, ident, extra)`; the three public
  scanners are now three lines each. Verified output-identical against the real
  400-day corpus before and after (same 442/72/1727 counts, same zero duplicate ids,
  same zero unresolvable dependencies). *Found by: `roborev analyze duplication` (C1).*

- **B57. The index fingerprint described a log NEWER than the projection. ✅ CLOSED.**
  `rebuild()` read the events, projected them, and only then took `log.head()` — so an
  append landing in that window was in the fingerprint and not in the index. The next
  `stale()` compared equal, returned False, and those events were never projected;
  `recall` omits them, and permanently if the log then stops growing, because only a
  later append would move the fingerprint again. The stored comment argued the opposite
  ("written from the SAME cheap read it will use"), which was true and beside the point:
  the disagreement it removed WAS the signal that the projection was behind. Fingerprint
  now taken first, so it can only under-report — at worst one unnecessary rebuild.
  Probe injects the append at the exact point the window opens
  (`tests/test_critic_findings.py::test_a_rebuild_that_races_an_append_reports_itself_stale`),
  mutation-verified. *Found by: the cross-family critic, raised THEORETICAL.*

- **B58. Does an existing schema-5 index gain the new `role` column? ✅ CLOSED as
  REFUTED.** `rebuild()` unlinks and re-creates a temp database, so `init()` always runs
  against an empty file and every column exists; `stale()` refuses any `schema != 6`
  outright. There is no migration path because there is never anything to migrate — the
  design note at `store.py:13` says exactly this. Raised by the critic as "the one thing
  I would want a human to confirm", which was the right call and cost one read.

- **B59. `gate verify` certified an already-red gate. ✅ CLOSED.** The anti-vacuous-pass
  check, being vacuous. A gate that exits non-zero on the UNMUTATED source — one
  pre-existing failing test, a tool that stopped being installed, a flake — reports
  `failed` for every mutation, so every `detected` is True and `verify` reported "this
  gate CAN fail". It could not; it was red before anything was touched. Now a green
  baseline is run first and its absence is a named failure. Probe first, fix second
  (`test_a_gate_that_is_ALREADY_red_does_not_count_as_detecting_anything`), mutation-
  verified on both the human and JSON surfaces. *Found by: the cross-family critic —
  its only CONFIRMED finding, and the most valuable one in the pass.*

- **B60. `verify`'s two failure channels made a pre-flight failure look like a pass.
  ✅ CLOSED.** Every pre-flight refusal returns `([], reason)`, and `all([])` is True —
  so a caller scoring on `results` alone reads "unknown gate" and "no registered
  mutations" as verified. The CLI now applies all three clauses
  (`bool(results) and not reason and all(r.ok ...)`) and the contract says so in the
  docstring. *Found by: the cross-family critic, THEORETICAL.*

- **B61. A stale worktree path silently mutated the PRIMARY checkout. ✅ CLOSED.**
  `cwd = W.load_path(repo, item.worktree) or repo` — so an item recording a worktree
  that no longer resolves had the mutation written into the real repository working
  tree, and the verdict then described the wrong tree entirely. Interrupted between the
  write and the `finally`, it leaves the primary checkout mutated. Now refused and
  named. *Found by: the cross-family critic, THEORETICAL.*

- **B62–B64. Three more from the critic's last chunk, all in the importer itself, all
  probed CONFIRMED and fixed. ✅ CLOSED.**
  - The unresolvable-dependency note checked against `plan.found` alone, which by
    construction excludes everything already in the queue — so the SECOND run of an
    incremental import reported every dependency on a first-run item as missing, with a
    consequence sentence that is false (the fold resolves against the folded queue, not
    against one scan's output). It sent the operator to fix something that was not
    broken, on the exact path the module exists for.
  - `**Globs:**` / `**Needs:**` attached to `found[-1]`, and `found` is never reset
    between files — so an annotation at the top of `b.md` landed on the last task of
    `a.md`, invisibly, because that task's `source` still points at its own line. Now
    anchored to a per-file, per-heading pointer.
  - `_parse_needs` stripped `_` as markdown decoration while `_is_id` admits it, so a
    project using `TASK_1` recorded a dependency on `TASK1` — permanently blocked on a
    token appearing nowhere in the project. The two halves of one grammar now agree.

  A fourth finding in the same chunk (branch idents bypassing `_unique`) was already
  fixed before the critic reported it; it reviewed the earlier diff.

The rest of roborev's consolidation list (C2–C10: table-spec-driven inserts, an
`applicable_decisions` helper, `Ctx.emit`, a `_kept` merge helper, module-level imports)
is pre-existing structural debt in files this change did not own. It is subsumed by B35
and deliberately not started here: ~215 lines across five modules is a refactor that
wants its own commit and its own review, not a tail-end of an import change.

## B65–B68 — roborev 772, on the import commit itself ✅ ALL CLOSED

Reviewed `6721d6f4` after it landed, as the cadence requires. Four findings, three of
them defects, all probed before a line changed and all mutation-verified. Follow-up
commit, per §roborev.

- **B65 (the substantive one). Re-importing after a new journal entry silently
  overwrote the earlier ones in the recall index.** Journal and memory entries land as
  notes in two fixed sessions, numbered by a fresh `enumerate` on every run;
  `_h_session_started` MERGES rather than replaces, so a second import appended notes
  0,1,2 beside the first run's 0,1,2. The index keys on `(session, 10_000 + seq)`, so
  each new note replaced an earlier one — and `prompts_fts` then held two rows under one
  doc id, so a query matching the OLD text resolved to the surviving row and returned
  text that did not contain the query terms. This is the incremental path the module
  advertises. Fixed where the number should always have come from: `_h_session_note`
  now assigns `seq = len(sess.notes)`, exactly as `_h_session_prompt` already did, and
  the caller's value is ignored.

- **B66. `id_from_source` was true even when `_unique` REJECTED the declared id.** Two
  files carrying the same `**142.1**` — the case `_unique` exists for — produced a
  derived slug recorded as source-read, which then voted in `_adopt_child_prefix`: the
  circular vote that function's docstring forbids. It disagrees in the first component,
  the common prefix empties, and the phase silently keeps its prose slug. The phase
  branch already guarded this; the task branch did not.

- **B67. `critical_path`'s cycle guard walked a different graph from `longest()`.** The
  memo is unsound on a cyclic graph and the guard exists to refuse rather than return a
  wrong number — but it read direct `needs` while `longest()` now walks INHERITED ones,
  so a cycle existing only in the inherited graph passed straight through. `find_cycles`
  now takes an `edges` callable and the caller passes the graph it will traverse.

- **B68. Docstring drift in the function that changed.** `stale()` still claimed to
  compare "(schema, event count, last lamport)" after it stopped reading the event
  count at all. A reader debugging a missing rebuild would look for a mismatch the code
  does not check.

## B69–B72 — the import becomes verifiable, and the tool can explain itself ✅ ALL CLOSED

Operator ask, 2026-09-25: can the bootstrap get the agent to run the import *and check
it*, can that be re-run at any time, can an operator verify what was imported, and can
anyone ask the tool what it does?

- **B69. Imported items were not machine-identifiable. ✅ CLOSED.** Provenance for
  phases, tasks and branches was PROSE in `body` — `"Imported from docs/todo.md:41."` —
  and `Item` had no source field, so "which items came from the import" could only be
  answered by regexing a sentence. That is the metadata-key-vs-field class: reword the
  sentence and the count silently becomes zero while the verification passes. `Item`
  now carries `source`, the body prose stays for `orchard show`, and `item.completed`'s
  `evidence` — written by the importer since day one and DROPPED by the fold, the third
  instance of that bug in this series — lands on `Item.completion_evidence`.
  `ResearchNote` gained `tags` so `imported` is spelled the same way on all three memory
  kinds; telling an imported note from a hand-written one by the SHAPE of its `sources`
  would have been a heuristic pretending to be a fact.

- **B70. `orchard import --verify` / `orchard_import_verify`. ✅ CLOSED.** Status (what
  is imported, per kind, and when), still-true (drift since, sources that yielded
  nothing, source files that have vanished) and finished (tasks with no globs, phases
  claiming SHIPPED over an open task). Exit 0/1/2 because there are three answers.
  Deliberately does not repeat `doctor`'s unresolved dependencies, duplicate globs or
  cycles, and says so. `--verify` with `--apply` is REFUSED rather than silently
  resolved: one reads and one writes.

- **B71. The handshake follows through, and the prompt re-runs. ✅ CLOSED.** The offer
  to import stopped the moment the queue had one item in it, so an import that landed
  1,170 tasks and stopped there was never mentioned again. The handshake now reports
  unfinished imported work — computed from the already-folded queue, so the ~0.65 s
  source scan stays out of every session start — and tells the agent to run
  `orchard_import_verify` before handing any of it out.
  `/import-existing-project` opens by checking what is already imported and branches to
  finishing-and-refreshing. Same name: renaming breaks anyone invoking it, and a second
  near-identical prompt is two documents that drift.

- **B72. `orchard help` / `orchard_help`. ✅ CLOSED.** There was no help surface on MCP
  at all, and argparse's listed 43 subcommands alphabetically without saying which to
  reach for first. Seven topics as overridable templates plus a generated capability
  inventory. Three ratchets: every command a page names must exist as a CLI leaf or an
  MCP tool (mutation-verified against three distinct rot modes — a bad subcommand, a bad
  top-level command, a bad tool name), every topic offered must resolve, and every tool
  must belong to a group.

**Deferred, filed rather than guessed:** `verify` does not report phases still carrying a
derived prose slug rather than a source id. Detecting that honestly needs the importer to
record WHICH way an id was chosen (read from the heading, adopted from children, or
slugged), and inferring it from the shape of the id afterwards is the heuristic this
change spent its time removing elsewhere. Low value: the queue works, the ids are only
unrecognisable.

## B73–B78 — the workflow becomes visible and editable, 2026-09-25

Operator ask: document how the tool is driven both standalone and as an MCP server
(including what to put in `AGENTS.md`/`CLAUDE.md`), add a command that explains how the
CURRENT workflow works, and let a project change that workflow — from the agent over
MCP or by hand.

- **B73. Nothing showed the configured workflow as a whole. ✅ CLOSED.** `gate status
  <id>` showed one item's position, `config --explain` printed ~60 flat knobs, and the
  only place that ever joined the pipeline, the gates and the companions was the MCP
  handshake — computed once at connect and unreachable from a terminal. `orchard
  workflow` / `orchard_workflow` now answers it, including **where each value came
  from**, so a deliberate choice is distinguishable from an untouched default.

- **B74. A pipeline naming an undefined gate was a silent, permanent trap. ✅ CLOSED.**
  Nothing validated pipeline contents. `status()` folds the unknown id to `""`,
  `require_outcome` defaults True so `complete` refuses forever, and `gate record`
  rejects the id as unknown — so the item could never be completed at all except with
  `--force`, and nothing anywhere said why. One typo bricked every item entering the
  pipeline. Now refused at write time with the near miss named, and reported by both
  `orchard workflow` and `orchard doctor`.

- **B75. The config write paths validated syntax only. ✅ CLOSED.** `--append-toml`
  parsed the merged text for SYNTAX and then validated `Config.load(repo)` — the config
  already on DISK. A writer that validates the state it is replacing has checked
  nothing: `[gatez]`, or any unknown knob, was written and broke every later command.
  `Config.check(data)` now validates the RESULT, and `tomlcfg.atomic_write` replaces
  the file through a temp + rename, because a truncating write interrupted halfway
  leaves an empty config that loads as "no overrides at all" without saying so.

- **B76. Decisions have no structured source. FILED.** Items carry `Item.source`,
  lessons `seen_in`, research `sources`, notes `note["source"]` — decisions carry only
  the prose `context`. So the vanished-source check covers items, lessons, research and
  notes, and cannot cover decisions. Parsing the path back out of the sentence is the
  anti-pattern this series spent its time removing, so the gap is recorded rather than
  papered over. The fix is a `sources` field on `Decision`, which is a model change
  wanting its own commit. *Found by: the cross-family critic, THEORETICAL, correctly.*

- **B77. Imported notes were counted without a provenance filter. ✅ CLOSED.**
  `len(sess.notes)` counted every note in `s-imported-journal` / `s-imported-memory`,
  and `orchard session note <sid>` accepts ANY session id — so one hand-written note
  inflated the imported count. Probed: 1 → 2. The sibling loop for lessons, decisions
  and research already filtered on the `imported` tag; this one did not. *Found by: the
  cross-family critic, raised THEORETICAL with a refutation ("if those sessions are
  written only by the import") that a five-line probe killed.*

- **B78. Cursor was supported, in the default agent set, and named nowhere a user
  looks. ✅ CLOSED.** Absent from the `--agents` help, the `orchard_setup` tool
  description and the README's agent table. Two README links to `templates/drivers/…`
  were also broken — the real path is `orchard/templates/drivers/…`. A ratchet now
  asserts every key of `AGENT_TARGETS` is named in all three places.
