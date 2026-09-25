# Backlog — findings accepted but not fixed in this pass

Each entry is a real finding from the review stack that was judged out of scope for the
initial build. None is a silent TODO in the source: they are all here, with the analysis
that produced them, so the next session does not rediscover them.

In an adopted project these would be ddflow tasks (`ddflow task add ...`). They live in
markdown here because ddflow is not yet dogfooding itself — which is itself the first
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
  **✅ CLOSED — the work shipped and this entry was never marked.** `schedule.interrupted` is the one classifier; `leases.scan` calls it (core/schedule.py:305, services/leases.py:380). Found by auditing every B1-B78 claim against the source rather than trusting its own marker, which is the drift `ddflow import --verify` exists to catch in other projects.


- **B3 — The live-lease dict is computed three times** (`State.active_leases`, inline in
  `plan`, and formerly in `_alternatives`). Route all of them through
  `State.active_leases`. *Found by: roborev architecture (C6).*
  **✅ CLOSED — the work shipped and this entry was never marked.** every caller goes through `State.active_leases` (6 call sites, none inline). Found by auditing every B1-B78 claim against the source rather than trusting its own marker, which is the drift `ddflow import --verify` exists to catch in other projects.


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
  **✅ CLOSED — the work shipped and this entry was never marked.** `EventLog.head()` answers it in O(shards) (infra/log.py:294, infra/store.py:148). Found by auditing every B1-B78 claim against the source rather than trusting its own marker, which is the drift `ddflow import --verify` exists to catch in other projects.


## Declared but unimplemented

- **B6 — `log.compacted` is vocabulary without a mechanism.** The event kind exists,
  `PROVENANCE_KINDS` documents what compaction must preserve, and `fold` handles the
  marker — but nothing implements it. Either implement (rewrite shards keeping every
  `PROVENANCE_KINDS` event plus the last state-bearing event per subject, then append
  the marker) or remove the kind. Declaring a retention policy the system cannot execute
  is worse than declaring none. *Found by: roborev architecture (C10). Not urgent: at
  407k events/s the growth ceiling is far away.*

- **B7 — ddflow does not dogfood itself.** This backlog should be an ddflow queue, and
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
  **✅ CLOSED — the work shipped and this entry was never marked.** `_transition` holds the shared body; renew/release/expire call it (services/leases.py:248). Found by auditing every B1-B78 claim against the source rather than trusting its own marker, which is the drift `ddflow import --verify` exists to catch in other projects.

  **✅ CLOSED — the work shipped and this entry was never marked.** `_require_item` is the one place that says 'no such item' (surfaces/cli.py:161, 12 call sites). Found by auditing every B1-B78 claim against the source rather than trusting its own marker, which is the drift `ddflow import --verify` exists to catch in other projects.

  **✅ CLOSED — the work shipped and this entry was never marked.** `_item_parser` builds both (surfaces/cli.py:3525). Found by auditing every B1-B78 claim against the source rather than trusting its own marker, which is the drift `ddflow import --verify` exists to catch in other projects.

  **✅ CLOSED — the work shipped and this entry was never marked.** `_csv` delegates to `config.csv_list` (surfaces/cli.py:135). Found by auditing every B1-B78 claim against the source rather than trusting its own marker, which is the drift `ddflow import --verify` exists to catch in other projects.


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

  **✅ CLOSED — the work shipped and this entry was never marked.** the cross-family critic ran on every pass since; B57-B61, B76, B77 came from it (docs/RESEARCH.md R10-R12). Found by auditing every B1-B78 claim against the source rather than trusting its own marker, which is the drift `ddflow import --verify` exists to catch in other projects.


## From mining this project's source repository's lessons corpus (2026-09-24)

A subagent read `docs/lessons-summary.md`, the 25 newest `docs/lessons.md` entries and
the standing rulebooks of the project ddflow was extracted from, looking for
capabilities born of repeated failure. Four were implemented immediately (they are gone
from this list); these are the rest, ranked as it ranked them.

- **B15 — `ddflow gate verify <gate>`: mutation-verify that a gate CAN fail.** ddflow
  has ten gates and nothing proves any of them is capable of going red. A gate declares
  `mutations = [{file, old, new}]`; the runner asserts `src.count(old) == 1` *before*
  patching (a mutation that did not apply is not a passed mutation test — the suite goes
  green and reads as "the check cannot detect this", the opposite conclusion), requires
  a non-zero exit, restores. A gate with zero registered mutations is itself a failure.
  *This is the highest-value item on the list: it is the anti-vacuous-pass check applied
  to ddflow's own checks.*
  **✅ CLOSED — the work shipped and this entry was never marked.** `gates.verify` mutation-verifies a gate, baseline included (services/gates.py:376). Found by auditing every B1-B78 claim against the source rather than trusting its own marker, which is the drift `ddflow import --verify` exists to catch in other projects.


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
  regenerated in pre-commit and must come back byte-identical. ddflow's own
  `docs/ddflow/*.md` views have exactly this hazard.

- **B19 — pickability audit.** After filing a task, assert `ddflow next` can actually
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
  read like rationale and was a rule". ddflow ships a driver and a rulebook block.

- **B23 — stale-rulebook / behind-count gate for long-lived branches.** Rulebook edits
  landing on the default branch are silently ignored by sessions that loaded them at
  start, and drift compounds: a 98-commits-behind branch had to be hand-ported. ddflow
  creates long-lived worktrees and has no staleness half. Note the asymmetry the source
  project settled on: the session hook *informs* (always exit 0), the commit gate
  *blocks*.

- **B24 — per-gate fire-rate tracking.** "A gate that fails on everything is worse than
  no gate: it trains the next reader to skip it." ddflow has the event log to compute
  this from; a gate firing on ~100% of changes should be auto-flagged for repair.

- **B25 — cadence fired-vs-scheduled counter.** "The mechanism you did not measure is the
  one that is not running" — on the source project three wakeups were scheduled and zero
  ever fired, producing a 12-hour stall while an undesignated mechanism did the work.
  **PARTIAL.** The due-ness computation exists (`cli.py:2451`); what is missing is a fired-vs-scheduled counter, so the cadence's own hit rate still cannot be argued.


- **B26 — test-polluter bisect.** Delta-bisect the test file that makes another fail only
  in full-suite order. Generic and high-value, but only once ddflow owns test execution
  rather than shelling out to a project's own command.

## B27–B34 — from the 2026-09-24 review pass

Filed rather than fixed, each with why it is not urgent.

- **B27. `critical_path` ignores inherited dependencies.** Readiness now consults an
  item's ancestors (`schedule.inherited_deps`), but the critical-path calculation still
  walks `needs` alone, so a phase-level dependency does not lengthen the reported path.
  The number is advisory — it sets an expectation, not a decision — and it is currently
  *shorter* than the truth, which is the harmless direction for a figure nobody gates on.
  *Found by: writing the inheritance fix.*
  **✅ CLOSED — the work shipped and this entry was never marked.** `critical_path` walks `inherited_deps` (core/schedule.py:477). Found by auditing every B1-B78 claim against the source rather than trusting its own marker, which is the drift `ddflow import --verify` exists to catch in other projects.


- **B28. The parallelism cap counts leases, not worktrees. ✅ CLOSED.** The two knobs
  were combined with `min()`, which is one number pretending to be one statement:
  `schedule.max_parallel_tasks` says how many items may be IN FLIGHT, `worktree.
  max_parallel` says how many trees may EXIST. Under the old form a machine allowed one
  tree could not run a second `--no-worktree` task, and a queue allowed four in flight
  silently became one — the silent-knob-drop class. Now two independent checks, both
  counted across the whole queue, each naming itself when it is the one that refused.
  `tests/test_rubber_duck_findings.py` mutation-verified.

