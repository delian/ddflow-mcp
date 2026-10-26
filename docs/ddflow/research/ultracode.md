# Ultracode and Claude Code dynamic workflows: research for ddflow

Status: research only, 2026-10-26. Nothing was run, installed or edited. Every item is marked verified, reported, inferred or unverified. Refuted claims were dropped. Disputed claims are marked.

Sources:
- Official docs: https://code.claude.com/docs/en/workflows, /model-config, /settings-reference, /tools-reference, /hooks, /agent-sdk/typescript
- Blog: https://claude.com/blog/introducing-dynamic-workflows-in-claude-code
- Repo paths are relative to `/home/delian/src/ddflow`.

---

## 1. What ultracode is

### 1.1 Facts verified against the official docs

- **Dynamic workflows.** A dynamic workflow is a JavaScript script that Claude writes. A background runtime executes it and spawns many subagents. The script holds the loop, the branching and the intermediate results in variables. Only the final answer reaches Claude's context window. The Workflow tool requires permission.
- **Release.** General availability was announced 2026-05-28 for the CLI, Desktop and VS Code. It covers paid plans, the API, Bedrock, Vertex and Foundry. It is off by default on Pro and must be turned on in `/config`.
- **Two ways to opt in.**
  - Per task: the word `ultracode` in a prompt you type. Asking "use a workflow" in your own words also works. Neither changes the session's effort level. Alt+W or Option+W, or backspace, dismisses the highlighted keyword.
  - Per session: `/effort ultracode`, the Tab toggle in the `/effort` slider, `claude --effort ultracode`, the `ultracode: true` settings key, or an Agent SDK control request. With it on, Claude plans a workflow for each substantive task. A request may produce several workflows in sequence (understand, change, verify).
- **Ultracode is a setting, not an effort level.**
  - From v2.1.284, `/effort ultracode` leaves effort unchanged and ultracode can stay on at any level.
  - `--effort ultracode` and the SDK `effortLevel: 'ultracode'` also set `xhigh` (v2.1.203+).
  - The persisted `effortLevel` setting and `CLAUDE_CODE_EFFORT_LEVEL` do not accept it.
  - It needs a model that supports `xhigh`.
  - Older docs and the blog say it "sets effort to xhigh".
- **Where the keyword counts.** Only in human-typed prompts: interactive, IDE panel, Remote Control, or an SDK app stamping `origin {kind:"human"}`. It does not trigger from `claude -p`, unstamped SDK prompts, scheduled-task prompts, or relayed webhook and PR-comment text. `workflowKeywordTriggerEnabled=false` or the `/config` toggle turns the keyword off.
- **Script contract.**
  - The script is top-level-await JavaScript. `export const meta = {name, description, phases?}` must be the first statement and a pure literal.
  - Primitives: `agent(prompt, {schema, label, stallMs, ...})`, `pipeline(items, fn)`, `parallel(tasks)`, `phase(title)`, `log(msg)`, and the global `args`.
  - `agent()` returns `null` if stopped or on an unrecoverable API error.
  - A schema gives JSON-validated output. The call fails after five attempts. The count is settable by `MAX_STRUCTURED_OUTPUT_RETRIES`. The docs say "five attempts", not "five retries".
  - A self-contradicting schema is rejected before the agent starts.
  - `Date.now()`, `Math.random()` and no-argument `new Date()` throw, so a relaunch repeats the same calls.
  - `import()` fails the run before it starts.
  - The script has no filesystem or shell access. Only agents do.
- **Limits.**
  - 16 concurrent agents by default, fewer on fewer CPUs. `CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS` takes 1 to 256 (v2.1.269+).
  - 4,096 items per `parallel()` or `pipeline()` call, and 1,000 agents per run.
  - No mid-run user input. A run pauses only for agent permission prompts and usage-limit waits.
  - A stalled agent auto-restarts up to 5 times (6 attempts). It then fails: `null` inside `parallel`/`pipeline`, a thrown error if awaited directly.
- **Resume.**
  - Every run's script and each agent result are saved under the session directory in `~/.claude/projects/`.
  - Relaunch replays in agent-start order. Completed agents return saved results.
  - The first agent whose prompt differs, or that failed, reruns, and so does every agent after it, even completed ones.
  - It works within the same session, after backgrounding, after `claude --resume`, and in cloud sessions. Otherwise there is "nothing to resume".
  - Usage-limit auto-continue (v2.1.271+) works only in interactive claude.ai-subscription sessions, with at most 2 waits and a reset within 24 hours.
