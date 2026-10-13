# Agent and bot support: tiers, budget, credentials, sandbox

Decision document for task `B-ac-decide`, filed by `P-decisions-docs.write`. It restates the decision records below; the records in the event log are the source of truth (`ddflow decision show <id>`).

## D-agent-compat: Agent and bot support: tiers, budget, credentials, sandbox

status **accepted** · decided by operator · decided by opus-lead

**Context.** R-agent-compat; phase P-agent-compat.

**Decision.** Operator 2026-10-09. TIERS as drafted: (1) live headless smoke: Claude Code, Codex CLI, Gemini CLI, Grok Build, Amp, Factory Droid, OpenHands, Copilot CLI, Kilo, opencode, Qwen Code; (2) fixtures only: Cursor IDE, Cline, Devin Desktop, Continue, Antigravity, Aider; (3) advisory cloud bots (sandbox repo): Copilot cloud agent, Claude Code Action, Gemini CLI Action, Codex cloud, Jules, Devin cloud, Cursor cloud; (4) event sources only: Dependabot, Renovate, CodeRabbit, Qodo; out of scope: Sweep, Grok chat/web/X. BUDGET: 3 USD spend cap per live run, majority of 3, weekly cadence plus before a release and on demand. CREDENTIALS: from the environment or the git-ignored local config only, never committed; ddflow may install a missing agent CLI into a scratch directory, with the operator's consent per agent; a missing key/CLI/rate limit is unavailable, never pass. SANDBOX REPO: none yet; tier-3 bot sandbox tests wait until the operator creates one.

**Alternatives rejected.** Fewer live agents first; 1 USD or 0.25 USD per run; never install; skip install.

Governs: `docs/ddflow/decisions/agent-compat.md`, `ddflow/harnesses/`, `ddflow/services/agent_smoke.py`, `scripts/ci/agent-smoke`
