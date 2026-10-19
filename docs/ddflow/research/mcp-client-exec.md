# RM-client-exec-research: a remote or containerised ddflow server that tells the agent what to run

Operator question (2026-10-19): if the MCP server is remote or in docker it cannot run git,
tests or reviews on the project. Should it instead return the commands for the agent's own
shell to execute, so a server with no filesystem can still manage a project?

Verdict (details in section 5): **narrow GO.** Build the *thin local client* first (the same
ddflow CLI, with only its event-log backend remote). A general "every tool returns commands"
mode is **NO-GO**: it forces a plan/apply split of 41 tools and lowers evidence trust for no
gain over the thin client. A small `needs_local` envelope is worth shipping for three verbs
only (claim, gate run, merge), where the agent has a shell but no ddflow install.

Prior art this builds on, not repeats: R14 (docs/RESEARCH.md:1371) REFUTED delegating file
I/O to the client over MCP primitives and split the product into a remote-capable half and a
local-required half; R-storage-seam / P-storage keep a server that needs no client
filesystem via a storage seam; D-remote chose a stateless opt-in `ddflow serve`
(P-remote.serve); D-dispatch-modes ships a propose-only mode. This note is about plain
instructions in tool results, executed by the agent's own shell.

## 1. Taxonomy: what each tool needs from a working tree

Source: `ddflow.surfaces.declared.family_modules(tools=True)` over
`ddflow/surfaces/declared/*.py` (31 family files). Counted by script on this worktree:
**157 declared commands, 119 of them MCP tools** (the rest are CLI-only). There is no locality
column in `registry.Command` today (its fields: path, summary, params, tool, call, payload,
tier, via, ... ; `ddflow/surfaces/registry.py:551`), so the classification below is mine, by
reading each `call=` target and its description; ST-locality is the task that should make it
data. Ambiguous cases are named.

| Class | Count | What it needs | Tools (MCP names without `ddflow_`) |
|---|---|---|---|
| A. pure state | **74** | the event log (+ derived index) only | next brief heartbeat release wait gate_status gate_list gate_record gate_skip abandon\* remove block unblock; task_add phase_add split resolve update batch task_fold rename record; research_add bug_found bug_file_tasks bug_fixed\* bug_invalid session_start/prompt/note/end; decision_add/list/show/applicable/supersede lesson_add/search/verify recall similar dupes link; memory_add/list/forget job_add job_list job_end; promote_add/deployed/status flow_show flow_choose; status show board history progress loops replay lanes health\* metrics cadence; review_triage reviewers_list rule_list rule_search rule_show; workflow workflow_state help prompts |
| B. tree/host-bound | **41** | a git worktree, a shell, files in the project | claim (worktree add), gate_run, gate_verify, merge, complete (compares landed tree to gate evidence), ci tests precommit bisect docs pins export render queue (integrator), phase_close (runs the phase suite), version_cut version_show, job_run, verify, sync, recover cleanup rebuild, reviewers_detect (probes host binaries), setup configure upgrade import import_verify doctor, hooks companions companions_verify companions_add, workflow_pipeline workflow_gate workflow_drop (write gates.toml), rule_add rule_edit rule_remove rule_sync (rules are files under `.ddflow/rules/`) |
| C. secret/network-bound | **4** | an API key or forge token, plus (for review) a diff | review (reviewer API keys, `services/review.py:90-181` `api_key_env` per provider; read from the environment at `review.py:290`), pr_sync pr_status pr_threads (forge token, `infra/forge.py`) |

Caveats that change a class: `brief` with `check_recovery` scans worktrees (becomes B on
that argument); `abandon` also removes a worktree; `bug_fixed` checks the regression test
in the tree; `health` with `run=true` acts on host processes. These are A tools with a B tail.

Evidence for the split, from this repo:

* **Decisions, lessons, research, bugs, sessions are events only.** Probe in a scratch repo:
  `decision add` and `lesson add` change only `.ddflow/events/<agent>.jsonl` (git status
  shows ` M .ddflow/events/srv.jsonl` and nothing else). **Rules are the exception:** `rule add`
  creates `.ddflow/rules/` plus `DDFLOW.md`, so rule_* and workflow_* are files and belong
  to P-storage's seam, not to the pure-state set. That moves 7 tools out of what R14 called
  "queue as pure data".
