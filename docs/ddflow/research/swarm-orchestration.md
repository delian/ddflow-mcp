# Swarm orchestration: ddflow distributing work across local and remote agents of several families

Task DX-swarm-research (merges DX-mixed-fleet-research, see section 8). Operator question, 2026-10-24, verbatim in
the session log. Status of this file: **part 1 (landscape, what ddflow has, hooks answer)**. Part 2 (algorithms,
data and session sharing, proposal, options, tasks) follows in a second merge; section 9 lists what is still open.

Evidence marks. **VERIFIED** = the primary page was fetched on 2026-10-24. **REPORTED** = secondary source or search
snippet only. **UNKNOWN** = not found. Important caveat: the fetch tool used here (WebFetch) returns a small model's
SUMMARY of a page, not raw text, so VERIFIED means "primary URL fetched, summary read", not "raw bytes read", except
where a row says raw. Search output is also summarised, so anything seen only in search is REPORTED. Nothing was run or
installed; no key was used.

## 1. Landscape

### 1.1 Kimi Agent Swarm (the operator asked specifically)

| Claim | Mark | Source |
|---|---|---|
| K2.5 "Agent Swarm": a trainable orchestrator decomposes a task and runs the parts on frozen sub-agents created dynamically, no predefined roles or workflows; up to 100 sub-agents and up to 1,500 coordinated steps (the page says "tool calls" in one place and "steps" in another) | VERIFIED (summarised) | https://www.kimi.com/blog/kimi-k2-5 |
| Trained with PARL (parallel-agent RL). Reward = instantiation reward (against "serial collapse"), finish-rate reward (against "spurious parallelism"), task outcome; the two auxiliary weights are annealed to zero | VERIFIED (summarised); exact schedule REPORTED from third-party write-ups, paper not read | same |
| "Critical Steps" metric: orchestration overhead plus the slowest sub-agent per stage; more sub-agents only help if the critical path shortens | VERIFIED (summarised) | same |
| Claimed 4.5x faster, 80% less runtime; Moonshot's own numbers, unaudited; "research preview" in beta | VERIFIED as a claim | same |
| Later limits: up to 300 sub-agents and 4,000+ parallel tool calls; help page lists a "K3 Swarm" model; which model/plan powers it is not stated | VERIFIED (help page, summarised); model attribution REPORTED | Kimi help page; KDnuggets snippet |
| Weights are open (Modified MIT); the swarm orchestration is NOT documented as part of the weights, it looks like a product feature. Whether an API exposes it or the training recipe is released | UNKNOWN | https://huggingface.co/moonshotai (model page) |
| Kimi CLI: legacy `MoonshotAI/kimi-cli` archived 2026-09-23, no swarm mention; successor `MoonshotAI/kimi-code` dispatches built-in `coder`, `explore`, `plan` sub-agents in isolated contexts; an unofficial cheat sheet claims an `AgentSwarm` tool | CLI sub-agents VERIFIED (summarised); `AgentSwarm` tool REPORTED (conflicting sources) | GitHub READMEs |

Reading for ddflow: Kimi's swarm is a model-level, in-product parallelism where the LEARNED orchestrator decides the
fan-out and the metric is critical-path length. ddflow cannot reuse the weights or the product, but two ideas transfer
directly: score a plan by its critical path, not by agent count; and penalise both serial collapse (one agent doing
everything) and spurious parallelism (many agents, no shorter path). ddflow already has the explicit graph (needs,
waves, globs) that Kimi has to learn.

### 1.2 Vendors and products

