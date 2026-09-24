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
