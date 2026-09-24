# Research log

Every entry carries a **verdict**: `CONFIRMED`, `REFUTED` or `THEORETICAL`. A
`CONFIRMED` or `REFUTED` verdict must be backed by a probe whose command **and output**
appear here. `THEORETICAL` must say why no probe was possible. An entry with no verdict
is a literature summary, not research.

**Probe budget for this pass: ≤ 60 minutes wall-clock, 0 GPU-hours.** Honoured.

---

## R1 — Can SQLite be the source of truth on an NFS checkout?

**Question.** The motivating project's repository lives on NFS, and several agents on
possibly several machines share it. Conventional wisdom is that SQLite over NFS is
unsafe and that `O_EXCL` is unreliable there. Is the conventional answer right *on this
hardware*, and if so what survives?

**Claim.** SQLite WAL and POSIX locking are unsafe on this mount; a file-based scheme
using `link(2)` is required.

**Falsifier.** A concurrent-writer benchmark on the actual mount showing zero lost
updates and zero errors for SQLite/`flock`/`O_EXCL`.

**Probe.** `probes/probe_01_nfs_lock_primitives.py` — 12 processes × 40 increments,
barrier-synchronised, five primitives, run against the NFS repo path and `/tmp` (ext4)
as a control.

```console
$ python3 probes/probe_01_nfs_lock_primitives.py "$PWD/.probe-nfs" /tmp/probe/ext

=== NFS (repo): .../bridge-cse.../.probe-nfs ===
  sqlite[wal     ] committed= 480 errors=  0 counter= 480 LOST=0
  sqlite[truncate] committed= 480 errors=  0 counter= 480 LOST=0
  flock              acquired= 480 errors=  0 counter= 480 LOST=0
  oexcl              winners=  40 (must be EXACTLY 40) -> OK
  link               winners=  40 (must be EXACTLY 40) -> OK
  (arm wall-clock 65.0s)

=== ext (/tmp): /tmp/probe/ext ===
  sqlite[wal     ] committed= 480 errors=  0 counter= 480 LOST=0
  sqlite[truncate] committed= 480 errors=  0 counter= 480 LOST=0
  flock              acquired= 480 errors=  0 counter= 480 LOST=0
  oexcl              winners=  40 -> OK
  link               winners=  40 -> OK
  (arm wall-clock 1.8s)

$ findmnt -T . -o FSTYPE,OPTIONS --noheadings
nfs4   rw,relatime,vers=4.2,...,hard,proto=tcp,timeo=600,...,local_lock=none,...
```

**Verdict: REFUTED** — the claim was wrong about *correctness* and right about *cost*.

Every primitive is correct on this mount. The reason is visible in the mount options:
`vers=4.2` with `local_lock=none`, so locking goes to the server and NFSv4's stateful
`OPEN` provides the exclusive-create semantics NFSv3 lacked. The conventional warning is
about NFSv3, and repeating it here would have been cargo-cult.

But the control arm is the finding that actually shaped the design: **NFS is ~36× slower
than local** (65.0 s vs 1.8 s). So correctness permits SQLite-on-NFS while performance
forbids putting it on the hot path.

**What this changed.** The design takes the third option: the *log* is authoritative and
is appended to under a `flock` (a few hundred bytes, one lock, one `fsync`), while
SQLite is a **derived, gitignored, rebuildable index** that can live anywhere and be
thrown away. The property that survives is portability — a design that depended on NFSv4
semantics would silently break on NFSv3, whereas `flock`-around-append plus
content-addressed dedup is correct even where locking is advisory-only, because the
content hash makes a duplicated append idempotent.