- **Saved workflows.**
  - In `/workflows`, select a run and press `s`. A dialog saves it to `.claude/workflows/` (project) or `~/.claude/workflows/` (user), and it runs as `/<name>`.
  - Plugins can ship a `workflows/` directory, namespaced `/plugin:name`.
  - `/deep-research` is bundled. The `/workflow-authoring` skill needs v2.1.248+.
  - Input arrives through `args`.
- **Permissions.**
  - `Workflow` allows every workflow. `Workflow(<name>)` allows one saved workflow. Hooks match the tool name `Workflow`.
  - The launch prompt appears in manual and acceptEdits modes on every run. In auto mode it appears on first launch only, and under ultracode it is skipped. Bypass mode never prompts.
  - In `-p` and the SDK there is no prompt. The call goes through normal permission evaluation.
  - A script's `agent()` prompt is marked script-computed for the auto-mode classifier.
- **Cost.**
  - Runs use meaningfully more tokens and count against plan limits.
  - A "Large workflow" warning appears above 25 agents or 1.5M projected tokens. It is advisory, and ultracode suppresses it.
  - `workflowSizeGuideline` is advice, not a cap.
  - Under ultracode, the session's concurrent-subagent limit is not enforced for Agent-tool subagents.
  - Workflow agents get a 5-minute prompt-cache TTL, including on a subscription. `subagentPromptCacheTtl=1h` extends it.
  - Agents with the same model, effort, agent type, tools, schema and cwd share a cache prefix. `CLAUDE_CODE_WORKFLOW_PREFIX_STAGGER_MS` (default 5000) holds the others until the first response begins. It is a cap, not a fixed delay.
- **Headless and SDK.** Workflows are available in `claude -p` and the Agent SDK. The SDK can turn ultracode on with `applyFlagSettings({ultracode:true})`. `projectConfigRoot` (v2.1.275+) reads `.claude/workflows` from another root.
- **Disabling.** Use `/config`, `disableWorkflows:true` (user or managed settings), or `CLAUDE_CODE_DISABLE_WORKFLOWS=1`. This also removes the keyword and the toggle.
- **Versus other primitives.** The docs' comparison table says:

  | | Decides what runs next | Intermediate results live in | Scale |
  |---|---|---|---|
  | Subagents, skills | Claude, turn by turn | Claude's context | A few per turn |
  | Agent teams (experimental) | The lead, turn by turn | A shared task list | A handful of long-running peers |
  | Workflows | The script | Script variables | Dozens to hundreds of agents per run |

### 1.2 Disputed or unverified

- **Hard budget.** DISPUTED. No hard token or dollar cap is documented for workflows. The repo notes (`docs/ddflow/research/agent-token-features.md`) list `--max-budget-usd` and `--max-turns` as Claude Code launch caps. Whether they bound a workflow run is unverified. The SDK `maxBudgetUsd` is a client-side estimate that can overshoot (verified in the TypeScript reference).
- **Script options and primitives not on the public page.**
  - `isolation:'worktree'`, `agentType`, `model`, `effort`, `budget` (`total`/`spent()`/`remaining()`) and `workflow(ref,args)` nesting do not appear on the public workflows page.
  - They appear in the session's workflow-authoring skill, which also gives the same 1000-agent and 16-concurrency caps.
  - The third-party `ultracode-workflows` repo (archived 2026-10-15) says nesting is one level deep.
  - Treat them as skill-documented but unverified on the public page.
- **Workflow tool schema.** The Agent SDK reference was truncated before the Workflow entry, so the tool's input and output schema is unverified.
- **Hooks inside workflow agents.** Hooks do run inside ordinary subagents, with `agent_id` and `agent_type` in the input, and `SubagentStart`/`SubagentStop` exist (docs verified for subagents). The docs read in this research are silent on workflow agents. Unverified: whether they fire there, whether `agent_id` appears, and whether the agents share the parent's MCP connection.
- **Showcase claims.** The Bun port from Zig to Rust (99.8% of tests, about 750k lines, 11 days) and the Klarna and CyberAgent quotes are vendor marketing. They were not independently checked.
- **Third-party descriptions.** The "research preview since late May 2026" wording comes only from mer.vin. The claim that open-source ports reimplement the pattern is not supported by any cited source and is dropped.

