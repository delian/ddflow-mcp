# Decision proposal: token-economy and compaction features on every supported agent

State: **PROPOSAL** (the operator decides; nothing is built before ATF-decide). Phase P-token-economy, task ATF-research, 2026-10-23.
Evidence and labels: [research/agent-token-features.md](../research/agent-token-features.md) (V verified, R reported, U unknown).
Operator request (verbatim, 2026-10-23): "can you make research for how to manage the same features on other coding agents, not only claude".
Related and not duplicated: D-TE-Q1..Q8 (token-spend.md), D-CC-* (context-compaction.md), TE-subagent-ttl, TE-context-caps, TE-spend-resume, CC-inventory, CC-advisor,
CC-resume-pack, P-agent-parity.descriptor-v2, .launcher, .meters, HX-facts-core, DX-descriptor-vocab-dsh.

## Finding in one paragraph

The Claude knobs of D-TE-Q1 and D-TE-Q2 have no equal elsewhere: a user-set cache TTL exists on Claude Code and (interactive only) Goose; a window or compaction
threshold can be set at launch on at least 11 of 17 agents (Claude, Codex, Gemini, Kimi, Qwen, Goose, Kilo, OpenCode, Cline, Grok, dsh) but by different means (env, `-c`, lane config file); compaction cannot be
triggered from outside except on Pi; a re-injection door after compaction exists on Claude, Codex, Qwen (and Goose per turn); limit signals are structured on Claude and Kimi only; resume is available nearly everywhere.
So the portable economy is: byte-stable prefix, short gaps, lane rotation from a resume pack, and per-agent launch settings where the descriptor has a verified setter.

## Proposals