**Source of the conventional claim:** [SQLite, *How To Corrupt An SQLite Database
File*, §2.1 "Filesystems with broken or missing lock implementations"](https://www.sqlite.org/howtocorrupt.html)
— "if the locking primitives do not work correctly, then two processes can write to the
database at the same time". Opened and read; it is a statement about broken lock
implementations, not about NFS per se, which is what the probe went on to test.

---

## R2 — Should the source of truth be a database, markdown, or an event log?

**Question.** Three designs are viable. Which one fails least badly?

**Candidates and their measured failure modes:**

| Design | Used by | The failure it cannot avoid |
|---|---|---|
| Markdown as source of truth | the originating project | drift — an audit script exists solely to reconcile checkboxes against commits, and found ~170 of 269 unchecked items were already shipped |
| SQLite as source of truth | TaskMaster | a binary file in git; two branches cannot be merged |
| Append-only event log, everything else derived | ActiveGraph | replay cost; no human-editable surface |

**Claim.** An append-only log sharded per agent has no merge conflicts *by construction*,
and this is worth its replay cost.

**Falsifier.** A replay cost that makes ordinary commands slow, or a merge that conflicts
anyway.

**Probe.** `probes/probe_02_fold_throughput_and_merge.py` — folds 20 000 synthetic
events, then creates two real branches that each append concurrently and merges them.

```console
$ python3 probes/probe_02_fold_throughput_and_merge.py
(a) folded 20000 events in 0.049s  (407,926 events/s), 11111 items
(b) merge exit=0: Merge made by the 'ort' strategy.
    conflicted files: (none)
    after rebuild, both branches' tasks present: ['X1', 'X2']
```

**Verdict: CONFIRMED.** Two agents on two branches touch two different shard files, so
git has nothing to conflict over — the merge is clean with no manual resolution, and
re-folding the union yields both branches' work.

Replay is also far cheaper than assumed: **407,926 events/s**, so a 20 000-event project
folds in 49 ms — well under the cost of the single `git status` that surrounds it. (My
pre-probe estimate was 47,600 events/s, low by 8.5×; the estimate was never load-bearing
because the SQLite index means the common read path does not fold at all, but it is
recorded here because an unprobed number that happens to be conservative is still an
unprobed number.)

**Residual risk, accepted and mitigated:** a log that grows without bound eventually
makes `rebuild` slow. At the measured rate 1 M events fold in ~2.5 s, so the ceiling is
far away. The `log.compacted` event kind is reserved for a compaction path, which must
preserve every `PROVENANCE_KINDS` event — those are the reconstruction input. Not
implemented, because no project is near that scale and a compactor written against an
imagined workload compacts the wrong thing.

**Source:** [Sanders et al., *The Log is the Agent: Event-Sourced Reactive Graphs for
Auditable, Forkable Agentic Systems*, arXiv:2605.21997](https://arxiv.org/abs/2605.21997).
Opened. The line taken: "the append-only event log is the source of truth; the working
graph is a deterministic projection of that log", and its determinism contract —
replay is made sound by *recording* model responses rather than assuming they reproduce.
That caveat is why `orchard replay` reconstructs the decision history and says plainly
that it does not reproduce the source.

---

## R3 — Is MCP sufficient to make this agent-agnostic?

**Claim.** Shipping only an MCP server makes the workflow portable across agents.

**Falsifier.** Any target agent that cannot consume MCP, or any workflow step that MCP
cannot express.

**Probe.** Checked what each target actually reads, and whether a non-MCP path is needed.

```console
$ # Instruction files each agent loads (from each project's own docs):
  Codex, Copilot, Cursor, Gemini CLI, Jules, Aider, Zed, Windsurf, Devin -> AGENTS.md
  Claude Code                                                            -> CLAUDE.md
$ # MCP transport support: all of the above support stdio MCP servers.
$ # But: CI jobs, Makefiles, git hooks and humans consume none of it.
```

**Verdict: REFUTED as stated, CONFIRMED in weakened form.** MCP is sufficient for *agents*
but not for the workflow, because a meaningful share of the steps (a pre-commit gate, a
CI check, an operator inspecting a crashed worktree at 2 a.m.) have no MCP client.

**What this changed.** Orchard ships **both** surfaces over one implementation: the CLI is
primary and complete, and `mcp_server.py` maps each tool onto the same `cli.main()` call
in-process. Two tests pin that they cannot diverge
(`test_mcp.py::test_a_tool_call_returns_the_cli_result`, and the demo's
"both doors must give the SAME answer" step comparing board and scheduler output).

The MCP SDK was **not** taken as a dependency: the stdio transport is newline-delimited
JSON-RPC 2.0, roughly 200 lines, and a portability tool that only installs where a
package index is reachable is not portable. `python3` and `git` are the entire runtime.

**Source:** [AGENTS.md](https://agents.md/) — the cross-tool instruction format, donated
to the Linux Foundation's Agentic AI Foundation in December 2025, read by 30+ agents.
This is why `orchard adopt` writes `AGENTS.md` and only *points* `CLAUDE.md` at it.

---

## R4 — Retrieval: full-text, embeddings, or both?

**Claim.** Lesson retrieval needs embeddings; BM25 will miss paraphrases.

**Falsifier.** BM25 ranking the right lesson first for queries sharing no keyword with it.

**Probe.** Three paraphrased queries against a seeded corpus, FTS5 BM25 only.

```console
q='reviewer unavailable clean'   -> 'Never treat an unavailable reviewer as a passing review'
q='empty list assertion'         -> 'Empty collections make assertions vacuously true'
q='parallel agent stash'         -> 'Git worktrees must not share a stash stack'

$ # and from the polyglot demo, with NO shared keyword at all:
q='cutting a url slug short leaves a dangling hyphen'
  -> 'Truncating a slug can leave a trailing separator'
```

**Verdict: REFUTED for this corpus size.** BM25 with a `porter` stemmer ranked the
correct lesson first in every case, including the last, where "cutting/short/dangling
hyphen" shares no stem with "truncating/trailing separator" except via the stemmer and
the co-occurring domain terms.

The honest reading: lesson corpora are small (hundreds of entries, not millions) and
lessons are *written* with their trigger words in them, which is the regime BM25 is
strongest in. Embeddings would add a model dependency, a download, and an index to keep
warm, for a ranking improvement this probe could not detect.

**Accepted limit:** a genuinely synonym-only query ("automobile" for "car") will miss.
The mitigation is a documented one-line convention — write the rule's trigger words into
the title — not a vector database. `lessons.search_backend` exists so a future project
can switch without a code change.

**Source:** [Willison, *Hybrid full-text search and vector search with
SQLite*](https://simonwillison.net/2024/Oct/4/hybrid-full-text-search-and-vector-search-with-sqlite/)
— opened; the RRF hybrid pattern it describes is what `search()` would grow into if the
limit above ever bites.

---

## R5 — Should an expired lease be reclaimed automatically?

**Claim.** Auto-reclaiming an expired lease is safe, because a crashed agent's worktree
holds nothing worth keeping.

**Falsifier.** Any case where a crashed agent's worktree contained work existing nowhere
else.

**Probe.** The originating project's own history, plus a constructed scenario.

```console
$ # From that project's operational memory, verbatim:
  "A killed session leaves FINISHED work uncommitted in its worktree (found
   2026-08-15, work recovered on 08-17, commit f9c9f44b, existing nowhere else)."
$ # And the counter-case, from the same source:
  "A dirty agent worktree is NOT automatically unshipped work: all 3 dirty
   worktrees held EARLIER drafts of phases already in master."
```

**Verdict: REFUTED.** Both records are true at once, which is exactly why the decision
cannot be automated: a dirty worktree is sometimes irreplaceable and sometimes garbage,
and nothing in the metadata distinguishes them. Only a diff does.

**What this changed.** `lease.reclaim_policy` defaults to `report`. `orchard recover`
*measures* each tree (uncommitted files, unmerged commits) and prints the exact `git
diff` command, but never deletes and never steals. `recover --apply` expires only trees
it measured as empty. Pinned by
`test_lease_and_recovery.py::test_sweep_never_touches_salvageable_work` and by the
crash-recovery demo scenario.

---

## R7 — Distribution: vendored scripts, or a published MCP package?

**Question (operator, 2026-09-24).** "Make the project management code portable, using
MCP... everything auto-installable... publish and register the MCP so they can be
downloaded and installed automatically — so every project needs only a minimal startup
language description."

**Claim.** Shipping as a published package invoked by `uvx` removes every adoption step
except one line of MCP config, and shrinks the per-project instruction text enough to
matter.

**Falsifier.** Any adoption step that survives; or a per-project text that does not
actually get shorter.

**Probe.** Built the wheel, installed it into a clean venv, adopted a fresh repo, and
measured the resulting artefacts.

```console
$ uv build && pip install dist/orchard_mcp-0.1.0-py3-none-any.whl
$ cd /tmp/fresh-repo && orchard adopt --agents cursor
  wrote docs/orchard/drivers/implement-phase.md
  registered orchard in .cursor/mcp.json
  wrote .cursor/rules/orchard.mdc (always-applied project rule)

$ cat .cursor/mcp.json
{ "mcpServers": { "orchard": { "command": "uvx", "args": ["orchard-mcp"] } } }

$ # the ENTIRE per-project instruction text:
$ awk '/ORCHARD:BEGIN/,/ORCHARD:END/' AGENTS.md | wc -w
232
```

**Verdict: CONFIRMED.** Adoption is one line of MCP config; `uvx` fetches and runs the
package on first use, so there is no clone, no virtualenv, no `PYTHONPATH` and no
install step to forget. The per-project text is **232 words**, because the MCP tool
descriptions already carry the how — and a second copy of that in every project is a
copy that drifts from the one the model reads at call time.

**A defect this probe caught that nothing else could.** `templates/` lived BESIDE the
package, so `adopt` worked perfectly from a source checkout and raised
`FileNotFoundError` for every installed user. A source tree is exactly where that bug is
invisible. Fixed by moving templates inside the package; pinned by
`tests/test_packaging.py`, which builds the real wheel and looks inside it.
Mutation-verified by moving the directory back out (2 tests red).

**Zero runtime dependencies is load-bearing, not minimalism for its own sake.** It is
what lets the server install inside a sandbox with no reachable package index, a CI
image, or another tool's ephemeral container. `test_the_package_has_no_runtime_dependencies`
fails if one creeps in.

**Registry.** `server.json` follows the
[MCP registry schema](https://modelcontextprotocol.io/registry/quickstart)
(`io.github.OWNER/orchard`, PyPI `orchard-mcp`, `runtimeHint: uvx`), published by
`.github/workflows/publish.yml` on a version tag via OIDC trusted publishing — no stored
tokens. The workflow refuses when tag, `pyproject.toml` and `server.json` disagree about
the version; `test_the_declared_versions_agree` pins the same invariant locally.

---

## R8 — Can a local model serve as the cross-family critic?

**Question (operator, 2026-09-24).** "Maybe locally there is a QWEN model running, can
you check if you can use it as cross critic?"

**Probe — tier 0, existence check first.**

```console
$ ss -ltnp | grep :8000
LISTEN 0 4096 0.0.0.0:8000 ...
$ curl -s http://127.0.0.1:8000/v1/models
{"object":"list","data":[{"id":"Qwen/Qwen3.8-Flash-Next-FP8","max_model_len":262144,...}]}
$ nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
23335, VLLM::Worker_TP0, 123190 MiB      # TP4, ~123 GB/worker
```

**Verdict: CONFIRMED — and it is genuinely cross-family.** Qwen is Alibaba-pretrained;
the author here is Anthropic. That is the only property this reviewer is selected for.

**But the first real run returned UNAVAILABLE, and the failure is worth recording**,
because it is the exact shape this project is built to refuse to paper over:

```console
=== UNAVAILABLE === 0/5 chunks reviewed, 5 off-contract in 201s
reason: chunk 1: empty completion
```

**Diagnosis, measured on one 30 KB chunk:**

| `max_tokens` | `finish_reason` | content chars | reasoning tokens | wall |
|---|---|---|---|---|
| 6 000 | `length` | **0** | 6 000 | 39 s |
| 32 000 | `stop` | 2 396 (valid verdict) | 28 381 | 190 s |
| 4 000, `enable_thinking=false` | `stop` | 18 (`STATUS: FINDINGS 1`, no findings body) | 0 | 0.6 s |

A **reasoning model spends the token budget thinking before it emits anything**, so a
budget sized for the answer alone yields a truncated reply with an *empty content field*.
Disabling thinking is fast and useless: a verdict with no findings behind it.

**What this changed.** `[[reviewer]].max_tokens` now defaults to 32000 and
`max_chunk_chars` to 30000, both with the measurement in the comment; and a truncated
completion is reported as `TRUNCATED: the model consumed all N tokens (M of them
reasoning) before emitting any answer — raise max_tokens or lower max_chunk_chars`
rather than as "empty completion", which sends an operator hunting a healthy endpoint.

**The part worth keeping:** at no point did the pipeline report a pass. An endpoint that
was up, answering, and burning 6 000 tokens per request still produced `UNAVAILABLE,
5/5 off-contract` — because length is not a verdict, and the absence of a `STATUS:`
block is the absence of a review. That is the failure mode this system exists to make
loud, encountered against itself.

---

## R9 — Does the orchestration actually work end to end over MCP?

**Question (operator, 2026-09-24).** "Create a simulated project and test the tool as
MCP and verify the orchestration end to end."

**Claim.** The pieces are individually tested, so the whole works.

**Falsifier.** Any defect that only appears when the pieces are composed.

**Probe.** `demos/scenario_mcp_orchestration.py` — an invented two-phase Python library
(`taskmetrics`: duration parsing, statistics, a CLI over both) built by two agents, each
a separate MCP client process, **entirely over JSON-RPC with no CLI call at all**. 24
steps, 57 assertions, ~4 minutes: bootstrap an unadopted repo, configure it, discover a
local reviewer, fill a two-phase queue, fan out, get refused by the enforcement hook,
run real pytest suites, run the real cross-family critic, merge, close both phases, and
reconstruct from the log.

**Verdict: REFUTED, decisively.** The composed run found **eight defects that 213 unit
tests and four existing scenarios did not**, and two of them made core features useless
out of the box:

| # | Defect | Why the unit tests missed it |
|---|---|---|
| 1 | **The default agent identity was `{host}-{pid}`**, stable for exactly one process. So `orchard claim` and the `git commit` hook seconds later were different agents: the hook **refused the holder's own commit and told them their lease belonged to somebody else**. Out of the box, enforcement rejected correct behaviour and blamed the user. | Every enforcement test passed `agent=` explicitly. The default path was never exercised. |
| 2 | **Merely starting the MCP server created `.orchard/events/`**, so a handshake wrote to any repository an agent connected to — and the "is this project adopted?" check then answered yes about a directory the server had just created itself. | No test asked whether a read-only operation mutated the repo. |
| 3 | **A JSON-RPC notification deadlocked the client.** `notifications/initialized` correctly gets no reply; a client that reads one anyway blocks forever while both processes sit at 0% CPU looking healthy. | The server was right and tested; nothing had ever *sent* a notification. |
| 4 | **The coverage gap was suppressed in JSON mode.** "gate X never ran" printed only for humans, so an agent over MCP — always JSON — completed an item and was never told a gate had not run. | The human path was asserted; the JSON payload was not. |
| 5 | **Four CLI commands had no MCP tool** (`show`, `update`, `release`, `block`). The canonical driver *instructs* the agent to run `orchard update --globs` before writing outside its claim — an instruction impossible to follow over MCP. | Nothing compared the two surfaces for completeness. |
| 6 | **No way to abandon or remove an item.** `item.abandoned`, `task.removed` and `phase.removed` were declared in the handler registry and handled by the fold, and nothing emitted any of them. A task created speculatively held its phase open **forever**, because completion counts any non-`done` task as unfinished and nothing could ever finish it. | A vocabulary with no way to say the words; no test tried to say them. |
| 7 | **`replay` dropped the phase/task BODY**, reducing a phase to "P1: Core" — the acceptance criteria and context, the part a rebuild most needs, were absent from the reconstruction brief. | The replay test used items with no body. |
| 8 | **The board counted abandoned tasks as outstanding**, so a completed phase rendered "3/4 tasks" — unfinished work that no longer exists. | Abandonment did not exist until #6 was fixed. |

**A ninth, corrected by probe rather than found by the scenario.** The scenario's merge
kept failing on a dirty `config.toml`, and the pre-check's stated reason was *"a merge
would mix them into the result"*. That premise is false:

```console
$ git merge --no-ff -m "merge feat" feat      # with an UNRELATED file dirty
exit=0 · Merge made by the 'ort' strategy.
  is my local edit still uncommitted?   M other.txt
  did it get into the merge commit?     0
$ git merge --no-ff -m "merge feat2" feat2    # branch touches the dirty file
exit=2 · error: Your local changes to the following files would be overwritten by merge:
	shared.txt
```

Git refuses precisely and only when the merge would overwrite a locally-modified file,
and names them. The blanket pre-check was both wrong in its reasoning and over-broad,
refusing safe merges — routinely including one blocked by Orchard's own freshly-written
config. Removed; git's own check is the better one.

**The pattern, stated plainly.** Every one of these lived in a *seam*: between two
processes (1, 3), between a read and a write (2), between two output surfaces (4, 5),
between a declared vocabulary and its callers (6), between a record and its projection
(7, 8). Unit tests exercise functions; seams only appear when the real pieces are
composed the way a user composes them. **The scenario is worth more than its assertion
count suggests, and "the parts are tested" is not evidence that the whole works.**

**Cost:** ~4 minutes per run, including a real cross-family review against a local
Qwen3.8-Flash-Next. Runs in the default `demos/run_all.py` sweep.

---

## R6 — Bugs this project found in itself

Each was found by a probe, fixed, and ships with a mutation-verified regression test.
They are recorded because the *classes* recur, not because these instances matter.

| # | Defect | Found by | Class | Regression test |
|---|---|---|---|---|
| 1 | A missing binary exits 127 under `shell=True` and was classified `failed`, so a tool that stopped being installed looked like a check that ran and found problems | bug-hunting `gates.py` | unavailable-as-failure | `test_gates.py::test_a_missing_tool_is_unavailable_not_failed` |
| 2 | `acquire` dropped `worktree`/`branch` when renewing an agent's own lease, so recovery reported **"nothing to salvage"** over a tree holding uncommitted work | crash-recovery demo | silent-knob-drop | `test_lease_and_recovery.py::test_recovery_never_says_nothing_to_salvage_over_real_work` |
| 3 | A legitimately *failing* command gate crashed the CLI, because `record` demands a reason and the command path supplied none | polyglot demo | error-path-untested | `test_cli.py::test_a_failing_command_gate_is_recorded_not_crashed` |
| 4 | `acquire` did not refuse an already-`done` item, so an agent with a stale snapshot re-did finished work — **50 claims for 32 tasks** | 8-process stress test | check-then-act on stale state | `test_lease_and_recovery.py::test_a_completed_item_cannot_be_reclaimed_by_a_stale_agent` |
| 5 | Untracked files made the primary checkout look dirty, so `merge` refused forever in any repo with a build directory | parallel-phase demo | over-broad precondition | covered by the demo; `worktree.dirty(untracked=False)` |
| 6 | The reconstruction brief named each rejected approach but omitted the probe **output** that killed it | reconstruct demo | evidence-dropped | `test_store_and_session.py::test_a_rejected_approach_carries_the_measurement_that_killed_it` |
| 7 | Auto-generated ids used `int(time.time())`, so anything created in the same second **silently overwrote** its predecessor — 7 lessons added, 2 survived | dead-knob probe | collision-by-timestamp | `test_cli.py::test_auto_generated_ids_do_not_collide_within_one_second` |
| 8 | Four documented knobs were **never read** by any code | `test_ratchet_no_dead_knobs.py` | dead config knob | the ratchet itself; allowlist may only shrink |
| 9 | `lease.py` had forked the git layer; its branch resolver fell back to the literal `"HEAD"`, so on a repo whose default branch is `trunk`/`develop` the unmerged count became `rev-list HEAD..HEAD` = 0 and recovery advised **deleting a worktree holding unmerged work** | roborev `analyze duplication` | duplicate-then-drift | `test_lease_and_recovery.py::test_recovery_is_correct_on_a_repo_whose_default_branch_is_not_main` |
| 10 | A stale `lease.renewed` from a **former** holder overwrote the current holder's worktree path — every destructive path then targets the wrong tree | adversarial rubber-duck | stale-event-applied-unchecked | `test_model_and_schedule.py::test_a_stale_renewal_cannot_hijack_the_current_holders_worktree` |
| 11 | `worktree.remove(force=False)` passed an empty-string argv element, so `git worktree remove` exited 129 — **the safe removal path could never succeed**, training everyone to pass `--force` | adversarial rubber-duck | argv-construction | `test_lease_and_recovery.py::test_the_safe_worktree_removal_path_actually_works` |
| 12 | `_measure` encoded measurement failure as `-1`, and `salvageable = dirty > 0 or unmerged > 0` collapsed **unknown into clean** — an unreadable worktree was reported "safe to remove" | roborev `analyze architecture` | three-valued-collapsed-to-two | `test_lease_and_recovery.py::test_an_unmeasurable_worktree_is_never_reported_as_safe_to_remove` |
| 13 | `lease._alternatives` had forked the readiness rules and ignored `unknown_dep_policy`, so a refused agent was told to take an item the scheduler would also refuse | roborev `analyze architecture` | third-copy-drift | `test_lease_and_recovery.py::test_suggested_alternatives_are_exactly_what_the_scheduler_would_offer` |
| 14 | `render.board` re-typed the ten default gate ids, so a project that trimmed its pipeline got ten columns under a caption naming gates it does not run | roborev `analyze duplication` | hardcoded-copy-of-config | `test_cli.py::test_the_board_renders_the_CONFIGURED_pipeline_not_a_hardcoded_one` |

**Reviewer accounting for this pass** — each named, none silently omitted:

| Reviewer | Family vs author | Status | Found |
|---|---|---|---|
| Self bug-hunt (dead-knob probe, id-collision probe) | same | ran | #1, #7, #8 |
| Demo scenarios (4, end-to-end) | n/a — executable | ran | #2, #3, #5, #6 |
| Stress test (8 processes) | n/a — executable | ran | #4 |
| Adversarial subagent rubber-duck | **same family (Anthropic)** — does NOT satisfy cross-family independence | ran, 2 confirmed / 3 refuted | #10, #11 |
| roborev `analyze duplication` + `analyze architecture` | same family (`claude-code`) | ran (jobs 753, 754) | #9, #12, #13, #14 |
| Cross-family critic (`deepseek` @ LAN vLLM) | different family | **UNAVAILABLE — endpoint returned HTTP 000; a concurrent session was sweeping it** | — |

**The cross-family gate is NOT satisfied for this work.** Recorded as `unavailable`, never as
a pass — which is the same rule this system enforces on its users, applied to itself. A
reviewer from a different pretraining family should review `orchard/` before it is
adopted for anything load-bearing.

**The pattern worth keeping:** defects 2, 3, 4 and 6 were invisible to unit tests and to
reading, and were all surfaced by *scenarios that used the system the way a user would*.
Defect 4 in particular does not reproduce below about six concurrent processes. Testing
the happy path of each function would have found none of them.

---

## R10 — the 2026-09-24 review pass: what a second adversarial reading found

**Question.** After 307 tests, five end-to-end scenarios, a cross-family critic run and
a roborev pass, what is left? Specifically: is the *requirements* surface as sound as
the *implementation* surface, or has the effort gone into making the code correct
against a specification nobody re-read?

**Budget.** Two adversarial subagents (one gap-audit against the operator's original
requirements, one bug-hunt with mandatory probes), plus one new end-to-end scenario
written deliberately to exercise the requirements rather than the code.

**Verdict: REFUTED.** The requirements surface was *not* as sound. **Twenty-two
defects** — fifteen from the two adversarial subagents and the new scenario, seven more
from roborev's duplication and architecture passes — of which the two most serious were
not code bugs at all but **features that were present, tested, documented and inert**.

### The two that matter most

**1. The phase dependency graph was decorative. CONFIRMED.**

`P2 needs P1` blocked `P2` — an item nobody claims, because phases complete when their
tasks do — and permitted every task *inside* P2, which is what an agent actually picks
up. The same hole appeared one level down once sub-tasks existed: an umbrella declaring
`needs A, B` had children with empty `needs`, handed out while A and B were open.

```console
$ orchard next
Ready (4 ready, 0 running, 2 blocked):
  P1.T1  money
  P1.T2  account
  P1.T3.a  posting rules        <-- its umbrella needs P1.T1 AND P1.T2
  P2.T4  summary                <-- its phase needs P1, which is 0/5 done
```

**Why it survived five scenarios and 307 tests.** The pre-existing end-to-end scenario
*did* assert "P2's task is withheld while P1 is open", and that assertion passed —
because the scenario declared `needs="P1"` **on the task by hand** as well as on the
phase. The test restated the thing under test as its own input, so it was true for a
reason that had nothing to do with the phase graph. This is the sharpest instance yet
of the rule that a test which supplies the property it is checking proves nothing;
`tests/test_inherited_deps.py` never re-declares an inherited dependency, and says so
in its docstring.

**2. `orchard claim` never looked at dependencies at all. CONFIRMED.**

```console
$ orchard next
  (blocked) T2: deps — T1 is open
$ orchard claim T2
claimed T2 (lease 1800s, renew every 300s)
```

`acquire` checked removed / done / leased / glob-overlap, and stopped. So an agent
picking work by id — which is what "implement phase X" does when it walks a plan —
bypassed the dependency graph entirely, and the failure is invisible: the work happens,
just in the wrong order, against files that do not exist yet. Fixed by routing both
callers through one `plan_blocker`, which is what `item_blocker`'s own docstring had
claimed for months.

### A third, found while writing a test for something else

**An unidentified reviewer satisfied the independence requirement. CONFIRMED.**

`gates.record` defaults the reviewer to the agent id, and `family_of` returned the
unmatched name back — so a `standards` gate recorded with no `--model` arrived as family
`"host-12345"`, compared unequal to the author's `"anthropic"`, and established
cross-family independence on its own. The check whose entire purpose is to refuse
*unverified* independence was passed by the **absence of information**.

It was found because a fixture meant to set up a refusal produced a pass — which is the
usual way, and an argument for writing the negative case first.

Under it sat the older defect: **two `family_of` implementations**, one in `reviewer.py`
with 23 substrings returning the model's own name for an unknown, one in `gates.py` with
9 returning `"unknown"`. A Phi reviewer was `microsoft` to the layer that ran it and
`phi-4` to the layer that decided whether it counted. Third instance of duplicate-then-
drift in this package; eliminated rather than guarded.

### The rest, by class

| # | Defect | Class |
|---|---|---|
| 1 | `replay` dropped every `decision.recorded` — the two renderers were unreachable | provenance loss |
| 2 | `split` appended children as it validated them; a collision on the second left the first written | non-atomic multi-write |
| 3 | `remove` checked for children only on phases, orphaning a task umbrella's sub-tasks | hierarchy half-applied |
| 4 | `block` was the one mutating command with no existence check; a typo created a phantom task the scheduler then offered | missing existence check |
| 5 | a gate in `gates.required` but in no pipeline made the requirement *disappear* | vacuous truth |
| 6 | `_h_lesson` rebuilt the object, dropping `superseded_by`; retired advice returned to the brief | fold reorder |
| 7 | the parallelism cap was measured against the queried phase, not the queue | scope mismatch |
| 8 | three loop detectors kept firing on removed items | stale finding |
| 9 | the two search backends disagreed on the shortest usable term by one character | two implementations of one rule |
| 10 | **a subprocess inherited the MCP server's stdin** | see below |
| 11 | unknown tool arguments were silently ignored despite `additionalProperties: false` | silent knob drop |
| 12 | `orchard_phase_add` had no `globs`; 15 more CLI flags unreachable over MCP | surface divergence |
| 13 | `orchard gate skip` and `bug found` had no MCP tool at all | surface divergence |
| 14 | adding a sub-task to a *claimed* task left the umbrella holding a lease that blocked its own children | transition reachable by two paths, guarded on one |
| 15 | a protocol-level refusal returned exit 0 | exit vocabulary broken at the boundary |

### #10 is the one to remember

Orchard runs as an MCP server **over stdio**: the JSON-RPC session *is* the process's
stdin and stdout. `subprocess.run(...)` with no explicit `stdin=` hands the child that
same pipe. All twelve subprocess call sites did this, and one of them is `gate run`,
which executes an arbitrary command from the project's own config.

The failure mode is as quiet as it gets:

```console
setup: {"jsonrpc": "2.0", "id": 2, "result": ...}
resp:  EMPTY
rc: 0   STDERR:
```

Exit **zero**, empty stderr, closed stream, nothing to explain it. Found because a
companion-detection probe added to `orchard setup` ended the session on the *next* tool
call. Fixed with one `proc.py` whose default is `stdin=DEVNULL`, plus a ratchet that
fails if any module calls the stdlib directly.

**The mutation test for it passed at first, and proved nothing** — under pytest the
parent's own stdin is already empty, so the child read `''` either way. The real test
spawns a parent with a pipe carrying bytes; under mutation it now prints
`CHILD_SAW='PROTOCOL-BYTES\n'` / `PARENT_KEPT=''`, which is the defect itself.

### What changed in how this project is tested

Three ratchets, each mechanically checkable, each mutation-verified:

- **no module may call `subprocess` directly** (`tests/test_stdio_safety.py`);
- **every CLI *subcommand*** must have an MCP tool, not just every command — the old
  ratchet passed while `gate skip` had none, because `gate` was "covered" by
  `gate run`;
- **every CLI *flag*** must be reachable from its tool, with an exemption list that
  carries reasons. This one found 15 divergences on its first run.

And one scenario: `demos/scenario_full_lifecycle.py`, which drives a two-phase project
with sub-tasks from the operator's first English sentence to a rebuild-from-log, over
MCP. It was written to exercise the *requirements*, and it found defects 12, 13, 14 and
15 before it finished passing once.

### The seven roborev added

Its duplication analysis found four **duplicate-then-drift pairs**, which is the third
time that class has produced a real bug here, and the reason the house rule is
*eliminate* a duplicate rather than guard it twice. What makes them hard to see is that
both copies read as correct on their own — the defect exists only in the difference.

| Pair | The drift, and what it cost |
|---|---|
| three TOML overlay loaders | companions **silently dropped** unknown keys while gates and reviewers raised, and read only its own file while the others also read `config.toml`. A misspelt `commmand` wrote a launch line that fails mid-task — the silent-knob-drop class, in a package whose config loader raises on a typo'd *section* to prevent exactly that. Now one `tomlcfg.py` with one policy. |
| four HTTP call sites | three rewrote loopback for containers; the fourth is the only path `kind="anthropic"` and `kind="gemini"` use, so container support covered a third of the backends |
| two "is this on PATH" checks | the weaker copy faced a *reviewer*: it split `FOO=bar claude -p` into a head of `FOO=bar` and reported a false UNAVAILABLE, had no builtin allowlist, and let `shlex.split`'s `ValueError` escape a function contracted never to raise |
| two `family_of` wrappers | `resolved_family` used the shipped map while `reviewer_independence` used `[agent].families` — a project teaching the map its in-house model name had it honoured by the gate that decides whether a review counted and ignored by `reviewers list`. Half-unified earlier in this same pass; the wrappers re-opened the seam one level up |

Its architecture pass added a fifth and a sixth: `resources/read` had **a second data
path** folding the log directly in a module whose premise is "one implementation, two
doors" (with a dead, shadowed table entry beside it, which is how a second path stays
hidden — nothing reads the line, so nothing contradicts it), and `Store.__init__` ran
`mkdir`, so a read-only `orchard status` **created `.orchard/` in a repository that had
never adopted the tool**:

```console
$ git init -q /tmp/orchprobe && python -m orchard --repo /tmp/orchprobe status
exit=0
$ ls -a /tmp/orchprobe   →   .  ..  .git  .orchard
```

The rest of its architecture reading is structural debt rather than defect, filed as
B35–B40 with its measurements — including a profiled demonstration that `plan()` is
roughly quadratic in item count because `State` has no parent index (74% of `plan()` at
n=800 is calls into `children`).

**Two of my own mutation tests passed and proved nothing**, both for the same reason:
they asserted on the *source text* rather than on behaviour. One grepped the function
body for `rewrite_localhost` and survived removal of the call, because the import and
the comment stayed. The other asserted that a reader and a writer agree — which they
still do when both are wrong in the same way. Rewritten to assert the URL actually
requested, and the field VS Code actually reads.

---

## R11 — the importer against a real 400-day corpus, not a fixture

**Question.** `orchard import` passes fourteen tests against a fixture and one
end-to-end scenario. Does it actually work on a project that has been running for four
hundred days — or does it only work on a file written by the person who wrote the
parser?

**Falsifier, stated first.** If the scan produces a queue whose ids match the ids the
project has been using in its own commit trailers, whose declared dependencies all
resolve, and which is unchanged by a second run, the importer works. Any one of those
failing kills it.

**Budget.** ≤2 hours, CPU only, on a corpus already on this machine.

**Corpus.** `run_nemo_run`, the repository Orchard lives in: `docs/todo.md` (35,078
lines, 4,799 checkboxes), `docs/todo/open/*.md` + `archive/*.md` (9 files),
`docs/lessons.md` (14,362 lines), `docs/RESEARCH.md` (7,534 lines), `docs/log/*.md`
(7 files), `docs/adr/` (5 files) and a 47-record OptMem store. Copied into a scratch
repository first — an import writes events, and writing them into the project being
read is not a test, it is an accident.

**Verdict: REFUTED, then fixed.** The importer did not work. It ran, it reported
success, and what it produced was unusable in eleven distinct ways. Every one of them
was invisible to the fixture tests, and all eleven now ship with a mutation-verified
regression test in `tests/test_import_real_project.py`.

### What the first run actually produced

```console
$ orchard --repo /tmp/rnr-import import --max-tasks 5000
  790 phase(s):
    [ ] SESSION-DRIVERFIX-THE-DE   Session DRIVERFIX — the defects ...  docs/todo/open/DRIVERFIX.md:1
    [ ] 160A-THE-DRAFT-SCORER-SC   160.A — the draft scorer: score ...  docs/todo/open/PHASE160.md:22
  [no tasks at all, and no explanation next to them]
  358 research(s):
    [ ] R-sources-opened-not-snippet-cited  Sources (opened, not snippet-cited)
```

Measured, before and after:

| | before | after |
|---|---|---|
| declared dependencies that resolve to an imported id | **8 / 47** | **45 / 45** |
| colliding ids (silent data loss on fold) | **42 pairs** | **0** |
| research entries vs. fragments of entries | 358 | 72 |
| journal entries dated by when they happened | 0 | 1,727 |
| phases carrying the project's own id | ~0 | 218 / 314 |
| phases proposed with no task under them | 459 | 0 |

### The eleven, each with the mechanism

1. **`142.A` was not an id.** The id pattern required a leading *letter*, so every
   numeric-dotted phase id — the shape this project has used for two hundred phases —
   was slugged to `142A-THE-SCALING-LAW-ADV`. That alone broke 39 of the 47 declared
   dependencies: they pointed at `142.A`, which then existed nowhere. Unknown
   dependencies are treated as unmet *by design*, so the work imported permanently
   blocked while the import reported success.
2. **An id inside a spanning bold was not an id.** `- [ ] **DRIVERFIX.1 — step 1 picks
   …**` matched neither the delimited pattern (which needs `**` straight after the id)
   nor the bare one (anchored at `^`, blocked by the `**`).
3. **The child-prefix rename destroyed the ids it existed to recover.** `### 142.A` has
   children `142.1`, `142.2`, so the derived prefix is `142` — and taking it renamed the
   phase out from under every `Needs: 142.A` in the file. A heading that declares its
   own id now outranks the inference.
4. **Derived task ids voted in that inference**, and one of them made the vote
   unanimous-with-nobody: a checkbox with no id was given `<phase-slug>.<title-slug>`,
   which disagrees in its first component, so the common prefix came out empty and 96
   phases kept a prose slug they did not need.
5. **42 pairs of ids collided.** Two lessons whose titles agree in their first 32
   characters produce the same slug; the second `lesson.recorded` folds over the first,
   one disappears, and the import reports both as written.
6. **One research entry became five.** The section splitter matched `#{2,6}`, so the
   `###` sub-parts of an entry — "Sources", "Known gaps" — became siblings of it. Four
   of the five fragments meant nothing standing alone.
7. **`docs/adr/README.md` imported as a decision** whose body was a table of contents.
   Every ADR directory has one.
8. **Every journal entry was dated the day the import ran**, destroying the one thing a
   journal is for. The dates are in the headings (`… (2026-04-30)`, `2026-04-22 — …`)
   and in the filenames.
9. **The fold dropped every note field but three.** `seq`, `ident` and `source` went in
   and never came out — so the idempotency check read `ident` back as `""`, compared it
   against `""` and reported "already imported" for everything. A projection silently
   deciding a field does not exist.
10. **Each memory printed twice.** A memory is one line and has no title; the note
    writer concatenated its title with its body, and the title *was* an excerpt of the
    body.
11. **Over the cap, 790 phases were proposed with zero tasks** — which reads as "this
    project has 790 phases of work", the opposite of true — and the human preview listed
    five of the eight kinds, so a repository whose history is a journal and a memory
    store printed a header with nothing under it.

### What it now reports rather than resolves

Two findings the scan can make and must not act on, because either answer could be the
wrong one:

- **32 phase headings say `SHIPPED` over unticked checkboxes.** Independently
  corroborated: memory `#3` in the same repository's OptMem store reads *"docs/todo.md
  checkboxes DRIFT: many `[ ]` items are actually done"*. One-sided risk — if the
  heading is right, the queue is about to hand out finished work.
- **A dependency on an id nothing produced** stays unmet, deliberately, so a typo
  surfaces as blocked work rather than as work that starts early. But it is now named:
  "never offered" otherwise looks exactly like "nobody has got to it yet".

### The end state

```console
$ orchard --repo /tmp/rnr-import import --max-tasks 5000 --apply
Imported: 4 decision, 1727 journal, 442 lesson, 47 memory, 314 phase, 72 research, 1170 task, 9 task_done

$ orchard --repo /tmp/rnr-import recall "H200" --max-chars 20000
## PROMPT/NOTE  — the operator asked, or an agent recorded, something like this
  [s-imported-memory#n2] 2026-07-31 the agent noted:
      Hardware: 8x H200 GPUs on this box, usually idle. GPU-owed test items in
      docs/todo.md can actually be run; check nvidia-smi first ...

$ orchard --repo /tmp/rnr-import doctor ; echo "exit=$?"
Healthy.
exit=0

$ orchard --repo /tmp/rnr-import import --max-tasks 5000 ; echo "exit=$?"
Nothing to import.
exit=2
```

3,789 events, 1,484 items, scan in 0.65 s and apply in 6.4 s.

**The generalisable finding**, and the reason this belongs here rather than in a commit
message: *a parser tested only against a fixture is tested against its own author's
assumptions.* Fourteen fixture tests and one end-to-end scenario were all green while
39 of 47 dependencies were broken. The corpus was free, already on the machine, and
found eleven defects in under two hours. `tests/test_import_real_project.py` keeps both
halves — a miniature carrying every real shape, plus a `@pytest.mark.slow` canary that
runs the whole scan against the host project when there is one and skips otherwise.

### R11 addendum — what the three reviewers found, and how disjoint they were

The rulebook requires a cross-family critic, a subagent rubber-duck and roborev on every
non-trivial change, on the argument that they find different things. Measured on this one:

| Reviewer | Family | Findings that survived a probe | Overlap with the others |
|---|---|---|---|
| Own double-check | — | 4 (dead `outcome.py`, duplicate glob reads, an over-permissive date regex, the offer counting 3 of 7 sources) | 0 |
| roborev (`analyze duplication`) | same | 2 (`cmd_merge` bypassing `_require_item`; the three-copy section scanner) | 0 |
| Cross-family critic | different | 8 (`head()`'s two-pass fingerprint; the `rebuild` fingerprint ordering; `gate verify` certifying an already-red gate; its two-channel return; the silent primary-checkout fallback; the incremental-import false alarm; the cross-file annotation leak; the underscore stripped from ids) + 3 refuted by probe | 0 |

**Zero overlap across fourteen findings.** The two labelled `THEORETICAL` by the critic
were the two worth acting on — one was a real defect (`head()`), one was refuted by a
five-line AST probe and left behind a ratchet. The reviewer that found the most
consequential bug — `orchard merge` landing work the operator had explicitly dropped —
found it while looking for something else entirely, which is the standing argument for
running the duplication pass even when nothing feels duplicated.

The critic's `CONFIRMED`/`THEORETICAL` labels were again not a ranking of importance.
Six of its eight real findings were labelled `THEORETICAL`, and every one of them was a
genuine defect with a deterministic regression test — including `rebuild`'s fingerprint
ordering, which permanently loses events from `recall` if the log then stops growing.
Its two refuted claims were also `THEORETICAL`, so the label carried no signal in either
direction; what separated them was a probe, in every case costing under ten minutes.

Its two other `THEORETICAL` claims died on inspection and are recorded because a
reject re-checked is worth as much as a claim confirmed (§Research-rules 4): `gate
verify` might dispatch to the `run` handler and silently execute the gate — refuted
because `tests/conftest.py::run_cli` spawns a real `python -m orchard` subprocess, so
every one of the thirteen `gate verify` tests drives the actual parser; and `_csv`
losing its `(v or "")` guard — refuted because argparse never calls `type=` with `None`.

Its single `CONFIRMED` was the best finding of the pass and deserves its own line:
**`gate verify` — the anti-vacuous-pass check — was itself vacuous.** A gate already red
for an unrelated reason reports `failed` for every mutation, so every mutation reads as
"detected" and the gate is certified as able to fail when nothing has shown any such
thing. The check written to catch the class contained the class. It now runs a green
baseline first and refuses without one.

One more measurement, from the tail of the same run: **the critic's last chunk — the one
reviewing `importer.py`, the file this whole change is about — produced three of its
eight real findings**, and the run then hit its 45-minute wall clock at 17 of 18 chunks
(exit 124 = PARTIAL, recorded as such rather than as a pass). Chunked review is not
uniformly valuable across a diff and the most valuable chunk was near the end; a budget
that cuts it off loses exactly the part that was worth paying for. Next time: review the
changed MODULE first and the incidental diff after, or raise the budget to match the
diff (158 KB over 18 chunks here).