---

## 2. Comparison with similar features in other agents

Legend: Y documented, P partial, model-driven or community, N not found, ? unverified.

| System | Scripted, deterministic | Fan-out | Structured output | Resume | Budget | Worktree isolation |
|---|---|---|---|---|---|---|
| Claude Code workflows | Y | Y (16 concurrent, 1000 per run) | Y (schema, 5 attempts) | Y (journal replay) | P (agent caps, advisory warning) | Y per skill, ? on public page |
| Claude Agent SDK | Y (you write code) | Y | Y (`outputFormat`) | Y (`resume`, `fork`) | Y (`maxBudgetUsd`, overshoots) | P |
| Codex CLI | N (model-driven subagents) | Y (`max_concurrent_threads_per_session`) | P (`exec --output-schema`, reported) | P (`exec resume`, reported) | N | ? |
| Gemini CLI | N | ? | N (headless JSON output only) | N | P (`max_turns`, `timeout_mins`) | N |
| Cursor 2.0 | N (GUI) | Y (8 agents) | N | N | N | Y |
| Copilot CLI | N | P (`/fleet`, reported, unverified) | N | N | N | ? |
| Kimi | N (learned orchestrator) | Y (vendor claims 100 to 300) | N | N | N | N |
| Qwen Code, opencode, Amp, Windsurf, Antigravity | N | P | N | opencode P (persistent sessions) | N | Windsurf Y (reported) |
| Goose | P (declarative YAML subrecipes) | Y (experimental, up to 10 workers) | ? | ? | N | N |
| Aider | N (shell or Python scripting) | N | N | N | N | N |
| pi-ultracode (3rd party, Pi) | Y | Y (128 agents, 16 concurrent) | Y | Y (structural identity) | N (observability only) | Y |
| LangGraph, Temporal, OpenAI Agents SDK | Y (libraries you code against) | Y | Y | Y (checkpointers, durable replay) | P | N |

Reading the table:
- No mainstream coding agent ships a first-class, model-authored, deterministic, resumable orchestration script. Most have model-driven subagents where the model decides turn by turn.
- The nearest peers are the third-party pi-ultracode extension (README fetched, verified) and the frameworks, which are programmed by developers rather than authored by the agent.
- Cells for Gemini parallelism, Copilot `/fleet`, Amp, Windsurf, Kimi CLI swarm and the Codex exec flags rest on search summaries or single community sources. Verify with `codex exec --help` and `gemini --help`.
- Vendor numbers such as Kimi's 4.5x speedup are unaudited.
- The pi-ultracode replay rule is the same as Claude's, which makes it a usable prior-art contract.
- The literature cautions about parallel writers. Anthropic reports about 15x chat tokens for multi-agent runs, and Cognition (2026-04-22) calls unstructured swarms mostly a distraction. Both are repo-recorded summaries (`docs/ddflow/research/swarm-orchestration.md` 1.2). Fan-out pays most for read-heavy work, adversarial verification and clean-context review.
- Existing repo research already covers most of the landscape: `docs/ddflow/research/swarm-orchestration.md` and `docs/ddflow/research/pi-dev.md`.

---

## 3. What ddflow already has

**Shipped (code or docs read):**
- Claim, lease and heartbeat, a worktree per item, the needs and wave DAG, and declared globs. Overlapping globs are refused at claim (`core/admission.py`), with shared globs exempt.
- Gate pipeline: every gate must carry an outcome, skips need a reason, and reviewer independence by model family is enforced (`services/gates/`).
  - Chunked parallel model review (`services/review.py`), range reviews through roborev, and test selection.
  - ddmin culprit search (`services/bisect.py`), kind pipelines, and cadence passes.
  - This is the deterministic analogue of ultracode's adversarial verification, but bound to item and diff review, not arbitrary fan-out.