- **B29. Companion detection has no cache.** `ddflow companions` probes on every call,
  and an `npx`-based probe can take seconds on a cold cache. `adopt` pays this once, and
  `--no-probe` exists, but a session-start hook that called it would feel it. A cached
  result with a short TTL in the gitignored index would fix it. *Found by: the first
  `adopt` run after companions landed.*
  **✅ CLOSED.** Probe results cache to `.ddflow/local/` (already gitignored, so nothing new to ignore and nothing machine-local ever committed), bounded by a new `[companions] probe_cache_ttl_s` knob, default 300 s. One deliberate asymmetry: a NEGATIVE result caches, an INCONCLUSIVE one never does — "could not tell" is transient, and caching it would make one blip stick for the whole window and report `unknown` about a tool sitting right there. A corrupt cache is a miss, never an error. Four behaviours mutation-verified; the ttl=0 case needed a frozen clock, because unfrozen it passed on clock ordering rather than on the guard.


- **B30. `gates.enforce_order` defaults to "warn" and nothing measures how often it
  fires.** If the warning is routine it is noise and the default should move to "off"
  for that project; if it is rare it should probably be "block". Neither can be argued
  without a fire-rate, which is the same gap B22 names for gates generally.

- **B31. No mutation test for the inherited-dependency rule at the CLI level.** The unit
  tests mutation-verify `inherited_deps` and `plan_blocker`. The scenario asserts the
  behaviour end to end but is not itself mutated, so a regression that only manifests
  through the MCP path would be caught by the scenario failing rather than by a
  demonstration that it *can* fail.
  **✅ CLOSED.** `tests/test_inherited_deps.py` now patches `inherited_deps` back to the naive version — an item's own `needs`, ancestors ignored — and REQUIRES the MCP surface to start handing out blocked work. In-process through `mcp._run_cli`, because a subprocess would not see the patch and the test would pass for the wrong reason, which is the trap that file's own docstring warns about.


- **B32. `companions.is_installed` is two-valued.** A probe that times out is reported
  as not-installed with the timeout in the detail, which reads correctly to a human but
  collapses "absent" and "could not tell" for any caller reading the boolean. `lease`
  already solved this shape with a three-valued `salvageable: bool | None`; this should
  follow it rather than invent a second convention.
  **PARTIAL.** `Status.installed` is already three-valued for *not probed*; what is missing is returning `None` when the probe TIMES OUT (`services/companions.py:131` still returns `False`), which is the case that collapses "absent" into "could not tell".

  **✅ CLOSED.** `is_installed` now returns `None` on a timeout or a failed spawn, and the register path says "could not tell" rather than "not installed here" — which was sending operators to install something they already had. `tests/test_companions.py`, mutation-verified in both directions: a PATH miss must stay `False`, or the unknown state means nothing.


- **B33. The `full-lifecycle` scenario is not run by `pytest`.** Neither is any other
  scenario — `demos/` is invoked separately and is not in the publish workflow. The
  scenarios have found most of the real bugs in this project, so the one suite that
  matters most is the one CI does not run. (Ceremony note: they take ~2 minutes and
  spawn processes, so they want their own marker and job, not inclusion in `tests/`.)
  **PARTIAL.** `tests/test_scenarios.py` and the `slow` marker exist, so the scenarios are reachable by name — but `.github/workflows/publish.yml:32` runs `pytest tests/ -q` under `addopts = "-m 'not slow'"`, so CI still never runs them. The suite that has found most of this project's real bugs is still the one automation skips.

  **✅ CLOSED.** The gap was bigger than the entry said: there was no CI on push or pull request AT ALL, only `publish` on a version tag. Added `.github/workflows/ci.yml` (lint + tests on 3.11 and 3.13, scenarios as their own job) and made a release run the scenarios too. Two ratchets in `tests/test_scenarios.py`, because a test file that exists and a marker that selects it are not the feature — being RUN is: one asserts a workflow actually invokes `-m slow` on the scenario file, the other that something checks ordinary commits and not only tags. The first ratchet passed on a COMMENT mentioning `-m slow` until comments were stripped, which is the vacuous-pass class inside the check written to prevent it.


- **B34. `ddflow history` does not exist as one view. ✅ CLOSED.** One
  reverse-chronological timeline over the log, filterable by `--item`, `--kind`
  (families or exact kinds), `--since` and `--limit`, on both surfaces. The MCP tool was
  missing on the first pass and `tests/test_mcp_parity.py` caught it, which is what that
  ratchet is for.

## B35–B40 — from roborev's architecture pass, 2026-09-24

Structural debt rather than defects. Filed with what it costs today, because the point
of recording it is to stop the next reading re-deriving it.

- **B35. ✅ CLOSED 2026-09-25** (commit 87d5fbb: `ddflow/api.py` + typed MCP dispatch;
  ONE tool migrated, `ARGV_TOOLS_CEILING` ratchets the remaining 62 downward).
  Original finding: there is no application layer; the protocol adapter depends on the
  presentation layer.** `mcp_server → cli` is the only edge and it is carried by
  **strings**: typed MCP arguments are flattened to argv, re-parsed by argparse, and the
  result is recovered by scraping stdout plus an exit code. The costs are already
  visible in the code — `_opt(..., clearable=True)` exists only to re-create the
  "absent vs empty" distinction argv erased, and `_run_cli` swaps process-global
  `sys.stdout`/`sys.stderr` for each call, which is not reentrant and forecloses
  concurrency. Parity is held by ratchets where types would hold it structurally, and
  those ratchets catch a *missing* flag, not a *changed* encoding. The fix is a real
  application layer (`ddflow/api.py`) that both surfaces call; it is a large change
  and the ratchets make the current shape safe, so it waits for a reason rather than a
  free afternoon.

  **`services/outcome.py` was the groundwork and shipped DEAD** — 91 lines, zero
  importers, committed in 89386614 and never wired into any of the ~60 command
  functions. Deleted 2026-09-24 rather than left in place: an unreferenced module that
  ships in the package is worse than a backlog entry, because it is importable,
  untested and reads as live API to the next person. `git show 89386614:ddflow/ddflow/services/outcome.py`
  has it when B35 is actually done. *Found by: reading the change surface before
  committing.*