| System | What it does | Mark | Source |
|---|---|---|---|
| Anthropic multi-agent research system | Orchestrator-worker, Opus lead with Sonnet workers; +90.2% over single-agent on their internal eval; agents about 4x chat tokens, multi-agent about 15x; token use explained 80% of variance on BrowseComp; effort scaling rules; says it fits poorly where agents share context or have many dependencies, "most coding" included | VERIFIED (summarised) | https://www.anthropic.com/engineering/multi-agent-research-system |
| Claude Code subagents | Own context window, result returns as summary, nesting up to 3 layers, 20 concurrent by default, worktree isolation option, resumable | VERIFIED (summarised, first 100k of 116k chars) | https://code.claude.com/docs/en/sub-agents |
| Claude Code agent teams | Experimental, off by default: lead plus teammate sessions, shared task list with dependencies and file-lock claiming, JSON mailboxes, hooks `TeammateIdle`, `TaskCreated`, `TaskCompleted` (exit 2 blocks); no nested teams, one team per session, teammates not restored on resume; advice 3 to 5 teammates, avoid same-file edits | VERIFIED (near raw) | https://code.claude.com/docs/en/agent-teams |
| Cognition, "Don't Build Multi-Agents" (2025-06-12) | Share full traces not messages; actions carry implicit decisions that conflict; default to a single-threaded agent | VERIFIED (summarised) | https://cognition.com/blog/dont-build-multi-agents |
| Cognition update (2026-04-22) | Parallel-writer swarms still little adopted; what works: clean-context code-review loop, "smart friend" (stronger model with forked context), manager-child delegation; unstructured swarms "mostly a distraction" | VERIFIED (summarised) | https://cognition.com/blog/multi-agents-working |
| Devin | Coordinator session delegates to "managed Devins", each in its own VM; coordinator monitors, resolves conflicts, compiles; children have own prompts, playbooks, ACU limits | VERIFIED (summarised); spawn/track API UNKNOWN | https://docs.devin.ai/work-with-devin/advanced-capabilities |
| OpenAI Agents SDK | Handoffs as `transfer_to_<agent>` tools (control moves; receiver sees history, filterable); agents-as-tools keeps control with the manager; code-driven orchestration (chaining, evaluator loop, `asyncio.gather`) | VERIFIED (summarised) | https://openai.github.io/openai-agents-python/handoffs/ |
| Codex subagents | Spawned on request or by AGENTS.md/skill; `agents.max_concurrent_threads_per_session`; inherit sandbox and approvals; recommended for read-heavy work, caution on write-heavy; costs more tokens | VERIFIED (summarised); nesting depth UNKNOWN | learn.chatgpt.com Codex subagents page |
| Cursor 2.0 / cloud agents | Up to 8 agents in parallel on one prompt using worktrees or remote machines; cloud agents run in isolated VMs and produce a PR per branch | VERIFIED (summarised) | https://cursor.com/changelog/2-0 ; https://cursor.com/docs/cloud-agent |
| OpenHands | `DelegateTool` sub-agents in parallel threads | REPORTED (docs URL 404 on fetch); see also docs/ddflow/research/openhands.md | search digest |

### 1.3 Protocols

| Protocol | Facts that matter to ddflow | Mark | Source |
|---|---|---|---|
| A2A 1.0.0 | Agent Card (signable JSON metadata), task states SUBMITTED, WORKING, INPUT_REQUIRED, AUTH_REQUIRED, COMPLETED, FAILED, CANCELED, REJECTED; SSE streaming; push webhooks; auth API key/HTTP/OAuth2/OIDC/mTLS; bindings JSON-RPC, gRPC, HTTP+JSON. Linux Foundation project, vendor neutral | VERIFIED (summarised, first 100k of 256k chars); LF membership list REPORTED | https://a2a-protocol.org/latest/specification/ ; Linux Foundation press release 2025-06-23 |
| Google ADK | A2A support marked experimental (Python, Go, Java) | VERIFIED (summarised) | https://adk.dev/a2a/ |
| MCP Tasks | Now an extension `io.modelcontextprotocol/tasks` (draft): server decides per request to return a task; `taskId`, `ttlMs`, `pollIntervalMs`; `tasks/get`, `tasks/update`, cooperative `tasks/cancel`; states working, input_required, completed, failed, cancelled | VERIFIED (near raw, draft spec may change) | https://modelcontextprotocol.io/specification/draft/basic/utilities/tasks |
| MCP sampling, elicitation | `sampling/createMessage` (human in the loop expected), `elicitation/create` form and url modes; secrets must not go through form mode | VERIFIED (raw, 2025-11-25 spec) | modelcontextprotocol.io specification pages |
| ACP (Agent Client Protocol) | JSON-RPC over stdio for local agents, remote HTTP/WebSocket in progress; `session/new`, `load`, `prompt`, `cancel`, `set_mode`, `request_permission`, `update`; Apache-2.0, protocol v1 | VERIFIED (summarised); Zed/JetBrains origin REPORTED | https://agentclientprotocol.com/protocol/overview |

### 1.4 Fleet tools and frameworks