| Id | Proposal | Task | State |
|---|---|---|---|
| D-ATF-1 | Per-agent token facts are **descriptor data** with the three-valued rule and HX-facts-core provenance: a new section `[tokens]` (cache, window, compact, output.brief, lane_dir), plus keys in the reserved `[headless]` (resume), `[meters]` (limit_signal, limit_exit_code, limit_match, reset_source, query), `[caps]` (tool_output, mcp_output, shell_output, max_completion) and `[spawn]` (subagent model/window). No Python per agent. `[tokens]` is registered in descriptor-v2 by amendment | ATF-descriptor-fields | PROPOSED |
| D-ATF-2 | A **setter** (`env`, `argv`, or a file in the lane's private config dir with a placeholder value) is how the launcher applies a knob at launch. The launcher applies only setters whose descriptor fact is verified or operator-overridden; otherwise it reports `unavailable` for that knob and starts the lane unchanged | ATF-launch-env | PROPOSED |
| D-ATF-3 | ddflow never edits the operator's user-level agent config to set a knob; it uses env, argv, an inline config (`KILO_CONFIG_CONTENT`, `-c`), or a file inside the lane's own directory (`CODEX_HOME`, `KIMI` `--config-file`, `GROK_HOME`, `QWEN_HOME`). User-level changes are proposals the operator applies | ATF-launch-env | PROPOSED |
| D-ATF-4 | Limit detection per agent from the descriptor (`hook`, `stream_event`, `exit_code`, `message`, `none`), feeding D-TE-Q8 (operator decided automatic resume). Reset time: taken from the agent's own message where it states one (Claude); else from a descriptor `query` command (`/usage` on Claude, Codex, Kimi; `kilo stats` is cost only); else a bounded probe ladder (resume attempts at 30 min, 1 h, 2 h, then hourly, each one a rejected request costing ~0 tokens when still limited), within `[spend].auto_resume` knobs (max resumes per day). `unavailable`, never `passed`, for a signal that was not recorded | ATF-limit-signals | PROPOSED |
| D-ATF-5 | Resume command per agent comes from `[headless].resume`; where headless resume is unverified (Kimi print + resume, Roo, Windsurf, Pi flags) the lane restarts fresh from the resume pack (CC-resume-pack) | ATF-resume | PROPOSED |
| D-ATF-6 | Cells that a launcher or advisor depends on are backed by a **recorded fixture** (stdin payload, stream line, exit code, config effect) per agent; docs-only cells carry `recorded: false` as in the hook fixtures; real-limit messages are recorded only from operator-supplied transcripts, never by exhausting a quota | ATF-fixtures | PROPOSED |
| D-ATF-7 | Scope statements: the 1-hour TTL decision (D-TE-Q1) and the `CLAUDE_CODE_AUTO_COMPACT_WINDOW` default (D-TE-Q2) are **Claude Code only**; for every other agent the TE/CC advisor uses the lowest-common-denominator policy of research section 6 until its descriptor has a verified setter | docs, ATF-docs | PROPOSED |
| D-ATF-8 | Closed IDE agents and agents with U cells (Roo, Windsurf, Antigravity, Cursor limits, Copilot cache) get no token features beyond the instruction file and the resume pack; they are listed in `ddflow harness show` under "not verified", not given invented values | ATF-docs | PROPOSED |

## Questions for the operator (task ATF-decide), each with a default

- **ATF-Q1** Register a `[tokens]` section in the descriptor (amending descriptor-v2) rather than spreading cache/window keys into `[caps]` and `[meters]`? Default: **yes, `[tokens]`; limits go to `[meters]`, caps to `[caps]`, resume to `[headless]`**.
- **ATF-Q2** May the launcher write a lane-private config file for knobs that are not env or argv (Kimi trigger ratio, Gemini and Qwen settings, Grok `config.toml`)? Default: **yes, inside the lane directory only, listed in the lane record, removed with the lane**.
- **ATF-Q3** Per-agent window defaults: reuse the TE-context-caps numbers (lane suggest 150 K, strong 250 K, rotate 350 K) as ratios of each agent's declared window, or set only the agent's own threshold (Qwen 0.85, Kimi 0.85, Goose 0.8, Gemini 0.5, Grok 85 %)? Default: **leave each agent's own threshold, apply ddflow's rotation advice on top; set a window/threshold at launch only for lanes where rotation is off (CC-Q6 default) and only to a value at or below the agent's default**.
- **ATF-Q4** Agents whose cache TTL cannot be set (all but Claude, Goose): add keep-alive pings (Aider has `--cache-keepalive-pings`)? Default: **no (CC-Q9); use short gaps and fresh lanes**; Aider's flag is allowed in its launch template when the operator opts in.
- **ATF-Q5** Research the U cells that need a live agent (limit messages and exit codes, hooks under print mode, Codex subagent isolation, Cursor binary name)? Default: **yes, only through `ATF-fixtures` with the operator's own installed agents and keys, never with credentials ddflow holds; each result lands as a fact with source and date**.
- **ATF-Q7** Agents that state no reset time (all but Claude): is the probe ladder (a resume attempt at 30 min, 1 h, 2 h, then hourly) acceptable under D-TE-Q8, or should ddflow wait for the operator on those agents? Default: **ladder, capped by `[spend].auto_resume` max resumes per day; a probe that is still limited must cost no model tokens (verified per agent in ATF-fixtures; if a probe is billed, that agent falls back to waiting for the operator)**.
- **ATF-Q6** Research Antigravity, Roo, Windsurf and the cloud agents further? Default: **not now**; add when the operator says one is in use.

## Alternatives rejected

- One generic `compact_window_env` string in the descriptor (the originating idea): rejected, because only 3 of 17 agents (Claude, Goose, Kimi) use env for it; the setter table (env, argv, file) covers the rest without per-agent code.
- A separate token-features file per agent outside `harnesses/*.toml`: rejected (a parallel structure; CC-inventory keeps only model-family facts outside).
- Setting Claude-style 1-hour TTL assumptions for all agents: rejected (research section 1, point 2).
- Probing limits by exhausting quotas: rejected (money and account risk).

## Would invalidate

A vendor adds env or flag control for a U cell (re-verify at P-agent-parity.drift); advice per agent ignored more than 80 % (as in CC-decide); a descriptor-v2 landing that already fixes a different shape for `[meters]` (then adapt the key names, not the placement); price ratios changing the break-even (dated data in CC-inventory).

## Answers

None yet.