- **B36. `cli.py` is a god module — 3,041 lines when filed, 4,208 now. STILL OPEN, and
  moving the wrong way.** Extracting `services/completion.py` (B35/B36) removed policy
  but the surface kept growing: `approve`, the dry-run plumbing, the human-gate
  branches and the companions guard all landed here. A number in a title is a fact with
  an expiry date; this one is recorded rather than quietly corrected, because the drift
  IS the finding. Original:** ~60 command functions, a 430-line
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
  'ddflow.cli'`. Harmless today; it is a tell for B35.

- **B39. `State` has no parent index, so `children()` is a full scan** and
  `descendants()`/`ancestors()`/`_is_umbrella()` call it per node per candidate.
  Measured (1 phase + N tasks): `n=100 → 2.0 ms`, `n=400 → 18.8 ms`, `n=800 → 46.1 ms`;
  cProfile at n=800 attributes 74% of `plan()` to 1600 calls into `children`. Roughly
  quadratic, harmless at realistic sizes, and a cheap fix (build `parent → [child]`
  once per fold). Related: every call refolds the whole log.
  **✅ CLOSED — the work shipped and this entry was never marked.** `State._child_index` is built once per fold (core/model.py:279). Found by auditing every B1-B78 claim against the source rather than trusting its own marker, which is the drift `ddflow import --verify` exists to catch in other projects.


- **B40. TRUNCATED-completion diagnostics are written three times** in `reviewer.py`
  (openai, anthropic, gemini), and have already drifted: the openai copy names
  `max_chunk_chars` and reports reasoning-token counts, the gemini copy names neither.
  Same class as the four duplications fixed in this pass, just lower blast radius —
  it degrades a message rather than a decision. *Found by: roborev duplication (D6).*
  **✅ CLOSED — the work shipped and this entry was never marked.** `review._truncated` is the one diagnostic; 4 call sites (services/review.py:533). Found by auditing every B1-B78 claim against the source rather than trusting its own marker, which is the drift `ddflow import --verify` exists to catch in other projects.


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

- **B53. `ddflow merge` acted on an item REMOVED from the queue. ✅ CLOSED.** It was
  the one mutating command not routed through `_require_item`. Removal is a FLAG on an
  item that still folds, so `st.items.get()` found it and only the flag said it was
  gone — and `ddflow merge T1` landed the branch of work the operator had explicitly
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
  now carries `source`, the body prose stays for `ddflow show`, and `item.completed`'s
  `evidence` — written by the importer since day one and DROPPED by the fold, the third
  instance of that bug in this series — lands on `Item.completion_evidence`.
  `ResearchNote` gained `tags` so `imported` is spelled the same way on all three memory
  kinds; telling an imported note from a hand-written one by the SHAPE of its `sources`
  would have been a heuristic pretending to be a fact.

- **B70. `ddflow import --verify` / `ddflow_import_verify`. ✅ CLOSED.** Status (what
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
  `ddflow_import_verify` before handing any of it out.
  `/import-existing-project` opens by checking what is already imported and branches to
  finishing-and-refreshing. Same name: renaming breaks anyone invoking it, and a second
  near-identical prompt is two documents that drift.

- **B72. `ddflow help` / `ddflow_help`. ✅ CLOSED.** There was no help surface on MCP
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
  handshake — computed once at connect and unreachable from a terminal. `ddflow
  workflow` / `ddflow_workflow` now answers it, including **where each value came
  from**, so a deliberate choice is distinguishable from an untouched default.

- **B74. A pipeline naming an undefined gate was a silent, permanent trap. ✅ CLOSED.**
  Nothing validated pipeline contents. `status()` folds the unknown id to `""`,
  `require_outcome` defaults True so `complete` refuses forever, and `gate record`
  rejects the id as unknown — so the item could never be completed at all except with
  `--force`, and nothing anywhere said why. One typo bricked every item entering the
  pipeline. Now refused at write time with the near miss named, and reported by both
  `ddflow workflow` and `ddflow doctor`.

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
  and `ddflow session note <sid>` accepts ANY session id — so one hand-written note
  inflated the imported count. Probed: 1 → 2. The sibling loop for lessons, decisions
  and research already filtered on the `imported` tag; this one did not. *Found by: the
  cross-family critic, raised THEORETICAL with a refutation ("if those sessions are
  written only by the import") that a five-line probe killed.*

- **B78. Cursor was supported, in the default agent set, and named nowhere a user
  looks. ✅ CLOSED.** Absent from the `--agents` help, the `ddflow_setup` tool
  description and the README's agent table. Two README links to `templates/drivers/…`
  were also broken — the real path is `ddflow/templates/drivers/…`. A ratchet now
  asserts every key of `AGENT_TARGETS` is named in all three places.

## B79–B86 — many agents on one server, and what two rival MCPs do better, 2026-09-25

Prompted by an operator question that turned out to have a wrong answer in the code:
*"if many agents and subagents call the same MCP server, how does it know which call
comes from whom?"* It did not. Plus a read of two existing workflow MCP servers, for
features worth taking.

- **B79. A companion could only be an MCP server. ✅ CLOSED.** `entry()` would build a
  plausible launch block from any `command`, `companions add` would write it into an
  agent's config, and the agent would fail the JSON-RPC handshake the first time a gate
  reached for the tool — a registration that reads as done and is not. `Companion.kind`
  is now `mcp` or `cli`; `add` refuses a `cli` one and names its install command
  instead. Registry gains **sequential-thinking** (`research`, `rubber_duck`,
  `bug_hunt`) and **OptMem** (`cli`, `rules`), and the `codeguide` entry was pointing at
  a different project than the one actually in use — corrected to
  `docker.io/delian/codeguide-mcp`. *Found while adding OptMem, which has no MCP mode.*

- **B80. Two agents in one worktree were ONE agent. ✅ CLOSED.** Identity was derived
  from the working tree, and `log.py:96` already said in a docstring that a harness
  running several agents in one tree "must set `DDFLOW_AGENT` — there is no signal that
  can distinguish them otherwise". Over MCP there was no way to set it: the env var is
  process-wide. So their events merged into one stream, `brief` answered with a
  sibling's task, and reviewer-independence compared an agent with itself and passed —
  a review gate satisfied by the author reviewing their own work. New `ddflow_identify`
  declares identity per CONNECTION, threaded through both the argv and the typed
  dispatch paths. Mutation-verified: dropping the threading turns three tests red.

- **B81. The MCP surface had no load or concurrency coverage at all. ✅ CLOSED.**
  `test_stress.py` and `test_lease_and_recovery.py` drive the CLI and the log directly;
  nothing exercised the surface the agents actually use. `tests/test_mcp_load.py` runs
  12 concurrent agents through real JSON-RPC and asserts no deadlock (a hard timeout —
  a wedged `flock` hangs rather than fails), no lost append, no repeated Lamport value
  within an agent, and correct attribution for EVERY event rather than a sample. Every
  threshold is an environment variable, because a load test with a hardcoded budget
  either flakes on a shared runner or is too loose to fail. Wired into CI as its own
  step with `if: always()`.

- **B82. No human-in-the-loop gate. ✅ CLOSED 2026-09-25** — see
  "§B82 — a gate the agent cannot clear" below for what shipped. Original finding: Every gate here is agent-driven or
  command-driven. `spec-workflow-mcp` gates each phase on an explicit human approval
  with a review UI, and for a plan or a spec that is the right checkpoint — an operator
  may well want to approve before agents burn compute on it. Fits the existing
  abstraction: a `human_review` gate kind that blocks on an approval record, plus a
  read-only projection of queue state to review against. The dashboard is the large
  part and is separable from the gate. *From: `pimzino/spec-workflow-mcp`.*

- **B83. No ad-hoc prompt macros. FILED.** `dx-zero/mcpn` defines named workflows in
  YAML — a system prompt plus a bound subset of tools plus `{{param}}` injection —
  invoked as "enter debugger mode". That is a genuinely different axis from the gate
  pipeline: operator-triggered modes that do not belong in the queue at all. `ddflow
  prompts` already has the template resolution and override precedence this would need.
  **Not to copy: their `toolMode: situational`**, where the model freely picks which
  tool to call from a bound set with no recorded ordering or rationale — that
  reintroduces the non-reproducibility the event log exists to remove.
  *From: `dx-zero/mcpn`.*

- **B84. Gate evidence records no diff statistic. ✅ CLOSED 2026-09-25** — see
  "§B84 — gate evidence records how much it was looking at" below. Original finding: `spec-workflow-mcp` keeps
  per-task implementation logs with code statistics. Cheap here — an optional
  files/lines-changed field beside the existing evidence and `tree_sha` — and it makes
  "what did this gate actually review" answerable rather than assumed.
  *From: `pimzino/spec-workflow-mcp`.*

- **B85. Half the prompt library was invisible from the CLI. ✅ CLOSED.** The MCP
  surface serves both registries through `prompts/list`; the CLI's `list_all` walked
  `TEMPLATE_NAMES` only. So `ddflow prompts list` showed five templates and none of the
  six workflow commands, `prompts show <command>` answered `unknown template` while
  listing five names that did not include the one you correctly typed, and `eject` —
  the documented way to customise a prompt — could not copy out a command at all.

  A divergence in the direction nothing looks: `test_mcp_parity` asks whether every CLI
  command is on MCP, which is what matters for an agent. The reverse matters for the
  operator, and without a check a capability can sit on one surface indefinitely.

  Caught by writing `ddflow prompts show research-companions` into the README one
  commit earlier and then running it — the doc was written from what the tool *should*
  do. `list_all` now returns both, `resolve_any` resolves either, `eject` writes a
  command into `prompts/commands/` where resolution actually looks (beside the
  templates would have produced an override the operator edits and the tool never
  reads), and a ratchet asserts everything reachable over MCP is reachable from the
  CLI. Nine of the twelve new tests were red before the fix. A further test asserts the
  README never names a prompt that does not resolve.

- **B86. No compaction on the state-reading path. FILED, not urgent.** Every
  state-reading call re-folds the whole log. Measured 2026-09-25: linear, converging on
  **~8.7 µs/event** — 20,000 events is ~175 ms per call, which is fine. At ~100k events
  it becomes ~0.9 s, which is not. Search and recall already avoid this via the SQLite
  projection; the scheduling and status path does not. Filed with the numbers so the
  decision to act is made against a measurement rather than a worry. *Supersedes the
  vaguer B6.*

## B87–B95 — what the two reviewers found on B79–B81, 2026-09-25

Both reviewers ran on this round after a setup fix each: roborev had no repo-local
`.roborev.toml` in this repository (it is its own git repo since the extraction) and so
fell through to the machine-global `default_agent = codex`, which is not installed —
**and it exited 0 while reporting that it could not review**, which is the vacuous-pass
class at the level of a whole reviewer. The cross-family critic's first run was killed
at a 900s budget and its second was invoked with a wrong flag; the third completed.

Nine findings, all either fixed here or filed below. Two of them were in code shipped
one commit earlier, and two were in the tests written to catch exactly their class.

- **B87. `tree_fingerprint` could not see a re-edit of an already-dirty file. ✅ CLOSED.**
  `git status --porcelain` is two status letters and a path — no content — so once a
  file was modified, every further edit produced byte-identical output and the digest
  did not move. Not an edge case: at gate time the tree is ALREADY dirty, so
  `stale_evidence`'s own documented scenario ("run the tests, edit one more thing,
  complete") was the case it could not detect, and a gate that passed on superseded
  source read as fresh evidence. The existing test passed by adding an UNTRACKED file,
  which moves the path list — it proved the case that already worked. `git diff HEAD`
  now goes into the digest. **Known limit, documented not papered over:** `git diff`
  renders a binary file as "Binary files differ", so a re-edited binary is still
  invisible; `--binary` costs far more than it buys. *Cross-family critic, CONFIRMED.*

- **B88. The typed and argv paths resolved DIFFERENT identities. ✅ CLOSED.** B80
  threaded identity through both paths only for a DECLARED name. Undeclared, the argv
  path went through `cli.Ctx` (which resolves `--agent` → `DDFLOW_AGENT` → `[agent].id`
  → tree) while the typed path called `EventLog(repo, "")`, which reads neither the env
  var nor the config. With `DDFLOW_AGENT=alpha` — which the demo harnesses set —
  `ddflow_claim` wrote as `alpha` and `ddflow_update` wrote as the directory name, on
  one connection, into different shards. `ddflow_identify` reported the tree name too,
  so the tool whose job is to make identity visible misreported it. Two encodings of
  one precedence; there is now one, `infra.log.effective_agent_id`, called from both.
  *roborev, CONFIRMED, reproduced before fixing.*

- **B89. The `registered`-is-unreachable class survived at two more sites. ✅ CLOSED.**
  B79 fixed `gate_coverage` and the `gaps` list and left the handshake payload and
  `cmd_adopt` re-deriving "goal state" themselves. So `optmem` was in
  `missing_companions` on every connection and the instructions told the agent — as
  "something to DO" — to run `ddflow_companions_add` on it, which now refuses; and
  `adopt` printed "installed here but not wired up" with a command that cannot act.
  Four sites, one predicate: `Status.usable` / `Status.is_gap`. The repo-wide sweep the
  rules ask for was done for two of three call sites and reported as complete.
  *roborev, CONFIRMED ×2.*

- **B90. Detection probes INSTALLED software. ✅ CLOSED.** `npx -y` fetches and installs
  the package in order to run it, and three shipped entries detected that way — so
  `ddflow companions`, which the MCP handshake and `adopt` both call, downloaded
  packages onto the operator's machine. That breaks this module's first stated rule.
  It also destroys the answer: the probe stops meaning "is this installed here" and
  starts meaning "can npm reach the registry", which cannot say no on any networked
  machine, so the companion counted toward its gates everywhere — an always-yes
  detector. Now `npx --no-install`, with a ratchet over the whole registry. `context7`
  and `memory` had the same shape and predate this work; the class was swept, not the
  new entry alone. *roborev, CONFIRMED.*

- **B91. Two of the new load assertions could not fail. ✅ CLOSED.** (a) The write
  latency budget: `elapsed / (12 × 15)` is bounded above by `LOAD_TIMEOUT_S / 180 =
  1.33s`, already under the 2.0s default and far under the 6.0s the new CI step sets —
  so the budget was decoration, the CI override was dead, and a slow-but-not-wedged
  runner would have been reported with the message "that is the deadlock signature".
  Now measured per worker. (b) `_reader` counted `"result" in reply`, which is true for
  an `isError` reply, so it could only ever see a read that HUNG. *roborev, CONFIRMED
  by arithmetic.*

- **B92. The read-modify-write load test proved nothing, and hid a broken test. ✅
  CLOSED.** `expected` came from the workers' own success counts, so the all-failed case
  read as a pass: 0 written, 0 survived, "nothing was lost". Asserting failures are zero
  turned it red immediately — **every `ddflow_lesson_add` in it had been failing**, with
  "missing required argument(s): title", because the worker passed `text`. The product
  was fine; the test had never exercised it. Mutation-verified both ways.
  *roborev, CONFIRMED — and the real defect was worse than the report.*

- **B93. A malformed companion registry silently deleted a whole handshake section. ✅
  CLOSED.** `_instructions` wraps the scan in `except Exception: pass`, so one typo in
  `.ddflow/companions.toml` removed the companions and gate-gap block entirely —
  "nobody could look" rendering as "no gaps", inside the report whose purpose is to
  expose exactly that. Now a `setup_todo` line naming the cause. `cmd_companions` also
  let the loader's `ValueError` escape instead of printing the sentence it carefully
  writes. **The first version of this test passed on unrelated template prose** and was
  rewritten to assert the specific signal. *roborev, CONFIRMED.*

- **B94. Stale docs and a dead parameter from the previous commit. ✅ CLOSED.** Removing
  `ddflow_update`'s argv lambda left `_opt(..., clearable=True)` with no caller — the
  dead-fallback shape that commit cited as its own reason for the removal. Gone, with
  its docstring. Also: a README claim that "writes never contend… a short exclusive lock
  is taken only to allocate the next Lamport value", which is not what `EventLog.append`
  does (the lock spans the clock read, the write and the `fsync`); two README headings
  and the `companions.py` docstring still saying "Companion MCP servers"; "ships the
  four" when there are six; and `help/parallel.md` — the page about several agents at
  once, served as an MCP resource — never mentioning `ddflow_identify`.
  *roborev, mixed CONFIRMED/doc-drift.*

- **B95. `tree_fingerprint` is blind to a re-edited BINARY file. FILED, THEORETICAL.**
  `git diff` emits "Binary files … differ" with no content, so the B87 fix does not
  cover binaries. No probe ships because no gate in any pipeline here tests a binary
  artifact, so the defect has no reachable consequence today. The fix (`--binary`,
  base85-encoding whole blobs into a digest) would make every gate run proportional to
  the size of the changed binaries. Recorded so it is a known limit rather than an
  assumption. *Noted while fixing B87.*

**Refuted from this round.** The critic raised `tests/test_layering.py:271`'s
`isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)` as a Python
3.9 `TypeError`, labelled THEORETICAL and explicitly unverifiable from the file it was
shown. `pyproject.toml` declares `requires-python = ">=3.11"` and CI runs 3.11 and 3.13,
so the trigger does not exist. Recorded as refuted rather than dropped — it is a
correctly-reasoned finding about a premise that happens to be false, which is what
`THEORETICAL` is for.

## B96–B97 — the critic on 031313a, 2026-09-25

Two findings, both correctly labelled THEORETICAL, both worth recording rather than
acting on as bugs.

- **B96. `_VALID_AGENT` did not refuse the character its comment singled out. ✅ CLOSED
  as hardening, REFUTED as a defect.** `^[A-Za-z0-9._-]{1,64}$` with `.match()` accepts
  `"reviewer\n"` — Python's `$` matches at end-of-string *or* immediately before a final
  newline. Probed: the pattern does match it. Also probed: the handler strips the name
  first, so nothing with a newline ever reached the check, and no shard filename could
  be corrupted. No behaviour changed, so no regression test could have failed and none
  is claimed. What changed is WHICH line carries the guarantee: `fullmatch`, no anchors,
  and a comment that says the strip is a courtesy rather than the safety. Two lines
  disagreeing about which is load-bearing is how the next edit deletes the wrong one —
  the third instance this session of a comment promising what the code did not do.
  *Cross-family critic, THEORETICAL, correctly: it could not see the call site.*

- **B97. `_run_cli` swaps process-global `sys.stdout`/`sys.stderr` per call. FILED,
  THEORETICAL.** Non-reentrant by construction: two overlapping calls would interleave
  each other's captured output, and on a stdio transport the escaped writes would
  corrupt the protocol stream. Not reachable today — `serve()` is a strictly sequential
  `for raw in inp:` loop, one message fully handled before the next is read — and
  `tests/test_mcp_load.py` contends with separate PROCESSES, so it does not exercise
  this either. It is filed because the direction of travel makes it live: per-connection
  identity, a load suite, and "several agents at once" all point at a dispatcher that
  eventually overlaps. The fix is the migration already under way — every tool on the
  typed `api` path returns an `Outcome` and captures nothing — so the right move is to
  keep lowering `ARGV_TOOLS_CEILING`, not to add a lock around a stream swap.
  *Cross-family critic, THEORETICAL, correctly: the dispatch model was in the half of
  the diff it was not shown.*

## B98–B105 — roborev on the review-fix commit itself, 2026-09-25

Eight findings on `0e23b31`, and the theme is one sentence: **three of that commit's
fixes stopped one site short**, in a commit whose message claimed a repo-wide sweep. The
"generalize every bug fix — hunt the CLASS" rule failed three times in the act of
applying it.

- **B98. The handshake still told agents to register a `cli` companion, and the new
  test could not see it. ✅ CLOSED.** `_instruction_vars` scans with `probe=False` (a
  session start must not wait on `npx`), so every `installed` is `None`; `usable`
  collapsed that to `False`, so `optmem` was in `missing_companions` on every
  connection. Worse, the kind-aware lists that fix added were **referenced nowhere in
  any template** — computed and dropped — and the regression test asserted on
  `unregistered_companions`, a variable nothing rendered. It passed over an instruction
  block that had not changed a word. **Third vacuous test of the session.** Now
  bucketed by `Status.advice` (`ok`/`register`/`install`/`check`), every bucket
  rendered, and the test reads `_instructions(repo)` — the string an agent receives.
  Mutation-verified against the old template.

- **B99. `Status.usable` collapsed three values into two, and its docstring said it
  did not. ✅ CLOSED.** The docstring promised "neither counts on `installed is None`";
  the code returned `False` for it. With `probe=False` that made "nobody looked" render
  as "no tool": `gate_coverage` reported `rules` in `gate_gaps` on every connection
  even with `memo` on the PATH. `usable` is now `bool | None`, `is_gap` is `usable is
  False`, and a gate is only a gap when no companion serving it is unknown. **Fourth
  instance this session of a comment promising what the code did not do.**

- **B100. B88's sweep missed `_instruction_vars`'s own `EventLog`. ✅ CLOSED.**
  `EventLog(repo, cfg.agent.id or "")` resolves neither `DDFLOW_AGENT` nor a declared
  name, and that identity is what `plan()` uses to decide which items are "already
  mine" — so the handshake reported the connection's own claimed work as someone
  else's, at the one moment the agent is told what to do next. `_instructions` now
  takes the connection's declared agent and threads it through.

- **B101. B87's fix left the identical hole for UNTRACKED files. ✅ CLOSED.** `git diff
  HEAD` never shows untracked content and porcelain shows only `?? path`, so a new
  module — untracked until its first commit, which is the ordinary state of agent work
  — could be rewritten entirely between the gate and the completion with the
  fingerprint unmoved. Probed and confirmed. Now hashed with `git hash-object` (no
  `-w`, so nothing is written), capped by the configurable `MAX_UNTRACKED_HASHED` with
  a degraded-and-SAID-SO fallback above it.

- **B102. ...and fixing B101 introduced a false positive on the ordinary path. ✅
  CLOSED, self-inflicted.** Hashing untracked content included `.ddflow/` — where
  recording a gate outcome appends an event. So the fingerprint moved as a direct
  consequence of taking it, and **every completion warned "passed on a different
  tree"**. A warning that always fires is one nobody reads, which is how this check
  gets switched off. Caught by `test_it_stays_quiet_when_nothing_moved`, which is
  exactly why that test exists. `FINGERPRINT_EXCLUDE` now drops `.ddflow/` from all
  three git calls, as pathspecs so git does the matching rather than a post-filter that
  would drift from what git considers inside the directory.

- **B103. The isError-blind read counter survived in `_mixed`. ✅ CLOSED.** Fixed in
  `_reader` and missed five lines below, in the same commit, in the file about
  vacuous assertions. An error is returned AS a result with `isError: true`, so
  `"result" in r` counted a failed `next` as a success and the assertion could only
  see one that HUNG.

- **B104. `cmd_adopt`'s `CO.scan` was the third of three sites, and the other two were
  fixed. ✅ CLOSED.** A `kind = "MCP"` typo made `ddflow adopt` — documented as safe to
  re-run — exit on a raw traceback *after* `init` had already written files. One
  `_scan_companions` helper now guards all three, rather than a guard per call site,
  which is what produced two-of-three in the first place.

- **B105. `config --explain` blamed the environment for a value nothing set. ✅
  CLOSED.** The source was inferred by comparing the resolved value against each
  candidate, which is wrong whenever two agree: with nothing set, the derived name
  differs from `cfg.agent.id` (`""`), so the branch fired and recorded `env`. An
  operator debugging identity is the one person who cannot afford that. `resolve_agent_id`
  returns the layer that won (`explicit`/`env`/`config`/`derived`) instead of leaving
  two call sites to guess it, and `ddflow_identify` uses the same answer.

- **B107. Fixing B98 dropped the install command from the handshake. ✅ CLOSED,
  self-inflicted, caught by two existing tests.** Splitting the companions into three
  rendered blocks was honest about state and useless to act on: the "not checked" block
  — which with `probe=False` is EVERY companion — carried no install command and none
  of the propose/agree/never-install-unilaterally guidance. Two tests encoding the
  operator's original instruction went red
  (`test_the_ones_missing_HERE_are_called_out_with_what_to_do`,
  `test_each_missing_companion_carries_its_install_command`), and they were right. One
  block again, each line carrying a `state_word` — so the CLAIM is accurate and the
  ACTION is still available in the same turn. The correction to make twice-over: being
  careful about what you assert is not a licence to say less.

- **B106. `install` carried prose and shell comments. ✅ CLOSED.** The field is
  documented as "the command a human runs" and is printed verbatim under "ask the
  operator, then:" — so OptMem's entry rendered a full sentence where a command
  belongs, and two PRE-EXISTING entries carried trailing `# comments` that break the
  moment anyone pastes them somewhere without a shell. New `note` field for the
  caveat, swept across all four affected entries rather than the one that was new,
  with a ratchet. *Raised in passing by the cross-family critic before its run timed
  out.*