- Merge queue (`ddflow queue add|run|status|remove`): a serial integrator that runs `ci` once per entry, with `max_batch` as a loop bound.
- Atomic multi-record writes in `api/batch.py` (`ddflow batch`, `fold`). The name clashes with gate batching.
- Detached jobs (`ddflow_job_run`, `ddflow_job_list`) and `ddflow_loops`, which detects repetition. `ddflow_wait` waits for a claimable lease, not for a job.
- A read-only lane ledger and advisor (`services/lanes/`) that never spawns or kills anything.
- Concurrency primitives.
  - `services/slots.py` is a cross-process flock counting semaphore.
  - `schedule.parallel` and `[ci].max_concurrent_suites` limit concurrency, and `suitelock.py` already implements a FIFO queue.
  - Declared `--resources` capacities.
  - The three limiters are not unified. That statement in the raw findings was refuted, and only `cmdrunner.py` and `suitelock.py` use `Slots`.
- Approvals by digest (`services/approval.py`): single-use, agent identities refused, and forgery visible rather than impossible. Remote delivery (`P-remote-approvals`) is still open.
- The event log is the shared blackboard: append-only, sharded per agent, committed, with a blob store (`infra/blobstore.py`).
  - Event kinds are declared as TOML in `ddflow/templates/events/<owner>.toml` (`core/eventkinds.py`).
- Reviewer transports in `services/review.py`, with PRESETS for openai-compatible, anthropic, gemini and command kinds, plus local servers (ollama, llamacpp, vllm, litellm).
  - There is an off-contract rule: a missing `STATUS:` line means the review did not happen.
  - The transport is one-shot text in and text out, with no tool-use loop and no JSON-schema mode. The parser is review-specific.
- 23 harness descriptors (`ddflow/harnesses/*.toml`). They cover files, hooks and identity, not launch, stream, resume or limits.

**Designed or decided, not built (task states read with `ddflow show`, 2026-10-26, in this worktree):**
- A deterministic scheduler with pull lanes (D-swarm-answers: SW-pull, SW-conformance), open.
- The driver layer (D-DRV-1..8, decided, option C with a plugin SDK): DRV-engines, DRV-lane-interface, DRV-plugin-sdk, DX-fleet-registry and AC-backend are open. There is no agent launcher in the tree.
- Herdr integration (HD-plugin, phase P-herdr) is open. The Herdr probe has not been run.
- Batched gates (BG-ci, B-batch-integration) are open.
- Token/cost accounting as an enforced input: TE-limit-aware-routing and TE-auto-resume are planned.

**Missing relative to ultracode:** a script runtime. ddflow has no `agent()`, `parallel()` or `pipeline()` combinators, no schema-validated sub-agent outputs, no journal or resume for agent fans, no agent-count cap and no run-level token budget.

**Design tension.** The accepted swarm decision rejects an LLM-driven orchestrator, "option C", except for read-only research items. The reasons are about 15x tokens, non-determinism, the single-writer leases and unauditable fan-out. An ultracode-like mode that lets agents author scripts is a form of option C. Any ddflow adoption must therefore confine it to read-only fan-out (research, review, verification), or route every write through a claimed item, a worktree and the gates.

**Naming.** `ddflow_workflow`, `_state`, `_pipeline`, `_gate` and `_drop` already mean the project's gate-pipeline configuration (`ddflow/api/workflow.py`, `surfaces/declared/hooks.py`). A new orchestration feature must use another noun, for example `program`.

---

## 4. Using it from ddflow when the host is Claude Code

### 4.1 Where it fits

- **Wave fan-out.** A saved script reads `ddflow next` and runs `pipeline(items, claim -> implement -> gates -> review -> merge -> complete)`. One agent per item replaces the hand-written wave loop described in `docs/ddflow/drivers/deltas/claude-code.md`.
- **Gate fan-out.** Verify and review panels map onto `parallel()` agents, with `agentType` naming the shipped `ddflow-implementer` and `ddflow-reviewer` agents.
- **Read-only research.** Multi-lens research, bug hunts and dedupe sweeps are the best fit and fit the decision's read-only carve-out.
- **Role.** The runtime can be a scheduler and executor. It must never be the source of truth. Leases, gates, merge and the event log stay in ddflow.

### 4.2 Integration points, by effort

1. Docs and templates only (small).
   - Ship a `workflow-wave` recipe and a saved `.claude/workflows/ddflow-wave.js` through `ddflow adopt`.
   - Each stage prompt puts `as_agent=<role>-<item>` into every ddflow call.