* **claim has exactly one tree-bound step.** `GIT_TRACE` of `ddflow claim` shows
  `git branch ddflow/<id> main` then `git worktree add -b ddflow/<id> <path>`
  (infra/worktree.py:244, `create`), then `[worktree].local_files` copies. The lease is
  an event. The worktree is adoptable: `create` returns the existing tree if present
  (worktree.py:219-223), which is what makes a retry after a lost reply safe.
* **gate_run's single choke point is `CommandRunner`.** `run_command_gate`
  (services/gates/runner.py:132) builds `Declared(command, "gates.toml [gate.X]")`, an env
  (child_env, `DDFLOW_RUNNING_GATE`, `PYTHONPYCACHEPREFIX` ...), a cwd and a timeout, runs it
  through `cmdrunner.CommandRunner().run` and turns the result into evidence: `command`,
  `command_digest`, `exit`, `output_digest`, `tail`, `tree_sha` (`<head12>+clean` or
  `+st:<manifest>`, evidence.py:257), `diff_stat`, `output_log`, `started_tree_sha`. Every
  command-gate, ci, tests and precommit funnels through it. That is the seam where a plan
  object can be emitted instead of a subprocess.
* **merge is not a script.** `api/lifecycle/merge.py:50` is a state machine: contested-lease
  refusal, gate blockers, `_what_to_land`, unreadable tree, dirty files (with
  `--allow-dirty`), "lands nothing", then git, then conflict handling, then events
  (57 git calls across it and worktree.py). A flat `commands:[...]` list cannot express its
  branches; it needs round trips. It also already has a PR-mode form: with
  `[flow].integration = "pr"` merge only opens a request and parks the item in REVIEW, and
  `pr sync` finishes it (merge.py docstring). That is the correct client-exec shape for merge.
* **Reviewers.** API reviewers (kind api, key from env) need only a diff; the diff can come
  from a git remote. Command reviewers (`rev.env`, `review.py:777`, `shell=True`
  `review.py:485`) run a local binary: host-bound.

Consequence: **About two thirds of the tools need nothing from a tree** (74 pure plus 4 secret-bound = 78 of 119, if the forge tools are
served from the server), 41 need one, and the 41 collapse to five primitives: worktree
create, run a declared command in a tree, git land, write a project file, probe the host.

## 2. The `client-exec` contract