Also: `zip(ids, paths)` in the new untracked digest, caught by ruff `B905` — a short
reply from `hash-object` would have paired hashes with the wrong paths and produced a
plausible, meaningless fingerprint. Now length-checked with an honest fallback.

## B84 — gate evidence records how much it was looking at, 2026-09-25

- **B84. ✅ CLOSED.** `diff_stat` on every command gate's evidence: files, insertions,
  deletions, untracked count. `tree_sha` answers "which tree" and is opaque; this
  answers "how big", which is what makes a recorded pass auditable after the fact — a
  review gate that passed over 4,000 changed lines in two minutes is a different claim
  from one that passed over 12, and the log could not tell them apart.

  Deliberately NOT folded into `tree_fingerprint`: a fingerprint answers "is this the
  same tree", and two different trees can share a line count. Mixing a magnitude into
  an identity would weaken the identity and make the magnitude unavailable alone.

  Uses the same `.ddflow/` exclusion as the fingerprint, for the same reason (B102) —
  a number that grows every time ddflow records an event describes ddflow's
  bookkeeping, not the work. Keys are always present even outside a repository, so a
  reader never has to tell "no change" apart from "this field did not exist in the
  version that wrote the event". Mutation-verified.

  *From `pimzino/spec-workflow-mcp`, which keeps per-task implementation logs with code
  statistics (R13). The idea adopted; their separate log subsystem declined — this is
  one field beside the evidence that already exists.*

