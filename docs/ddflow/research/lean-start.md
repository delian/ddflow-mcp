# Lean start: what a spawned agent carries before it works, and how to get under 32K tokens

Research item RM-lean-start-research, 2026-10-21. Operator, verbatim: "I have noticed that every new agent
you spawn (background tasks on claude) immediately starts with 64k tokens taken. Can you investigate how we can
optimize the tokens in the beginning of the sessions, still informing the models to ask us for additional
information and enforcing what we need, but looking into working with <32K tokens in the beginning of the
session at least so we could support models with smaller context windows?" and: "check the already planned
simplification of the mcp commands if that is going to help the P-lean-start ?"

Evidence labels: MEASURED = read from `claude -p ... --output-format json` usage on this machine
(Claude Code 2.1.296, model haiku, prompt "reply ok", cwd a worktree of this repository, `--no-session-persistence`,
one run per variant, so +-0.3K run noise); ESTIMATE = bytes/4 (no tokenizer is installed); UNKNOWN = not attributed.
"First-turn input" is input + cache_creation + cache_read tokens of the single request.

## 1. Measured breakdown

Probes (A..L are file names under the scratchpad; the variation is the only change from the row above it):

| run | variation | first-turn input |
|---|---|---|
| H2 | no built-in tools (`--tools ""`), no hooks, no skills, no MCP | 5.7K |
| H | `--tools Read,Bash`, same | 8.0K |
| J | all built-in tools, no hooks, no skills, no MCP | 22.7K |
| E | J plus skills list | 22.8K |
| B | E plus hooks (ddflow SessionStart etc.), still no MCP | 25.4K |
| A | B plus the user's claude.ai connectors and plugins (as-is in this worktree) | 37.2K |
| C | no connectors, ddflow MCP only (123 tools), tool search ON (default) | 29.3K |
| C2 | C with `ENABLE_TOOL_SEARCH=false` (every schema sent) | 84.8K |
| I | C with `ENABLE_TOOL_SEARCH=auto:90` (threshold above ddflow's size: nothing deferred) | 87.3K |
| L | J plus ddflow all 123 tools, nothing deferred | 84.2K |
| K | J plus ddflow `core` tier (32 tools, `DDFLOW_MCP_TOOLS=core`), nothing deferred | 54.7K |
| D | C with core tier, tool search ON | 27.9K |
| G | `--agent` with `tools: [Read, Bash, ddflow_brief, ddflow_claim]`, core server, hooks and skills on | 10.4K |

Derived per component (this machine):

| component | bytes | tokens | source of tokens | who controls it |
|---|---|---|---|---|
| Claude Code system prompt (no tools) | n/a | ~5.7K | MEASURED (H2) | Claude Code |
| built-in tool schemas + CLAUDE.md (2,537 B) + auto-memory index (1,365 B) | n/a | ~17K (J minus H2) | MEASURED, split UNKNOWN | Claude Code; CLAUDE.md and memory are the user's |
| skills list | small | ~0.1K | MEASURED (E minus J) | user config |
| hooks: ddflow SessionStart payload | 5,595 | 1.4K est; hook delta measured 2.6K | MEASURED (B minus E) | ddflow |
| claude.ai connectors (Figma, Linear, Gmail, Slack, Drive, Calendar, Context7, ...) as deferred names plus their server `instructions` | ~25 KB est | ~11.8K | MEASURED (A minus B) | user config |
| ddflow with tool search ON: instructions plus 123 deferred names | 12,387 + 3,656 | ~3.9K | MEASURED (C minus B) | ddflow |
| ddflow `instructions` | 12,387 chars | ~3.1K | ESTIMATE | ddflow |
| ddflow 123 tool schemas, nothing deferred | 104,285 | ~58.4K | MEASURED (L minus J minus instructions) | ddflow, plus the Claude Code deferral decision |
| ddflow core 32 schemas, nothing deferred | 34,745 | ~28.9K | MEASURED (K minus J minus instructions) | ddflow |
| ddflow `prompts/list` / `resources/list` | 5,331 / 4,227 | 0 observed in A..L (not listed in context) | MEASURED by omission | ddflow |
| launch brief of a subagent (`ddflow brief --for-agent`, cap 9,000) | 8,468 | ~2.1K | ESTIMATE; sent as the subagent prompt, not in the probes | ddflow |
| plain `ddflow brief` (1200-token cap) | 4,354 | ~1.2K | ESTIMATE | ddflow |

Reconciling with the 64K the operator sees: an as-is subagent here is 37.2K without ddflow, 41.1K with ddflow's
names deferred, and 96.6K with ddflow undeferred. 64K lies between, so the operator's subagents are neither fully
deferred nor fully loaded, or run a different model's system prompt and tokenizer (Opus). The measured switch is the
deferral rule: with tool search ON the 123 tools cost ~0.8K (names), with it off or not triggered they cost ~58K. Claude
Code's `auto` mode defers only when MCP definitions exceed a share of the window (default 10%), so on a 1M-window model
the same server is sent in full: this is a HYPOTHESIS (the mode is documented; the 1M behaviour was simulated with
`auto:90`, not run on a 1M model). What I cannot attribute: the ~14K between H and J split into built-in schemas and
CLAUDE.md; the exact Opus-versus-haiku difference; why core costs 0.83 tokens per byte and all costs 0.56 (both
measured; extrapolations below carry that range); whether the Agent tool's subagents receive the parent's CLAUDE.md
(not measured here; Claude Code documents that omitting `tools` in an agent definition inherits all tools including MCP).

Run G caveat: `--agent` also replaces the default main prompt with the agent body, so 10.4K conflates "restricted tool
list" and "short system prompt". Both are things a generated agent definition does; the split is UNKNOWN.

## 2. Levers, ranked by tokens saved per effort and risk

"Saved" is against the as-is subagent; deferred = Claude Code with tool search; undeferred = no tool search (1M models,
`ENABLE_TOOL_SEARCH=false`, Cursor/Copilot/local models).

| # | lever | who | saves (undeferred / deferred) | effort | risk |
|---|---|---|---|---|---|
| 1 | Subagent definitions with `tools:` allow-list (and `mcpServers`) generated for researcher / implementer / reviewer by `ddflow adopt/setup` | ddflow ships, Claude Code honours | up to ~27K / up to ~15K (G: 25.4K to 10.4K) | low | low: roles keep brief, claim, gate, bug, lesson tools |
| 2 | Default `[mcp].tools = core` for lane workers via the `DDFLOW_MCP_TOOLS` env in their MCP config (exists today, no code) | ddflow, user config | 27K (84.2K to 54.7K measured) / 0 | trivial | low: rare verbs need the CLI or a profile |
| 3 | Thin MCP `instructions`: a charter of <= 1.5K chars plus pointers (`ddflow_brief`, `ddflow_help`, resource), full text fetched on demand; knob `prompts.mcp_instructions` already exists | ddflow | ~2.4K / ~2.4K | low | low if the contract lines stay (see section 4) |
| 4 | SessionStart payload as a charter of <= 1K chars (brief, due passes and behind-main notices on demand) | ddflow | ~1.1K to 2.3K / same | low | low: gates still enforce |
| 5 | Disable unused claude.ai connectors for this project, `--strict-mcp-config` for lane workers | user config | 11.8K / 11.8K | trivial | none for ddflow |
| 6 | Launch brief sized to the model window: `[session].context_budget_tokens` or a model-to-window table | ddflow | ~1K to 1.5K | low | low |
| 7 | MCP tool diet: profiles lean/core, families, search/call facade | ddflow (P-tool-diet) | see section 3 | medium to high | medium |
| 8 | Tiered requirement injection (charter always, contextual on claim/glob, search on demand) | ddflow (P-requirements) | prevents growth; ~0 today | medium | low |
| 9 | Trim skills list | user | ~0.1K | trivial | none |
| - | Cached fast `initialize` (SU-init-fast) | ddflow | 0 tokens: it is a latency fix | - | - |

Levers 1, 2, 3, 4 and 6 need nothing from P-tool-diet and nothing from the operator's pending decision.

## 3. Does the planned MCP simplification (P-tool-diet) help?

Numbers from research mcp-tool-diet.md (bytes) converted with the measured ratios above; family and lean token counts
are ESTIMATES (range = the two measured ratios 0.56 and 0.83 tokens per byte, plus ~3.1K instructions).

| surface | tools | list bytes | tokens undeferred | tokens deferred (names) |
|---|---|---|---|---|
| flat `all` (today) | 123 | 104,285 | 61.5K MEASURED (58.4K + instructions) | ~0.9K |
| `standard` | 81 | 71,837 | ~43K to 63K est | ~0.6K |
| `core` (exists today) | 32 | 34,745 | 32.0K MEASURED | ~0.3K |
| A1 families (MTD-family-schema) | 24 | ~53,000 | ~33K to 47K est | ~0.2K |
| `lean` profile (MTD-profiles) | 15 | ~29,000 | ~19K to 27K est | ~0.15K |
| search/call facade (MTD-search-call) | 2 | ~1,100 + 13 KB catalog on demand | ~1K, plus ~7K to 11K once when a catalog is fetched | ~0.05K |

Answer, per client type:

- (a) Claude Code with tool search (names up front): the diet saves little. All of ddflow costs 3.9K there, of which
  ~3.1K is the server `instructions` and ~0.8K the 123 names; going to 24 families saves ~0.7K. The diet is not the
  lever that matters for the 64K seen unless the deferral is not triggered, which it is not on large-window models.
- (b) Clients without tool search (Cursor, Copilot, local models, 1M-window Claude): the tools list is the largest
  single item (58K of 84K). The existing `core` tier already removes 27K with one env var. A1 families do not beat
  core on bytes (53 KB against 34.7 KB); their value is count (24 against 123, under every client cap) and reaching every
  verb. Only `lean` and the facade get a no-tool-search client below ~25K from ddflow: the facade is the one design that
  leaves ~1K.
- Not touched by the diet: server `instructions` (3.1K), SessionStart payload (1.4K to 2.6K), launch brief
  (up to 2.1K), `prompts/list` and `resources/list`, the skills list, other connectors (11.8K), Claude Code's own system
  prompt and built-in tools (22.7K here). Those are P-lean-start's work.

Sequencing: P-lean-start needs MTD-profiles (lean profile, chosen from the client), MTD-search-call (facade for caps
and windows that cannot take a list) and, only for the "families" profile, MTD-family-schema. It does not need
MTD-decision for the work that is not a rename: measurement, thin instructions, charter hook, generated agent
definitions, core-by-default for workers and launch-brief sizing can start now.

## 4. What must stay: the contract in a minimal start

The charter (instructions and SessionStart together, target <= 600 tokens) keeps, in this order: call `ddflow_brief`
(or `ddflow brief`) first; claim before editing; ask the operator for anything missing, ambiguous or needing approval and
do not guess; record `unavailable`, never `passed`, for a tool or reviewer that could not run; a bug needs a regression
test; where to fetch more (`ddflow_brief`, `ddflow recall "<topic>"`, `ddflow_help`, `ddflow_search`). Enforcement does not
move into prompt text: it stays in hooks (pre-tool, pre-commit), the claim lease, gate refusals (exit 3) and `complete`;
the charter only says what those will refuse, so a model with a small window still behaves, and one that ignores it is
stopped by the tool.

## 5. Target and lean profile

Targets (first-turn input, ddflow plus Claude Code, model haiku measurements as the yardstick):

| profile | who | budget | composition |
|---|---|---|---|
| default worker | Claude Code subagent on this machine | < 32K (goal 15K to 25K) | restricted `tools:` (~10K measured), core or facade, charter, no connectors |
| lean | local or small-window model, 8K to 16K | ddflow share <= 3K | facade list 1K, charter 0.4K, SessionStart one line 0.1K, brief <= 1K on request; no connectors, no skills; the harness must supply its own short system prompt |

Budget as a knob: `[session].context_budget_tokens` (0 = off) selecting launch-brief size, charter or full instructions, and
tool profile; `ddflow doctor` prints `session start cost: N tokens (budget M)` counting ddflow's own part (instructions,
tools list for the chosen profile, SessionStart payload, launch brief); a ratchet test beside tests/test_mcp_tool_budget.py
fails when the number grows past the recorded budget.

## 6. Defects found

- doctor has no session start cost line, and the `tools_budget` option is dead (DX-dead-knob-tools-budget,
  DX-tools-list-budget-doctor-brief already filed).
- `ddflow claim` made a new `.ddflow/worktrees/` tree although the caller stood in a linked worktree
  (B-claim-points-to-ddflow-worktrees-not-session, already filed).
- Filed from this run: B-list-limit-silently-clamped (`list --limit 5000` clamps to 1000 and still says raise --limit; fix task fix-B-list-limit-silently-clamped), B-doctor-no-session-start-cost (fixed by LS-measure).
- Plan: phase P-lean-start, tasks LS-measure, LS-inherit-probe (wave 1); LS-charter, LS-agents, LS-worker-mcp-config, LS-launch-brief-budget (wave 2); LS-tier-hooks, LS-profile-select (wave 3, need RQM-inject, B-rc-context, MTD-profiles); LS-lean-profile (MTD-search-call), LS-e2e, LS-docs.
- Not defects, noted: `claude -p --bare` returned zero usage without an API key (not usable for a floor measurement);
  the interactive shell aliases `ls` to a missing `exa`, which prints noise on every command.