2. A `ddflow next`-style read that returns a conflict-free wave with tier or model hints. The scheduler pieces already exist in `core/schedule.py` and `core/admission.py`.
3. A run-recording event family (`workflow.run.*`: run id, script digest, agent-to-item map). Nothing records runs today. The name must avoid "workflow".
4. A spend link to `services/quota.py` and the token-spend work.

### 4.3 Risks, with the code that decides each

- **Identity collisions are silent.**
  - Several agents on one tree merge into one identity with no error, so a review gate can compare the author with itself and pass (`ddflow/surfaces/mcp.py`, `Server._caller` and `_someone_else`).
  - Every workflow prompt must carry a unique `as_agent`, matching `[A-Za-z0-9._-]{1,64}` (`core/agentname.py`).
  - Workflow agents must not call `ddflow_identify`.
- **Double-worktree hazard (code read, runtime behavior unverified).**
  - For a foreign `as_agent`, `_invoke_marked` forces the caller location to the primary checkout, so `ddflow_claim` creates a second tree instead of adopting the runtime's `isolation:'worktree'` tree (`api/lifecycle/claim.py`, `infra/worktree.py`).
  - Gates and merge then act on a tree the agent did not edit.
  - Options: (a) skip `isolation:'worktree'` and have the agent `cd` into the tree `ddflow_claim` returns, or (b) have the agent use the CLI from inside its isolated tree, where `ddflow claim` adopts it.
  - Option (b) needs `DDFLOW_AGENT` set per command, because harness identity is per harness process (`infra/harness_identity.py`).
  - The biggest code change would be letting `ddflow_claim` bind an explicit tree when `as_agent` is foreign.
- **Lease races.**
  - Acquisition is check-then-act inside `EventLog.transaction` (flock, `services/leases.py`).
  - With 16 concurrent agents the real risk is lock timeouts and refused claims, not double claims.
  - Expiry never steals work (`reclaim_policy=report`).
  - Scripts must treat a claim refusal as normal and must not retry in a tight loop.
  - Scripts should pre-filter the wave to disjoint globs.
- **Heartbeats and stall restarts.**
  - A workflow agent idle in a long LLM turn can let the lease lapse.
  - The runtime's stall auto-restart reruns the prompt from scratch, so claims must be idempotent.
  - `_renew_in_place` and `worktree.create` are idempotent, but the retry must pass the same `as_agent` and item.
- **Reviewer independence.**
  - `reviewer_independence` (`services/gates/reviewers.py`) compares the author's model with the reviewer's recorded model. An `inherit` model cannot satisfy it (D-subagents).
  - A workflow `agent()` that omits `model` inherits the author's.
  - An Anthropic-only runtime cannot supply another family. Whether `opts.model` accepts non-Anthropic ids is unverified.
  - Critic and rubber_duck gates still need Kimi or Codex through `ddflow_review` (command reviewers are operator-only, D-reviewer-trust).
- **Cost.**
  - Measured in ddflow's own sessions (`docs/ddflow/research/token-spend.md`): subagents were 93% of weighted spend, and 52% of subagent spend was cache rewrites after the 5-minute TTL.
  - Workflow agents carry the same 5-minute TTL. D-TE-1 already decided `subagentPromptCacheTtl=1h`.
  - Ultracode suppresses the Large-workflow warning, so ddflow should not enable it implicitly.
  - Cache-prefix sharing needs identical `agentType` and cwd across the fan-out.
  - Workflow runs were not measured in the token study.
- **Resume versus the event log.**
  - The runtime journal is session-bound and keyed by prompt hash. The ddflow log is committed and authoritative.
  - Rule: the journal may cache LLM outputs only. Every state change must be an idempotent ddflow call whose result is re-derived with `ddflow_show` on rerun.
  - A resumed prefix returns cached results without re-running ddflow calls, so a cached "claimed" can be stale after a lease expiry. The script must re-verify with `ddflow_show` or a heartbeat.
- **Approvals and triggers.**
  - The keyword does not work from `-p`, scheduled prompts or relayed text.
  - `/loop`- or schedule-driven `implement` needs a saved workflow with a `Workflow` allow rule, or auto or bypass mode.
  - ddflow MCP tools must be on the allow list, or agents prompt mid-run.
- **Long MCP operations.**
  - Heavy tools (`gate_run`, `review`, `ci`) are offloaded to worker processes with progress frames (`surfaces/mcp.py`), so one stdio connection is not blocked.
  - Polling is the only portable completion contract (`docs/ddflow/research/mcp-long-ops.md`).