## B82 — a gate the agent cannot clear, 2026-09-25

- **B82. ✅ CLOSED (the gate; the dashboard stays filed).** Every gate in this pipeline
  was cleared by the agent — a command it ran, or an assertion that it thought. Right
  for work checkable after the fact; wrong for a plan, where by the time the agent has
  built the wrong thing the cost is already paid. `human = true` on a gate makes it a
  checkpoint the operator clears with `ddflow approve`, before the compute is spent.

  **The design property that matters:** a human checkpoint reachable from the MCP
  surface is not a human checkpoint, it is a second `gate record` with a longer name.
  So there is no MCP tool, `gate record` and `gate skip` both refuse (exit 3 —
  coordination, not failure: nothing is broken, the caller is simply not the party who
  can clear it), and the refusal lives at the SERVICE boundary rather than in the CLI
  branch that happens to be the usual caller — a check in one surface is a check the
  other does not have.

  **Scoped honestly.** An audit trail and a speed bump, not a security boundary: an
  agent with shell access can run `ddflow approve` itself and nothing here prevents
  that. What is guaranteed is that the ordinary path is closed and that a clearance
  carries the OS user and a `human` flag, so a forged one is visible rather than
  identical to a real one. Saying more would be the overclaim this project keeps
  finding in its own docstrings.

  Rejection is a first-class outcome with a mandatory reason, because "looked and said
  no" and "nobody has looked" are different states and the second is a silent stall.
  Opt-in: the shipped pipeline has no human gate and a test keeps it that way.

  **The test for the headline property was vacuous twice before it worked.** First
  version asserted end-state after looping every gate tool — `ddflow_gate_record`
  cleared the gate under mutation and `ddflow_gate_skip` then overwrote the outcome
  with `skipped`, so the final read never saw it. A later write masking an earlier one
  collapses a whole loop of attempts into one observation. Now asserted after EVERY
  call, and mutation-verified red.

  Found while fixing it: `gate verify` called a human gate "an agent gate", which
  carries the wrong advice — an agent gate's honesty rests on the evidence contract,
  a human gate's rests on a person having looked, and no mutation can demonstrate
  the latter.

  *From `pimzino/spec-workflow-mcp`, the best idea in either rival server (R13). Their
  review DASHBOARD is declined for now and stays filed: it is a separate surface with
  its own security story, and the gate is the part that changes behaviour.*

