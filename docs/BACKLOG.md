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

- **B28. The parallelism cap counts leases, not worktrees.** `plan()` now measures
  in-flight work from live leases across the whole queue rather than from the filtered
  candidate list. A lease taken with `--no-worktree` therefore consumes a slot from
  `worktree.max_parallel`, which is a statement about disk and CPU. Over-counting is the
  safe direction and the two knobs are separately configurable, so this is a precision
  gap rather than a defect. *Found by: the cap fix.*

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

- **B34. `orchard history` does not exist as one view.** `status`, `progress`, `replay`
  and `recall` each answer part of "what has happened here", and an operator asking that
  question has to know which to run. A single reverse-chronological timeline over the
  log, filterable by item and kind, would subsume the common case.

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