### 4.4 Unverified, needs a probe

- Whether workflow agents share the parent's MCP connection or start their own ddflow server.
- Where `isolation:'worktree'` trees live and what branches they use.
- Whether ddflow's PreToolUse and commit hooks fire in workflow agents, and whether `agent_id` is in the payload. ddflow's `services/claudehooks.py` does not read `agent_id` today.
- Whether `agent()` accepts env or cwd options.
- Whether the runtime can export its journal.

---

## 5. A ddflow-native equivalent for all agents

### 5.1 Verdict

Feasible, but as a deliberately narrower runtime. ddflow can mimic the control-flow shape: scripted fan-out, schema-checked outputs, a resumable journal, caps and budgets. It cannot mimic the host agent deciding on its own to think in programs. Build it as an external runner that drives agents through their headless interfaces and records everything in the event log. A rough estimate (unmeasured, low confidence) is about 12 to 16 tasks and 4 to 6k lines with tests.

### 5.2 Design

- **Naming and surface.** The noun is `program`, under `.ddflow/programs/`.
  - Long-op shaped tools that return a handle and are polled: `ddflow_program_run`, `_status`, `_resume`, `_stop`, `_list`. The tool-diet research favours one tool with an action argument.
  - The existing `ddflow_workflow*` family stays untouched. Docs say "workflow means gate rules, program means a scripted multi-agent run".
- **Language, two layers.**
  - Layer 1 (MVP): declarative TOML with phases, steps (lane, prompt template, schema, `needs`, `foreach`, `until = "dry"`, `join`) and a budget. It is static and so trivially resumable, reviewable by digest, and matches D-DRV-7 (data, not code).
  - Layer 2 (later): restricted Python programs run in a deny-by-default subprocess, operator-approved by digest. Choosing JS would add a Node dependency and a sandbox ddflow lacks. Dynamic fan-out and loop-until-dry are natural in code and awkward in TOML.
- **Primitives** map onto the Claude script API.

  | Claude primitive | ddflow program |
  |---|---|
  | `agent` | A step with lane, schema and timeout |
  | `parallel` / `pipeline` | `foreach` and `needs` |
  | `phase` / `log` | Labelled journal spans |
  | `budget` | Hard stop that writes an event |
  | `workflow()` | `include`, with a depth cap (about 3) and an agent-count cap |

  Budget accuracy is limited: `core/budget.py` is a chars-divided-by-4 estimate. Real spend needs usage data from harnesses (the ATF-* work).
- **Launching agents, three tiers, in build order.**
  - T1 (MVP): the reviewer transports in `services/review.py` give read-only thinker agents (researchers, critics, classifiers, planners) with no new launcher. They need a JSON-schema mode and a generalised parser.
  - T2: headless coding subprocesses (`claude -p`, `codex exec`, `gemini`, opencode, qwen, kimi) through the Lane interface. This is blocked on DRV-engines and DRV-lane-interface, both open.
  - T3: Herdr, ACP and tmux lanes, and plugin lanes through DRV-plugin-sdk.
  - A step declares required capabilities. A lane lacking one is reported `unavailable`, as D-DRV-4 requires.
- **Structured output for weak models.** Use the strongest available mechanism and record which one was used:
  - native schema or response format where the route supports it;
  - constrained decoding on local servers (llama.cpp grammar, vLLM guided_json, Ollama format), unverified per server;
  - a prompt contract plus a ddflow parser with a bounded repair loop;
  - a formatter pass by another lane.
  - From `review.py`: invalid output is off-contract and never counts as a pass.
- **Journal in the event log.**
  - New kinds go in `ddflow/templates/events/program.toml`: `program.started`, `.phase`, `.agent.started`, `.agent.finished`, `.log`, `.budget` and `.finished`. Bulk goes to the blob store by reference.
  - Step key = hash of program path, loop index, input digest and prompt digest. A matching `finished` event returns the recorded output with no model call. A changed key invalidates that step and everything after it, as Claude's replay rule does.
  - A TOML file alone is not enough. `eventkinds.problems()` requires a fold handler in `core.model.HANDLERS` and the fixture `tests/fixtures/event_kinds.json` to be updated. The claim that a new phase edits no core code was disputed on this point.
  - Shard per run so concurrent agents do not contend. Concurrent high-frequency appends from many processes are untested.