## B108 — "ask the operator first" gets something behind it, 2026-09-25

- **B108. ✅ CLOSED.** Operator question: *"how will `ddflow companions` work in MCP
  mode — would it ask the agent to ask the user for permission?"* Answering it honestly
  exposed an inconsistency.

  What was true: `ddflow_companions` is read-only and installs nothing; installation
  requires the agent to shell out, which hits the harness's own permission prompt, not
  ddflow's. `ddflow_companions_add` writes only a repo-local config, merges rather than
  overwrites, and cannot register something uninstalled (no `--force` over MCP).

  What was NOT true: that the agent asks first. The handshake *instructed* it to, and
  that instruction had nothing behind it — the agent could describe the change in its
  own words, or make it and report afterwards. Neither is the operator seeing what will
  be written. B82 had just been built on the principle that a human checkpoint
  reachable from the MCP surface is not a human checkpoint; this was the same class at
  a lower stake, left on prompt-level trust.

  **Operator's call** (asked, three options offered): add `dry_run` rather than removing
  the tool from the MCP surface — the operation is small, reversible and repo-local, so
  the agent keeps it and gains a way to make "ask first" actionable. Matches the
  existing `ddflow_workflow_gate` pattern rather than inventing a concept.

  The dry run creates NOTHING — not the file, not its parent directory; a dry run that
  mkdirs has changed the machine. A test asserts the preview matches what the real
  write produces, because a preview that drifts from the write is worse than no preview:
  the operator has now signed off on it. Mutation-verified: disabling it turns five
  tests red.

  Also: the tool's description now opens with **WRITES**, per the MCP spec's guidance
  that clients must treat annotations as untrusted — the safety has to be in text the
  model actually reads.

## B109–B112 — remote / filesystem-independent operation, 2026-09-25

From the operator's question about running the MCP server remotely or in a container
without direct filesystem access. Research and verified probes: `docs/RESEARCH.md` R14.
**Filed, not started** — the capability split is a product decision, not a refactor.

- **B109. A storage seam behind `EventLog` and `tomlcfg`. FILED.** There is no storage
  abstraction of any kind today (`grep` for `Protocol|ABC|abstractmethod|Backend` across
  the package returns nothing). Two genuine choke points exist — `infra/log.py` for
  events, `infra/tomlcfg.py` for TOML — and most services already go through them.
  Introducing a backend interface there, with the filesystem as the default
  implementation and an in-memory one for tests, is worth doing **on its own merits**
  (test isolation, no tmpdir per case) regardless of whether remote ever ships. It does
  NOT make ddflow remote; see B111 for why.