| System | Notes | Mark | Source |
|---|---|---|---|
| Gas Town (Yegge) | Roles Mayor, Polecats, Witness, Refinery, Deacon; hooks are git worktrees; the Refinery is a Bors-style bisecting merge queue, "polecats never push directly to main"; Beads is a git-backed tracker; runtimes include Claude, Gemini, Codex, Cursor; alpha, 20 to 50 agents REPORTED; its conflict handling UNKNOWN | VERIFIED (README, summarised) | GitHub README |
| Claude Squad | tmux session plus worktree per agent; Claude Code, Codex, Gemini, Aider; AGPL-3.0 | VERIFIED (summarised) | GitHub README |
| Vibe Kanban | README says it is sunsetting | VERIFIED (summarised) | GitHub README |
| Conductor | One worktree and branch per workspace; Claude Code and Codex | REPORTED | vendor/blog digests |
| Paseo | Local daemon with worktree flag and orchestration skills; official repo status | REPORTED / UNKNOWN (search returned forks); ddflow's own view is D-control-planes | n/a |
| LangGraph | Multi-agent doc lists Subagents, Handoffs, Skills, Router, Custom workflow | VERIFIED (summarised) | LangChain docs |
| CrewAI | Sequential and Hierarchical (manager plans, delegates, validates) | VERIFIED (summarised) | CrewAI docs |
| Microsoft Agent Framework | Successor of AutoGen and Semantic Kernel, graph-based workflows; pattern names UNKNOWN | VERIFIED (raw, dated 2026-07-29) | Microsoft Learn |
| Worktree-per-agent practice | Conflicts move to merge time; file ownership and task partitioning are the real control; worktrees do not isolate ports or databases | REPORTED | vendor and dev blogs |
| Contract net, hedged requests, work stealing, speculative execution for LLM agent runs | Hedging is documented for single LLM calls (cap about 10% extra requests); contract-net appears in two papers (not read); no shipping coding fleet found using any of them; work stealing and speculative whole-run execution: nothing found | REPORTED / UNKNOWN | literature search |

### 1.5 What the landscape agrees on

This is synthesis, not a sourced claim. (a) Every vendor gives workers an isolated context and returns a summary; the
parent keeps control. (b) Parallel WRITES are the contested part: Anthropic, Cognition, Codex and the Claude teams docs
all advise one writer or disjoint files. (c) What works in practice is read-heavy fan-out, clean-context review, and
manager-child delegation with explicit scope. (d) Token cost grows about 15x for multi-agent runs, so parallelism must
shorten wall time or raise quality enough to pay for it. ddflow already encodes the answer to (b): leases on declared
globs plus a worktree per item plus a merge queue. That is the Gas Town shape, with gates the others lack.

## 2. What ddflow has and plans (ids to reuse)

| Capability | Id | State |
|---|---|---|
| Claim, lease, heartbeat, worktree per item, merge gates, needs and wave DAG, file globs | core | shipped |
| Launching agents with consent, launch templates only the operator sets | D-agent-parity, `services/launcher.py` | accepted; launcher shipped, templates operator-owned |
| ACP client as lane driver, wrap proxy, thin ACP agent; auto-allow worktree plus lease-covered files | D-acp-r2, AC-backend | accepted; AC-backend open |
| Control planes (Paseo first), adopt foreign worktrees, trusted bots start agents from operator templates, scoped tokens, loopback default, no relay | D-control-planes, CP-* | accepted |
| Lane interface (LaunchSpec, observe, steer, cleanup), engines, model catalog, plugin SDK | DRV-lane-interface, DRV-engines, DRV-model-catalog, DRV-plugin-sdk (docs/ddflow/research/driver-architecture.md) | open |
| dsh as a lane worker | DSH-lane (research: docs/ddflow/research/dsh.md) | open |
| Limit-aware routing and auto-resume | TE-limit-aware-routing, TE-auto-resume | open |
| Batched gates across parallel items | P-batch-gates | open |
| Operator approvals from remote channels | P-remote-approvals, P-remote | open |
| Operator digest | P-operator-digest | open |
| Durable fleet registry, drain mode | DX-fleet-registry, DX-drain-mode | open |
| Session tree, on-demand fenced transfer, native lane status | D-session-context | accepted |
| Memory and handoff | P-agent-memory | open |
| Orphan recovery | P-orphan-recovery | open |
| Load-based parallelism cap, FIFO suite lock | `schedule.parallel`, `schedule.max_parallel_*` config | shipped |
| Tool-surface accuracy per model family | MTD-accuracy-eval (done), LS-* lean profiles | done / accepted |
| Per-harness descriptors with `[hooks]` | `ddflow/harnesses/*.toml` (23 files) | shipped |

Gap in one line: ddflow has a strong WORKER side (lease, worktree, gates, per-agent descriptors) and a planned LAUNCH and
OBSERVE side (lane interface, ACP), but no SCHEDULER that decides which agent family takes which ready item. Today the
human lead (or `ddflow next`) picks by queue order. That scheduler, plus quality and cost feedback from gate outcomes,
is the actual swarm feature.

## 3. Hooks with a Claude lead and non-Claude workers (the operator's hook question)