Shape of the reply of any class-B tool when the server has no tree (`[serve].exec = "client"`,
or the caller's `locality` capability says `none`):

```json
{
  "needs_local": true,
  "plan_id": "pl_8f2c...",            // server-minted, single use, expires (plan_ttl_s)
  "tool": "ddflow_gate_run",
  "steps": [
    {"id": "s1", "argv": ["git","fetch","origin","ddflow/probe-w"],
     "cwd": "$REPO", "env_names": [], "expect": {"exit_codes": [0]}},
    {"id": "s2", "argv": ["python3","-m","pytest","-q","test_m.py"],
     "cwd": "$WORKTREE", "env_names": [],   // names only, never values
     "timeout_s": 600, "capture": {"stdout_digest": true, "tail_bytes": 4000},
     "expect": {"exit_codes": [0,1], "artifacts": []}}
  ],
  "declared_by": "gates.toml [gate.unit_tests]",     // Declared.source, command_digest below
  "command_digest": "53f697ca568fc793",
  "then": "ddflow_gate_report(plan_id, results=[{id, exit, elapsed_s, output_digest, tail}], tree={head, dirty}, output_blob)"
}
```

Rules of the envelope:

* `argv` arrays, never shell strings, except the one declared gate command that is a shell
  line by definition (`Declared.line`): it is delivered as `["sh","-c",line]` with
  `declared_by` so the agent and an auditor can see whose line it is. No secret ever appears
  in `argv`; the envelope carries `env_names` only (names the agent's environment must
  already hold), no values. A declared line that matches a secret heuristic (inline token,
  `Authorization:` header, `-p <password>`) is NOT shipped: the planner records that gate
  `unavailable` with that reason and the operator moves the secret to an env name. Reviewer
  keys never leave the server (section 3).
* `cwd` uses `$REPO` / `$WORKTREE` placeholders; the agent substitutes real paths. This is
  the path-difference fix: the log already stores worktree paths relative to the repo root
  (R14, worktree.py:341-377).
* `expect.exit_codes` lets the server tell "ran and failed" from "could not run" exactly as
  `classify_exit` does (runner.py:266). `127`/spawn error/timeout map to `unavailable`.
* **Replay semantics, one way:** `plan_id` is minted once per (tool, item, head) and is live
  until reported or expired. Calling the tool again while a plan is live returns the SAME
  plan (no second plan, no orphan in `brief`); a report for an already-reported plan returns
  the recorded outcome. The agent has no `Worktree.create` adoption logic, so the claim plan
  begins with a probe step (`git rev-parse --verify --quiet refs/heads/ddflow/<id>`) and the
  server picks `git worktree add -b ...` (fresh) or `git worktree add <path> <branch>`
  (existing) from the reported probe result (a two-round plan); a report of "already present"
  counts as success. The adoption claim in section 1 therefore holds for the thin client only.
* The server emits the envelope by a `PlanRunner` that implements the `CommandRunner`
  interface (cmdrunner.py:208) and returns a plan instead of running. Same code path, same
  evidence keys, one new status `"deferred"` beside `ran`/`unavailable`.

### Worked example A: claim with a worktree (probed)

Scratch repo `srv` (server state), `agent` clone has no `.ddflow` (fake agent:
`/tmp/.../scratchpad/probe/fake_agent.py`). Today's claim with `--no-worktree` is the lease
alone (probed: `claimed probe-w (lease 1800s, renew every 300s)`). Proposed contract:

```
> ddflow_claim {id:"probe-w", exec:"client"}
< {ok:true, lease:{expires:"+1800s", renew_every_s:300}, needs_local:true, plan_id:"pl_1",
   steps:[
    {id:"s1", argv:["git","fetch","origin","main"], cwd:"$REPO", expect:{exit_codes:[0]}},
    {id:"s2", argv:["git","worktree","add","-b","ddflow/probe-w","$REPO/.ddflow/worktrees/probe-w","origin/main"],
     cwd:"$REPO", expect:{exit_codes:[0]}}],
   then:"ddflow_plan_report(plan_id, results)"}
  (agent runs s1, s2; both exit 0)
> ddflow_plan_report {plan_id:"pl_1", results:[{id:"s1",exit:0},{id:"s2",exit:0}],
   tree:{head:"399834b564bb", branch:"ddflow/probe-w"}}
< {ok:true, worktree:{branch:"ddflow/probe-w", base:"main", head:"399834b564bb",
    status:"agent-reported"}}                      // lease already held; worktree event appended
```

Failure shapes that must be explicit: report with only `s1` => `partial`, lease kept, the
item is listed by `brief` as "plan pl_1 half-done: run s2" (the existing orphan-worktree
recovery in `brief` generalises); no report before `plan_ttl_s` => the plan expires and
`brief` leads with it, never a silent claim success.

### Worked example B: gate run unit_tests (probed, agent side)

```
> ddflow_gate_run {id:"probe-w", gate:"unit_tests", exec:"client",
                   tree:{head:"d77a9316378d", pushed:true}}
< {needs_local:true, plan_id:"pl_2", command_digest:"53f697ca568fc793",
   steps:[{id:"s1", argv:["sh","-c","python3 -m pytest -q test_m.py"], cwd:"$WORKTREE",
           timeout_s:600, expect:{exit_codes:[0,1]},
           capture:{stdout_digest:true, tail_bytes:4000, upload_blob:true}},
          {id:"s2", argv:["git","rev-parse","HEAD"], cwd:"$WORKTREE"},
          {id:"s3", argv:["git","status","--porcelain"], cwd:"$WORKTREE"}],
   then:"ddflow_plan_report"}
  (agent runs; pytest exit 0, HEAD d77a93..., porcelain empty; pushes output blob)
> ddflow_plan_report {plan_id:"pl_2", results:[{id:"s1",exit:0,output_digest:"..",elapsed_s:0.1,tail:"1 passed"},
   {id:"s2",stdout:"d77a9316378d..."},{id:"s3",stdout:""}], blob:"<upload id>"}
< {gate:"unit_tests", outcome:"passed", trust:"agent-reported",
   verified:{sha_on_remote:true, command_digest_matches:true, blob_digest_matches:true},
   tree_sha:"d77a9316378d+clean"}
```

What the probe actually did (today's primitives, no new server code):
`gate record probe-w unit_tests --outcome passed --command ... --exit-code 0 --reviewed-sha
<sha> --output-file /tmp/ga_out.log` was accepted and wrote `gate.passed` with
`evidence:{command, exit, output_bytes, output_digest, output_file:"/tmp/ga_out.log",
reviewed_sha, summary, tail}`. Gaps seen: (1) `output_file` is a path on the *agent's* host and
dangles on the server; (2) **no `tree_sha`** from this route (the real run records
`tree_sha:"399834b564bb+clean"`), so `complete`'s check of evidence against the landed tree
has nothing to compare; (3) the server could resolve the pushed sha (`git cat-file -e
<sha>^{commit}` true, tree `ab987cf9b925`), so a *sha-on-remote* check is cheap.

### Worked example C: merge (PR-mode, two round trips)

```
> ddflow_merge {id:"probe-w", exec:"client", head:"d77a9316378d"}
< refusals that need no tree are evaluated BEFORE any plan: contested lease, gate blockers
  (evidence tree vs the supplied head), and a provisional nothing-to-land check on the
  supplied head (re-run on the fetched head after the push). Then {needs_local:true, plan_id:"pl_3",
   steps:[{id:"s1", argv:["git","push","origin","ddflow/probe-w"], cwd:"$WORKTREE", expect:{exit_codes:[0]}},
          {id:"s2", argv:["git","status","--porcelain"], cwd:"$WORKTREE"}],
   then:"ddflow_plan_report"}
> ddflow_plan_report {plan_id:"pl_3", results:[{id:"s1",exit:0},{id:"s2",stdout:""}]}
< server fetches ddflow/probe-w from the remote, re-runs nothing-to-land on the fetched head
  (a plan that expires after the push leaves only an unreferenced branch, which `cleanup`
  lists), verifies head == the head the gates ran on,
  then lands IT, not the agent: (a) integration="pr": opens/updates the PR via forge token,
  item -> REVIEW, `pr sync` finishes (merge.py docstring); or (b) a server-side bare mirror:
  `git merge-tree` conflict check + `git update-ref` fast-forward of main, then push.
  {merged:false, state:"review", pr:{number:12,url:"..."}}   (or {merged:true, sha:"..."})
```

Why merge is the one verb where "agent runs commands" is wrong: landing is the most
consequential action in the package (merge.py:51) and its safety rests on server-visible
state. In client-exec it must be the server that moves the ref, from objects the server
fetched itself. The agent's job is only `git push` of a branch.

## 3. Trust and verification

Evidence is agent-reported; the question is which gates can still be made verifiable (the
falsifier of the task). Result: server-derived gates stay verifiable in client-exec ONLY for a pushed, clean tree
(`head+clean`). **A gate run on a dirty tree (`+st:<manifest>`, the normal state while
developing) is verifiable today and is NOT in client-exec**: a real downgrade, so dirty-tree
gates are not expressible in the envelope and need the thin client. Prose gates were never
verifiable and stay honor-system. One existing hole should be fixed first.

**The floor today (probed).** `ddflow gate record probe-u unit_tests --outcome passed --command
"pytest -q" --exit-code 0` on a command gate that was never run is accepted and logged as
`gate.passed` (a NOTE about gate order only). An agent with a shell can already fake any
command gate. And the report's "by hand" heuristic does not catch it:
`services/report_gates.py:15` marks a gate hand-recorded only when none of
`TOOL_KEYS = ("command","reviewer","covered_by","roborev","branch_head","ci_scope","scope")`
is in the evidence, and `gate record --command` writes `command` itself. So a faked pass
carrying a command is reported as tool-produced. Filed as bug Bed643a611b (task fix-Bed643a611b; `bug found --check` found no earlier duplicate); client-exec must not
ship before a provenance field exists.

Trust labels (a `trust` field on gate evidence, ordered): 

| Label | Meaning | Gates that can reach it |
|---|---|---|
| `server-run` | server (or its sandbox) executed it | review by API reviewers (diff from remote), research/rules/standards gates that are agent prose do not apply; ci re-run in a server sandbox |
| `forge-attested` | an external system reported it | ci via `pr_status` on the pushed sha; merge via forge merge commit |
| `server-verified` | agent ran it, server re-derived the checkable parts | unit_tests, tests, precommit when: command_digest equals the declared one, sha on remote and equals the branch head at report time, tree clean (`head+clean`), output blob digest matches, plus sampled server re-run (`[gates].audit_rate`) |
| `agent-reported` | agent says so, nothing to check | hand-recorded gates (research, critic, rubber_duck prose), dirty-tree runs (`+st:<manifest>` is not derivable from a remote), gates whose command needs the agent's private environment (GPU, secrets) |

What the server can re-verify with a git remote and no working tree: that a commit sha exists
and is the branch head (`cat-file`, `rev-parse sha^{tree}`: probed true for the pushed sha);
ancestry relative to base (`merge-base --is-ancestor`); the diff stat and the diff itself
(`diff base...sha`) for reviews; merge-tree conflict prediction. It cannot compute
`source_tree`'s dirty-content manifest, so **client-exec requires push-before-gate** (clean
`head+clean` identity only); a gate on an unpushed dirty tree is `agent-reported` at best.
Re-running in a server sandbox (ephemeral shallow clone of the pushed sha + `CommandRunner`)
is real CI and fits ci/unit_tests for projects whose toolchain can be provisioned; it is out
for projects that need GPUs or private services.

How the standing rules survive:

* **unavailable is never passed.** The report must account for every `step.id`; a missing step,
  non-zero code outside `expect`, timeout, or an expired plan is recorded `unavailable` (could
  not run) or `partial` with the reason, by the server, never inferred from silence. This is
  the same rule `run_command_gate` implements (runner.py:211-215, 266) moved to the report
  handler. Silence (no report) leaves the gate with no outcome, which `complete` already refuses.
* **different-family reviewer.** Preserved and in fact strengthened for API reviewers: the
  server holds keys, fetches the diff itself, records `model`/`family` itself (not
  agent-declared). Command-kind reviewers run on the agent's host; their `model` is
  agent-declared, so for the independence rule they count only if the operator sets
  `[review].accept_agent_run = true`; otherwise recorded `unavailable`. Secrets thereby stay out
  of command lines.
* **bug closed only with a regression test that fails on the unfixed code.** The
  fail-against-unfixed run is two command runs on two shas: server can require both reports
  (`sha_unfixed`, `sha_fixed`) and verify both shas on the remote. Same trust label rules.
* **evidence digest.** The agent's `output_digest` is uncheckable alone; requiring the full
  output blob upload (reuse `services/logblobs.py`, B-gap-log-scale) lets the server recompute
  it and lets a human read what ran. Without upload it is `agent-reported`.

Honest bound: a malicious agent can still upload a fabricated blob for a clean tree. The
defence is the sampled/forced server re-run and forge CI for merge-critical gates; the rest is
as trustworthy as an agent with a shell is today.

## 4. Thin local client versus the agent running raw commands

| Dimension | Thin client (`ddflow --remote URL`) | Raw commands in tool results |
|---|---|---|
| What runs | the real ddflow code: `Worktree.create`, `CommandRunner`, `merge`, evidence builders, against a remote log backend | the agent's shell executes argv the server emitted |
| Per-tool cost | **~0**: reuses the declared commands; needs only `EventLog` over HTTP (P-storage seam + P-remote.serve) | a plan/apply split for each of 41 class-B tools, each with its own state machine (merge is 57 git calls with branches) plus a report handler and tests |
| Evidence fidelity | identical keys (`tree_sha`, `diff_stat`, `output_log`, `command_digest`) | reconstructed from agent reports; `tree_sha` and dirty manifest not derivable |
| Skipped or faked step | the client is ddflow itself; a wrong client is a different problem | fully open: agent may skip, reorder, edit argv, fabricate results; mitigated by step ids, digest checks, sampled re-run |
| Partial execution | the local half is atomic per command as today (lease, adoption) | needs plan state, ttl, resume; the new thing the server must keep |
| Secrets in commands | none: keys read from the client's env as today, or stay server-side for review | must be designed out (env names only); a command line in an LLM context is a leak path |
| Paths / OS | handled in-process (worktree paths already relative); Windows still blocked by `fcntl.flock` (B13) | placeholders help, but argv for Windows/macOS differs (quoting, `sh -c`); the server guesses the client OS |
| Container without git / without python | does not work (needs ddflow installed) | works if the agent has any shell with git; the only case it wins |
| Version skew | client and server both check the skew guard (0.2.1 vs 0.2.4 refusal seen this session) | server is the only versioned party; a stale client is invisible |
| Effort | blocked on P-storage seam (the real work) | can start now, but duplicates the seam's job per tool |

Which to build first: **the thin client**, because it is the seam work P-storage already
plans and it adds nothing per tool. The raw-command envelope is built second and only as a
three-verb fallback, generated from the same declared commands.

## 5. Go / no-go and ordered tasks

* GO: thin client over the remote log (needs P-remote.serve + P-storage seam).
* GO (narrow): `needs_local` envelope for claim, gate run, merge-as-PR, with push-before-gate and server-side verification.
* NO-GO: general client-exec for all class-B tools (setup, upgrade, hooks, doctor, import, render, cleanup, rebuild): these are *install-time or local-maintenance* verbs; on a client with no ddflow they have no meaning, and a thin client already runs them.
* Bug first: Bed643a611b, a hand-recorded command-gate pass is unlabelled. Without it the trust label cannot be built.

Reuses: P-remote.serve (single stateless writer, authorization table over declared commands),
P-remote.runs (run handles, idempotent retries = the plan_id/report mechanics),
P-remote.auth (tokens), ST-locality (the locality column = this note's taxonomy as data),
ST-acceptance (client with no filesystem: claim, next, status, recorded gate), ST-config-overlay,
DX-review-detach (review keeps running server-side), `services/logblobs.py` (output blob),
`cmdrunner.CommandRunner` (plan seam), `[flow].integration="pr"` (merge), D-dispatch-modes
propose-only (same "emit a proposal, don't act" posture; note it has no argv object today, the
envelope is new).

| # | Task (title) | Globs | Needs | Wave |
|---|---|---|---|---|
| 0 | fix-Bed643a611b. Gate provenance: `gate record` on a command gate carries `trust:"agent-reported"` and the report no longer treats `command` alone as tool-produced (bug) | ddflow/api/gates.py, ddflow/services/report_gates.py, tests/test_gate_provenance.py | none | 1 |
| 1 | Locality column from this taxonomy: A / B / C per declared command, shown in the matrix, enforced by serve | ddflow/surfaces/registry.py, ddflow/surfaces/declared/*.py, tests/test_locality.py | ST-locality (extend, do not duplicate) | 1 |
| 2 | `trust` ladder on gate evidence (server-run, forge-attested, server-verified, agent-reported), shown in gate status/report/doctor, and a `[gates].min_trust` knob per gate | ddflow/services/gates/outcomes.py, ddflow/services/report_gates.py, ddflow/config_sections/gates.py | task 0 | 2 |
| 3 | Remote EventLog backend (HTTP append/read) behind the seam; thin client flag `--remote` | ddflow/infra/log_remote.py, ddflow/surfaces/cli.py | P-remote.serve, P-storage seam | 2 |
| 4 | Thin-client acceptance: claim, gate run, merge from a clone against a served queue | tests/test_thin_client.py | task 3, ST-acceptance | 3 |
| 5 | `PlanRunner`: a `CommandRunner` that returns a plan (`deferred`), plus plan state (plan_id, ttl, step ids) in the log | ddflow/services/cmdrunner.py, ddflow/services/plans.py | P-remote.runs, task 1 | 3 |
| 6 | `ddflow_plan_report`: accounts every step, maps to passed/failed/unavailable/partial, fetches and verifies the pushed sha, checks command_digest and blob digest | ddflow/api/plans.py, ddflow/services/gates/verify_remote.py | task 5, task 2 | 4 |
| 7 | Server-side git-remote ops with no working tree: sha/ancestor check, diff for review, merge-tree + update-ref | ddflow/infra/gitremote.py | task 6 | 4 |
| 8 | Client-exec for claim (worktree plan) and merge-as-PR | ddflow/api/lifecycle/claim.py, ddflow/api/lifecycle/merge.py | tasks 5, 7 | 5 |
| 9 | Server-side review: API reviewers on a remote-fetched diff; command reviewers `unavailable` unless `accept_agent_run` | ddflow/services/review.py, ddflow/api/review.py | task 7, DX-review-detach | 5 |
| 10 | Sampled server re-run (`audit_rate`) of agent-reported command gates in an ephemeral clone | ddflow/services/gates/audit.py | tasks 6, 7 | 6 |
| 11 | Acceptance: the fake agent from this probe, against a server container with no repo mount, including a fabricated-pass attempt that must be caught | tests/test_client_exec_acceptance.py | tasks 8-10 | 6 |

## Probe record

* Fake agent: `/tmp/claude-1001/.../scratchpad/probe/fake_agent.py`; server repo `srv` with
  `ddflow init`, tasks probe-t/u/w, `gate.unit_tests.command = python3 -m pytest -q test_m.py`;
  agent clone `agent` without `.ddflow`. Claim `--no-worktree` leased; the agent made the
  worktree with `git clone` + `git checkout -b`, ran pytest, pushed the branch to the server
  repo, then `gate record` was accepted; `git cat-file -e <sha>^{commit}` on the server: true.
* Real run (`ddflow gate run`) evidence has `tree_sha:"399834b564bb+clean"`, `output_log`, `elapsed_s`,
  `command_digest`; the record route has none of tree_sha, elapsed_s, command_digest.
* Not probed: a real server container with no repo mount (P-remote.serve does not exist yet);
  no ddflow code was changed (only this note was added).