- **B110. The scattered writers. FILED, blocks B109's usefulness.** Seven modules
  bypass both choke points for one-off writes — `adopt` (AGENTS.md merges), `enforce`
  (git hooks), `sessions` (reconstruction output), `companions` (probe cache, MCP config
  merges), `gates` (gate-script patching), `cli` (`.gitignore`, `.gitattributes`,
  `config.toml` at init, prompt export) and `views/markdown`. Plus `infra/store.py`,
  which is a third independent I/O implementation with its own atomic-publish rather
  than reusing `tomlcfg.atomic_write`'s. A seam that only half the writers respect is a
  seam that reports the wrong answer about what is portable.

- **B111. The git coupling is the real blocker, and it is not storage. FILED,
  THEORETICAL.** `cli.py:216` writes `.ddflow/events/*.jsonl merge=union` into
  `.gitattributes`, so **concurrent-branch safety is delegated to git's own union merge
  driver**: two agents on two branches append to their own shards and git unions them
  with no conflict. That is the conflict-resolution strategy, not an implementation
  detail, and it exists only because the log is a file in the repo. On top of it:
  `fcntl.flock` is POSIX single-machine (`log.py:197`, `tomlcfg.py:114`); gate and
  reviewer commands run `shell=True` against a local worktree (`gates.py:776`,
  `review.py:485`, `review.py:782`); every git call is `git -C <local-path>`. A remote
  backend would have to REPLACE the merge strategy, not just the storage — and would
  still leave worktrees, merge and tree-fingerprint evidence needing the real checkout.

- **B112. The honest split, if remote is ever wanted. FILED.** Not a port of the current
  server — two halves. **Remote-capable:** the queue as pure data (items, dependencies,
  gates, lessons, decisions, research, recall), no git. **Local-required:** worktrees,
  merge, tree-fingerprint gate evidence, command gates. That is a shared team queue with
  local execution agents, which is a coherent and possibly better product, but it is a
  different one. Build deliberately or not at all.

**Already true, recorded so it is not re-investigated:** "containerised" is supported
today — `Dockerfile:4` documents `-v "$PWD:/repo"`, `infra/container.py` detects the
container, relocates worktrees inside the mount and rewrites `localhost` reviewer
endpoints to `host.docker.internal`, and `worktree.py:341-377` stores worktree paths
RELATIVE to the repo root so the log stays valid at a different absolute path. What is
unsupported is specifically *no filesystem at all*, not *not-the-host-machine*.

**DECLINED, with the probe that killed it:** delegating file I/O to the agent over MCP.
The spec has no primitive for it — `roots/list` hands the server `file://` URIs (the
protocol's model is server-does-I/O), `sampling` runs completions, and
`elicitation/create` is restricted to flat objects of primitives and forbidden from
carrying sensitive data. Beyond the protocol, delegating the EVENT LOG would make a
dropped agent write into silent data loss in the source of truth and destroy the
append-only and ordering guarantees. Config alone could be delegated — small, idempotent,
low-frequency — but config is not what pins the server to the filesystem.

## B113–B114 — roborev was registered as an MCP server it is not, 2026-09-25

- **B113. The flagship companion shipped a launch command that does not exist. ✅
  CLOSED.** `roborev` was `kind = "mcp"` with `args = ["mcp"]`. Probed:

      $ roborev mcp
      Error: unknown command "mcp" for "roborev"

  So `ddflow companions add --id roborev` wrote `{"command": "roborev", "args":
  ["mcp"]}` into the operator's `.mcp.json`, and the agent spawning it got that instead
  of a handshake — **precisely the failure the `kind` field was added to prevent (B79),
  in the entry that motivated the registry, added by the same change.** roborev is a
  command-line tool: the agent shells out to `roborev review <sha>` and `roborev
  analyze duplication`, which is how this project has used it all session.

  Why it survived: `detect` was `roborev --version`, which proves the BINARY exists and
  says nothing about whether the LAUNCH works. Detection and launch were different code
  paths and only one was ever exercised. A narrow ratchet now catches the
  same-binary-different-subcommand case; the general case is not mechanisable (nothing
  static can tell whether a binary speaks JSON-RPC), so the registry header carries the
  instruction to LAUNCH an entry before marking it `mcp`.

  *Found by the operator asking "is roborev an MCP or a tool or both?" — a question,
  not a bug report. Two of this session's sharpest findings came from questions.*

  Note also: the ratchet's FIRST version keyed on the first non-flag argument and went
  red on `codeguide`, which is correct (`docker run <image>` probed by `docker image
  inspect <image>`) — and its docstring claimed a carve-out the code had not
  implemented. Fifth instance this session of a comment promising what the code does
  not do, this one self-inflicted and caught by the check firing on a good entry. It
  keys on the LAST non-flag argument now: the thing being launched, not the runner's
  subcommand.

- **B114. `ddflow companions --verify` — launch it and check it speaks MCP. FILED.**
  The sound version of the check above: for each `kind = "mcp"` companion, spawn
  `command args`, send `initialize`, and require a JSON-RPC response. That is the only
  thing that actually distinguishes a server from a binary with a plausible name, and
  it is what would have caught B113 at the moment the entry was written rather than
  when an operator asked a question. Opt-in and never on the scan path — it spawns
  processes, and `ddflow companions` is called by the MCP handshake.

## B115–B117 — ddflow creates a worktree the agent is already standing in, 2026-09-25

Operator challenge: *"wouldn't the agent manage worktrees, merge, branches, and we only
orchestrate them? Why do this ourselves instead of instructing the agent and messing
with the user's or agent's work?"* Largely correct, and demonstrated.

- **B115. `claim` created a rival worktree when the caller was already in one. ✅
  CLOSED 2026-09-25.** `W.current()` detects it (`--show-toplevel` differs from
  `repo_root` inside a linked worktree — no name conventions to defeat), `claim` adopts
  the tree and branch, and the event is `worktree.adopted` so `remove_on_merge` never
  deletes a tree ddflow did not make. A tree already bound to another OPEN item is
  refused; a settled item's tree can be reused. Knob: `worktree.adopt_existing`.
  Mutation-verified. Original finding: Probe — the agent's harness has already isolated it, as Claude Code and
  Cursor both do:

      $ git worktree add ../wtclash-agent -b agent-branch
      $ ddflow --repo /tmp/wtclash-agent claim T1
        worktree: /tmp/.ddflow-worktrees/T1
        branch:   ddflow/T1 (from main)
        cd there and work.

      $ git worktree list
      /tmp/wtclash               [main]
      /tmp/.ddflow-worktrees/T1  [ddflow/T1]     <- ddflow's
      /tmp/wtclash-agent         [agent-branch]  <- where the agent IS

  Uncommitted work in the agent's tree is stranded and one item now has two branches.
  ddflow correctly resolves the repo root through `--git-common-dir`, so its STATE is
  shared — it is only worktree CREATION that collides.

  **Fix: adopt, do not create.** When the caller is in a non-primary worktree, bind the
  item to that path and branch and record it, rather than making a rival. ddflow still
  gets what it actually needs (a recorded tree it can find after a crash); the agent
  stays where its harness put it. An explicit `--worktree <path>` covers the case where
  the agent wants to name one.

- **B116. Separate what is needed from what is done. FILED.** Reading the claim path,
  three things are bundled that are not equally justified:
  **coordination** does not need it (`L.acquire` runs BEFORE the worktree and
  `--no-worktree` / `worktree.enabled=false` already skip creation with everything else
  intact); **recording** does need it, but being TOLD satisfies that as well as
  creating — `recover` needs to know the tree, not to have made it; **creating** is
  pure convenience and is the colliding part; **merging** genuinely belongs in the tool,
  because `ddflow merge` merges from the primary WITHOUT a `git checkout` there, and a
  checkout in the primary disrupts every other agent.

  The justification that survives is determinism plus recoverability, and it justifies
  recording, not creating: a crashed agent is precisely the one that never reported
  back, so a purely instruct-and-report design loses exactly the work `recover` exists
  to find. Adoption keeps the record and drops the collision.

- **B117. ✅ CLOSED 2026-09-25** by `infra/worktree.current()`, and `Ctx.called_from`
  keeps the caller's pre-resolution path — `self.repo` is deliberately the primary (that
  is what makes every worktree share one log) and resolving lost the one fact `claim`
  needed. Original finding: `worktree.enabled` is a project-wide switch; there is no
  per-invocation notion of "the caller is already isolated". `infra/worktree.repo_root`
  resolves a worktree to its primary via `--git-common-dir`, which is what B115 needs to
  detect the case — but nothing consults it on the claim path. Related: this connects to
  R14's local-vs-remote split, since adoption moves ddflow one step further from needing
  to CREATE anything on the filesystem, leaving `merge` as the main remaining git
  operation it performs itself.