Short answer: yes for most agents, with three honest limits. The enforcement point that works everywhere is the git
commit hook plus the claim lease; agent hooks add earlier, friendlier signals where the agent supports them.

Per-agent hook strength, from the `[hooks]` section of each descriptor (shipped file, `ddflow/harnesses/<id>.toml`):

| Agent | Hook file ddflow writes | Style / block | Events (pre_tool = can block a tool) | Strength |
|---|---|---|---|---|
| Claude Code | `.claude/settings.json` | claude, exit 2 | SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PreCompact, Stop, SessionEnd | blocking pre-tool: full lease enforcement |
| Qwen Code | `.qwen/settings.json` | claude-compatible, exit 2 | same names as Claude | blocking pre-tool |
| Gemini CLI | `.gemini/settings.json` | gemini, exit 2 | SessionStart, BeforeAgent, BeforeTool, AfterTool, PreCompress, AfterAgent, SessionEnd | blocking pre-tool (Gemini stays supported beside Antigravity, D-agent-parity) |
| Codex | `.codex/hooks.json` | claude style, exit 2 | same names | blocking pre-tool |
| Cursor | `.cursor/hooks.json` | cursor, exit 2 | sessionStart, beforeSubmitPrompt, preToolUse, postToolUse, preCompact, stop, sessionEnd | blocking pre-tool; context inject only at session start |
| OpenCode, Kilo | `.opencode/plugins/`, `.kilo/plugin/` (JS plugin) | plugin, block by throwing | tool.execute.before/after, session.created, chat.message, compacting | blocking pre-tool, but it is executable code written into the project: needs operator consent and a shown diff (D-agent-parity) |
| Kimi | `~/.kimi/config.toml` (USER file) | claude style, exit 2 | Claude-style names | blocking, but the file is global; project-level hook file, additionalContext, session id env are unverified in the descriptor |
| dsh | `.ddflow/harness/dsh.hooks.json` via experimental bridge | claude style | SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop | experimental bridge; a bad config path runs NO hooks and the agent starts anyway (silent), per docs/ddflow/research/dsh.md |
| Grok | none: no descriptor hook entry yet | n/a | n/a | none; git hook and lease only; file as a gap |

How to install them when ddflow orchestrates (design, to be decided in SW-decide):

1. Lane-private hook config (ATF-Q2, accepted default in docs/ddflow/decisions/agent-token-features.md): the launcher
   writes the hook file INSIDE the lane directory and points the agent at it (`QWEN_HOME`, `KIMI_SHARE_DIR`,
   `GROK_HOME`, `CODEX_HOME`, `CURSOR_CONFIG_DIR`, `DSH_HOME` and similar, see agent-token-features.md), lists it in the
   lane record and removes it with the lane. This avoids touching the operator's global Kimi file and avoids project
   files that parallel lanes would share.
2. Project-level hook files (what `ddflow adopt` writes today) remain right for the operator's own interactive agent,
   not for fleet workers, because every lane would read the same file and the identity would be wrong.
3. Identity: every hook invocation must carry THAT lane's agent identity (an env var set by the launcher, for example
   `DDFLOW_AGENT`), otherwise the known identity bug applies (commands from one shell attributed to another agent). The
   Claude lead keeps its own hooks; lane hooks must not inherit the lead's.
4. Fallbacks, in order: the claim lease with its worktree (an agent that works only in its worktree cannot collide), the
   git pre-commit and commit-msg hooks inside the lane worktree (work for ANY agent, including Grok and cursor
   without hooks), and the merge gates. An agent without a blocking pre-tool hook can still edit a file outside its
   globs; the pre-commit hook then refuses the commit and the lane is told. That is detection at commit, not
   prevention at edit, and the docs must say so.
5. Consent: plugin-style hooks (OpenCode, Kilo) are code; they need the operator's consent and a diff once per
   template; a lane-private copy inside the lane directory makes that a one-time template approval, not a per-lane one.
6. Unverified facts stay marked in the descriptors; a lane conformance run proves a hook fires before the lane is
   trusted with a real item.

## 4 to 8. Parts that follow

4. Algorithms and policies. 5. Data and session sharing. 6. Remote workers and trust. 7. Proposal, options,
recommendation, pilot and metrics. 8. Folding of DX-mixed-fleet-research. Written in the second merge.

## 9. Open at this point

- Kimi: whether `AgentSwarm` is a CLI tool (conflicting sources) and whether a K3 swarm is reachable by API: UNKNOWN.
- OpenHands delegation docs: fetch returned 404; REPORTED only.
- Paseo official status: UNKNOWN.
- No agent was run; hook strengths come from descriptors and their recorded sources, not from a live conformance run.