- **Concurrency.** Take the minimum of the per-program cap, the per-lane cap, `schedule.parallel` and the agent-count cap. Reuse `slots.py`. Back off on HTTP 429 and fail over lanes.
- **Writers.**
  - Read-only steps need no isolation.
  - Any step that writes runs under a claim: an item with declared globs, a worktree, and the normal gates and merge. Programs therefore cannot bypass leases or gates.
  - Overlapping-glob fan-outs are refused or serialised at plan time.
  - Worktrees do not isolate ports, databases or network.
- **Mixed model fleets.** Programs name roles (`cheap_bulk`, `strong_planner`, `critic`), mapped to (harness, model, route) lanes through the DRV catalog. A `verify()` helper runs N verifiers from distinct families with a quorum, reusing the family-independence rule.
- **Approvals.** The program digest (script, resolved lanes, budget, fan-out bound) is approved once through `services/approval.py`. Writer or high-spend programs require it. Small read-only T1 programs may be auto-approved by config. The digest subject type is new (inferred, not yet existing).

### 5.3 Phases

| Phase | Scope | Notes |
|---|---|---|
| 0 | Decision record and naming | 1 task |
| 1, MVP | TOML loader and lint, runner (`agent`/`parallel`/`pipeline`/`phase`/`log`) over T1, schema validation and repair loop, journal and resume, budget and caps, program tools, approval gate, tests with a stub lane so CI needs no model | About 5 to 7 tasks, about 1.5 to 2.5k LOC |
| 2 | Headless coding lanes after DRV-lane-interface, claim-backed writer steps, nested programs, `verify()`, loop-until-dry | About 4 to 5 tasks |
| 3 | Restricted Python programs, Herdr and ACP lanes, plugin lanes, quality-ledger routing, remote approvals | Later |

Build the journal, resume and schema-enforcement core first, against a stub lane. They are the parts hardest to retrofit.

### 5.4 Honest limits (cannot be unified)

1. The host agent's standing-mode behavior (deciding on its own to write scripts and showing progress inline) belongs to the host. ddflow offers a tool plus prompt guidance only.
2. Forced schemas and prompt caching are provider features. ddflow can validate and repair, but cannot make a weak model comply. Repair costs tokens.
3. Spend accounting is exact only where the harness reports usage. Hard budget stops are best-effort for CLI agents without usage output.
4. Mid-conversation resume is impossible for most CLIs. ddflow resumes at step granularity by rerunning the interrupted step.
5. Determinism applies to the orchestration, never to model outputs. Impure Layer 2 code breaks replay.
6. ddflow is not a sandbox. Isolation is worktrees plus leases, with no port, database or network isolation and no defense against a hostile agent with a shell.
7. Parallel writers stay contested, and about 15x token cost applies regardless of runtime. Fan-out pays for read-heavy work, review and verification.
8. Lane capabilities vary, so some steps can only report `unavailable`.
9. Learned orchestrators (the Kimi swarm) cannot be mimicked. Only the control-flow shape can.
10. MCP clients cannot be pushed to, so completion must be polled.

---

## 6. Recommendation and decisions the operator must make

**Recommendation.** Do both, in this order.
1. Cheap, reversible Claude Code integration first: a saved wave script plus identity and worktree guidance, confined to read-only fan-out. This costs little and tests the runtime against ddflow's rules.
2. Then build the MVP program runtime (Phase 1). It is useful with T1 thinker lanes alone, so it does not wait on the open DRV tasks.
3. Do not pursue a clone of ultracode's standing mode.

Questions, each with a default:

1. **Scope of adopting Claude's runtime.** Allow workflow fan-out only for read-only research, review and verification, or also for writers? Default: read-only only. Writers wait for the claim-binding change in 4.3.
2. **Claim-binding change.** Allow `ddflow_claim` to adopt an explicit tree when `as_agent` is foreign? Default: yes, as a filed task (the biggest code change).
3. **Shipping `.claude/workflows/ddflow-wave.js` through `ddflow adopt`.** Default: yes, behind a documented opt-in, with a `Workflow(ddflow-wave)` allow rule offered.
4. **Enabling ultracode for ddflow sessions.** Default: no. Use the keyword or saved workflows per task. Set `subagentPromptCacheTtl=1h` for any workflow use, per D-TE-1.
5. **Native mimic.** Build the `program` runtime? Default: yes, MVP only, after a Phase 0 decision record. Reconcile with D-swarm-answers Q1, which keeps option C for read-only items.
6. **Name.** `program`, not `workflow`. Default: `program`.
7. **Language.** Declarative TOML first, restricted Python later, never JS. Default: as stated.
8. **Which lanes first.** T1 direct model calls, then T2 after DRV-lane-interface. Default: T1 first.
9. **Cross-family reviewers inside programs.** Require an explicit declared model per verifier, and refuse `inherit`. Default: yes.
10. **Run recording.** Add the `workflow.run.*` or `program.*` event family, with a retention plan for high-volume events. Default: yes, with compaction after a run ends.
11. **Probe before building.** Run a read-only probe workflow to settle the 4.4 unknowns (MCP sharing, hook firing, `agent_id`, worktree location). Default: yes, before any wave script ships.

---

## 7. Defects and gaps found in ddflow worth filing

1. **Identity is not per workflow agent.** Harness identity records are per harness process, so every shell under one harness shares a name unless `DDFLOW_AGENT` or `--agent` is set per command. Hooks do not read `agent_id` (`infra/harness_identity.py`, `services/claudehooks.py`). This matches the known identity bug.
2. **Foreign `as_agent` claim forces the primary checkout.** `ddflow_claim` creates a second worktree and does not adopt the caller's tree (`surfaces/mcp.py` `_invoke_marked`, `api/lifecycle/claim.py`). Enhancement: an explicit adopt or worktree argument.
3. **No SubagentStart handler.** `docs/ddflow/research/requirements-management.md:216` says ddflow has none. A workflow-aware identity story would use it.
4. **Stale research doc.** `docs/ddflow/research/batch-gates.md` section 3 says the suite lock has "no FIFO (fix-B1fa77a6832 in flight)". `services/suitelock.py` already has a FIFO queue with positions and grace (verified in code, HEAD 4aee0038f).
5. **Documentation overstatement.** `services/slots.py` says its three limiters "exist or are planned", and only `cmdrunner.py` and `suitelock.py` use `Slots`. Do not describe the limiters as unified.
6. **Event-kind extension is not data-only.** Declaring a kind in TOML needs a fold handler in `core.model.HANDLERS`, an upcaster entry and a fixture update, or `eventkinds.problems()` fails. This should be documented where the docstring says a new phase edits no core.
7. **Naming collision.** "Workflow" means gate rules in ddflow (`ddflow_workflow*`) and orchestration scripts in Claude Code. `docs/ddflow/decisions/shared-engines.md` already uses "workflow packs" and "workflow-template engine" loosely. A glossary entry would help.
8. **No generic structured-output transport.** Reviewer transports have no JSON-schema or response-format support and no tool-use loop. The parser is `STATUS`/`FINDING`-specific.
9. **No run ledger for external orchestration.** A Claude workflow run leaves nothing in the event log: run id, script digest, agent-to-item map. There is no doctor check for orphan leases whose holder was a dead run.
10. **No token-spend enforcement.** TE-limit-aware-routing, TE-auto-resume, ATF-usage-fields and ATF-limit-signals are open. Without them a fan-out cannot be budgeted.
11. **`ddflow_wait` and `ddflow_job_list` semantics are easy to conflate.** `ddflow_wait` waits for a claimable lease. The `job_run` text says to wait with `job_list` rather than poll, and it is unverified whether `job_list` blocks. Clarify the text or add a job wait.
12. **Process note.** The first four gate skips printed no approval id (from the preceding session summary). That is worth a bug if it reproduces.

**Gaps in this research.**
- Claude Code: the Workflow tool schema, per-agent options on the public page, hook behavior in workflow agents, and whether non-Anthropic model ids work in `opts.model`.
- Other agents: the official docs for Amp, opencode, Kimi CLI, Windsurf, Antigravity, Copilot `/fleet` and Goose schemas were not read. The Codex and Gemini headless flags come from third-party guides.
- Framework claims (LangGraph Send API, Temporal replay) were not checked against primary docs.
- No measurements. The LOC and task estimates are rough, and the Herdr probe has not been run.
- Task states were read in this worktree. Main may have moved.

The finished document is intended for `docs/ddflow/research/ultracode.md`. It is not written to disk.