## B36/B37 — the migration starts, 2026-09-25

Not closed. Recorded so the runway is legible rather than rediscovered.

- **B37 pattern established.** `api.loops()` returns one `Outcome`; `cmd_loops` renders
  the human view FROM it and `ddflow_loops` returns its `data`. Before, each surface
  computed its own view of the same answer — the shape that printed a coverage gap to
  humans only, invisible to the agent reading JSON that most needed it.
  `test_a_migrated_tool_gives_both_surfaces_the_SAME_data` pins it, mutation-verified
  by making the api's data diverge from what the CLI prints.

  Two things the first migration taught, worth applying to the rest:

  1. **Do not re-detect in the surface.** The first version called `PR.detect` again to
     get objects with a `render()` method, turning one O(events) fold into two on the
     layer whose only job is to render what was already computed. `LoopFinding(**d)`
     reconstructs from the dicts the Outcome already carries.
  2. **Inherit the exit contract, do not redesign it.** `loops` returns 1 on findings
     and 2 on none. "Loops found" arguably is not a failure, but callers branch on it
     and a silent renumbering during a refactor is worse than the imperfection.
     `test_a_migrated_tool_keeps_its_exit_contract` holds it.

- **B36 remains open and is still moving the wrong way** — `cli.py` was 3,041 lines when
  filed, 4,208 before this migration. One tool moved; `ARGV_TOOLS_CEILING` is 61, down
  from 62, and the ratchet only ever allows it to fall. The read-only reporting family
  (`status`, `doctor`, `progress`, `board`, `rebuild`, `cleanup`) is the natural next
  batch: no writes, and each already builds a JSON payload an `Outcome` can carry
  directly.

## B118–B126 — roborev on the human-gate / dry-run / roborev-kind commit, 2026-09-25

Nine findings on `137f362`, all applied. The theme again: **the new refusal shipped
without updating the surfaces that TELL an agent what to do**, so the tool instructed
the agent to run the command it would then refuse.

- **B118. `approve` was the only gate-writing path with no existence check. ✅ CLOSED.**
  Every sibling calls `_require_item`; `cmd_approve` went straight to `G.record`, and
  `_h_gate` folds through `_item`, which CREATES an item for an unknown subject. So:

      $ ddflow approve TYPO-NOT-REAL plan_approved --note "read it"
      TYPO-NOT-REAL.plan_approved approved by delian — read it     # exit 0

      items in projection: ['T1', 'TYPO-NOT-REAL']
      T1 approved?       :            <- the one the operator meant
      phantom approved?  : passed

  A phantom task carrying a human approval, while the real item stayed unapproved — in
  the one command whose entire value is that a person looked at a SPECIFIC thing.

- **B119. Three surfaces still directed the agent to the refused commands. ✅ CLOSED.**
  `gate_instruction.md` printed `ddflow gate record …` for a human gate (no branch on
  kind); the `require_outcome` blocker suggested `gate run|record` and `gate skip`, all
  three of which refuse; and `workflow.describe` computed `"command" if is_command_gate
  else "agent"`, so `ddflow_workflow` — the tool an agent asks "what is the pipeline
  here?" — reported it as an agent gate. The same mislabel fixed in `gates.verify` and
  left in the three places an agent actually reads. `_gate_kind()` is now one
  definition with four values.

- **B120. The "impossible through the MCP surface" claim was FALSE. ✅ CLOSED.** Probed:
  `ddflow_configure` with `set="gate.plan_approved.human", value="false"`, then
  `ddflow_gate_record` — two calls, no shell, gate cleared. `Config.check` skips
  `[gate.*]` as a foreign table and `human` is a real `GateDef` field, so the write
  sailed through. Closed by refusing `gate.<id>.human` in `_write_config` (whether a
  checkpoint belongs to the operator is not a configurable preference), AND the claim
  is narrowed in all three places it appeared to what actually holds: *no MCP tool
  records a human outcome*. The broad version was the overclaim this project keeps
  catching in other people's docstrings.

- **B121. The dry run was a second implementation of the write. ✅ CLOSED.** They had
  already diverged: the TOML preview omitted the leading newline the write prepends,
  and the JSON preview reported "WOULD add" over a file the write would REFUSE as
  unparseable — signing the operator off on a change that could not happen, which is
  the failure a preview exists to prevent. One decision now, both paths, and the JSON
  preview shows the MERGED result rather than a lone entry.

- **B122. `test_the_dry_run_preserves_an_existing_config_in_the_preview` never ran a
  dry run. ✅ CLOSED.** It wrote a config, did a REAL add, and asserted the write
  merged — while its docstring described the misleading-preview property it did not
  check. The one test whose name covered that case passed regardless of the preview.
  **Fifth vacuous test of this session, and the sixth docstring describing behaviour
  the code did not have.** Now parametrised over both the JSON and TOML targets, plus
  a case asserting a preview never promises a write that would be declined.

- **B123. `gate verify` on a human gate returned FAIL. ✅ CLOSED.** The same commit
  argued — correctly, in its own comment — that a human gate is a coordination refusal
  and changed `gate record` to exit 3 for exactly that reason, then left `_gate_verify`
  collapsing "there is nothing a mutation could demonstrate" into "this is broken". The
  test asserted only the message, so the drift was unpinned.

- **B124. The README sample contradicted the two sections around it. ✅ CLOSED.** It
  still showed roborev as an unregistered MCP server offering `ddflow companions add
  --id roborev`, a command that now exits 3, twenty lines above a table calling it
  *(cli)*. Re-rendered from the cli branch, with `codeguide` taking over the
  installed-but-not-wired-up example the paragraph goes on to explain.

- **B125. Two backlog pointers resolved to the wrong entry. ✅ CLOSED.** The registry
  header and a test both said `ddflow companions --verify` is "filed as B113"; it is
  B114, and B113 is the roborev fix itself.

- **B126. Test hygiene. ✅ CLOSED.** Leftover `print(..., file=stderr)` debugging firing
  on every green run, and an assertion `"WOULD add" in text or "applied" in text` whose
  second clause is in every `--json` payload and therefore could not fail.

Mutation-verified: reverting the two behavioural guards (the item check and the config
refusal) turns three of the new tests red.
