# Herdr: the runtime coding agents run on, and what ddflow needs to integrate with it

Research for task `DX-herdr-research`, phase `P-herdr`. Operator 2026-10-24 (verbatim): "Can you make a research of this Herdr: the runtime coding agents run on https://share.google/3Bi883OV2cDJAQ6hz - herdr - and check if there are features ddflow needs to implement to be able to integrate with it? If you find gaps to what we have or plan on, add it before the restart". Proposal state: `docs/ddflow/decisions/herdr.md`. First note (facts, gaps, queue); a second pass may extend it.

## 1. What the link is

| Step | Result | Tag |
|---|---|---|
| `https://share.google/3Bi883OV2cDJAQ6hz` | HTTP 302 to `https://www.google.com/share.google?q=3Bi883OV2cDJAQ6hz`, then HTTP 200 landing on `https://herdr.dev/` (fetched with curl, retrieved 2026-10-24) | VERIFIED |
| `https://herdr.dev/` page title | "Herdr: the runtime coding agents run on" (matches the operator's phrase) | VERIFIED |

So the link is Herdr's own homepage, not a search result. Identification: Herdr, `github.com/herdrdev/herdr`, confidence HIGH. Other candidates considered: none with the same name and description ("herdr" web search returns only this project, its YC page and third-party reviews). Same-name unrelated projects were not seen.

## 2. Method and evidence tags

Read on 2026-10-24 as raw files, not through the summarising WebFetch: `herdr.dev` pages (curl; the docs pages are a client-rendered shell, so the docs source was read instead), the GitHub API (`api.github.com/repos/herdrdev/herdr`, releases), `README.md` and `docs/next/website/src/content/docs/*.mdx` at `HEAD` of `herdrdev/herdr` (raw.githubusercontent.com): concepts, agents, agent-automation, socket-api, session-state, persistence-remote, connecting-machines, integrations, cli-reference. Downloads were kept in a scratch directory and read as text; nothing was installed or executed. Tags: VERIFIED = primary page or docs source read (path in the table); REPORTED = a third party or marketing statement; UNKNOWN = not found, never guessed. Nothing here is VERIFIED by running Herdr; the probe task `HD-probe` does that.

## 3. What Herdr is

| Fact | Tag | Source |
|---|---|---|
| A terminal workspace manager and "runtime your coding agents live on": a background server owns real terminal panes (PTYs); clients (TUI) attach and detach; agents keep running | VERIFIED | README.md, docs/concepts |
| It does not wrap or replace agents; it owns their terminals and detects them from the foreground process, screen "detection manifests" and optional integrations | VERIFIED | README.md, docs/agents |
| Not a sandbox or container runtime: no VM or container isolation, no resource or spend limits found in the docs; `HERDR_AGENT=<agent>` tells it which manifest to use when a wrapper (VM, sandbox) hides the agent process | VERIFIED (absence in the docs read) | docs/agents "VMs and sandbox wrappers" |
| One Rust binary; macOS, Linux, Windows (Windows "beta" docs page); install by `curl -fsSL https://herdr.dev/install.sh \| sh`, brew, mise, or release binaries | VERIFIED | README.md |
| License Apache-2.0; repo created 2026-03-27; 45,607 stars; latest releases v0.9.4 (2026-10-13), v0.9.5 (2026-10-14), pushed 2026-10-23; version is 0.x so the API may change | VERIFIED | GitHub API |
| Vendor claims 44,627 stars, 1,644,092 installs, 1,789 community plugins, 22 agent CLIs; a "Y Combinator" blog post and pricing/marketplace pages exist (what is paid is UNKNOWN: not read) | REPORTED (homepage, search snippets) | herdr.dev |
| Third-party review: after a server or machine restart only the layout is restored, original processes do not survive | REPORTED, and VERIFIED by the vendor's own session-state doc | docs/session-state |

Maturity: fast-moving 0.x, large community, one maker team. Treat the API as unstable until `HD-probe` pins a version.

## 4. Agents, launch, configuration, lifecycle

| Topic | Fact | Tag |
|---|---|---|
| Agents recognised | claude, codex, copilot, cursor, opencode, pi, omp, droid, devin, kimi, kilo, hermes, qodercli, qwen, letta, mastracode, grok, antigravity-cli; state-only: amp, kiro, maki, gemini, cline; self-reporting: Command Code, Crush, Kiro, Muse Code, Prime Agent. Others run as plain terminals | VERIFIED docs/agents |
| Start | `herdr agent start <name> --kind <k> --pane <pane> -- <agent args>` needs an existing shell pane at its prompt; it never creates layout; returns after the agent is detected ready, or `agent_not_ready` if blocked. Agents started by hand are detected too. Names: `[a-z][a-z0-9_-]{0,31}`, unique among live agents | VERIFIED docs/agent-automation |
| Layout | `workspace create --cwd --label --env K=V`, `tab create`, `pane split --env K=V`; JSON on stdout (`.result.root_pane.pane_id`); ids like `w1:p2` are server-scoped and change on `pane move` (`previous_pane_id` returned) | VERIFIED |
| Env given to the process | caller `env` pairs plus `HERDR_SOCKET_PATH`, `HERDR_ENV=1`, `HERDR_WORKSPACE_ID`, `HERDR_TAB_ID`, `HERDR_PANE_ID`; session via `HERDR_SESSION=<name>` | VERIFIED docs/socket-api |
| Worktrees | `worktree.list/create/open/remove` (CLI `herdr worktree ...`) manage Git checkouts as workspaces; `--trust-repository` flag exists; provenance on workspace records | VERIFIED docs/socket-api, cli-reference |
| Steer | `agent prompt [--wait]` (refuses `agent_blocked` without sending), `agent send-keys`, `agent wait --until blocked\|idle`, `pane run`, `pane wait-output`; a timeout or `agent_prompt_stalled` does not prove nothing was sent | VERIFIED docs/agent-automation |
| Read | `agent read` / `pane read --source visible\|recent\|recent-unwrapped\|detection`, text or ANSI; full-screen agents render history in the alternate screen, so a full transcript is not always readable; the doc advises asking the agent to write a file | VERIFIED |
| States | `blocked` (needs input/approval), `working`, `done` (finished, unseen), `idle`, `unknown`; `idle` and `done` both mean ready for input; Antigravity and Grok count idle while background tasks run; blocked detection is strict and an unknown prompt may read as idle | VERIFIED docs/concepts, agents |
| Hooks | `herdr integration install <agent>` writes a hook script into the agent's config (Claude `~/.claude/hooks/herdr-agent-state.sh` and `settings.json` SessionStart; Codex `hooks.json`, etc.) that reports session identity (and for some agents state) to the socket; state for most agents still comes from screen detection | VERIFIED docs/integrations |
| MCP | no MCP server or MCP client mentioned in the docs read for agent control (the control surface is CLI and socket); agents bring their own MCP config. Whether Herdr injects or manages MCP config: UNKNOWN | UNKNOWN |
| Event stream | `events.subscribe` over the socket (newline-delimited JSON; Unix socket, named pipe on Windows): `pane.agent_status_changed` and lifecycle events; not durable, `events_lost` error closes a lagging subscription | VERIFIED docs/socket-api |
| Custom reporting | `pane.report_agent` (source, agent, state, message), `pane.report_agent_session`, `pane.report_metadata` (display tokens), so ddflow could report lane state into the sidebar | VERIFIED |
| Plugins | plugins run commands with `HERDR_PLUGIN_*` env, open panes/popups, subscribe to events; Herdr "does not review or sandbox plugin code" | VERIFIED docs/plugins, socket-api |

## 5. Persistence, remote, auth, secrets, limits

| Topic | Fact | Tag |
|---|---|---|
| Persistence | detach keeps processes; server restart restores layout and cwd only; agent conversation resumes only with a native session reference reported by an official integration or the agent's own resume command (`resume_agents_on_restore`, default on); `session.json`, 48 layout snapshots; pane screen history is off by default because it can hold secrets (`[experimental] pane_history`) | VERIFIED docs/session-state |
| Named sessions | separate server namespaces, sockets and state (`herdr session attach work`, `HERDR_SESSION`) | VERIFIED |
| Remote | `herdr --remote ssh://user@host` and saved SSH machines combine remote servers in one UI; auth is plain OpenSSH (keys, agent, `ForwardAgent` is the user's choice); ids and agent names are scoped per server, so CLI commands do not retarget when the UI machine changes | VERIFIED docs/persistence-remote, connecting-machines |
| Socket auth | the docs read describe the socket as local (Unix socket / named pipe); socket file permissions and any token: UNKNOWN. Treat any process that can reach the socket as having full control of every pane | UNKNOWN (permissions) |
| Secrets | none managed. Env is passed per pane/workspace; no vault, no redaction found; remote manifest updates are fetched from herdr.dev automatically (valid per-agent rule updates applied without restart; local override wins) | VERIFIED |
| Limits and spend | none found: no quotas, rate limits, token or cost accounting. Usage must come from the agents' own transcripts (ATF-*) | VERIFIED (absence) |
| Network | none managed; agents use their own endpoints. Telemetry by Herdr: UNKNOWN (not searched in source) | UNKNOWN |
| Install on this host | Linux x86_64 supported; needs no root (single binary); run from a scratch directory with `HERDR_SOCKET_PATH`/`HERDR_SESSION` set so it cannot touch a user's session. Not installed during this research | VERIFIED docs, not run |

## 6. What ddflow has and plans, per integration need

Legend: HAS (shipped, id), PLANS (queued, id), LACKS (new task in `P-herdr`).

### 6.1 ddflow drives Herdr (control plane / lane backend)

| Need | Status | Id |
|---|---|---|
| A Lane driver contract: LaunchSpec, observe, steer, cleanup, capability list | PLANS | DRV-lane-interface, CP-backend-iface |
| Launch only from operator templates; trusted bots may start; per-item scoped tokens | PLANS (decided) | D-control-planes, CP-scoped-tokens, D-agent-parity |
| A Herdr backend registered in that interface (agent start in a pane, subscribe, prompt, stop) | LACKS | DRV-herdr-lane |
| Foreign ids in the lane ledger (workspace/tab/pane/agent name, server-scoped, change on move) | PLANS generic, LACKS Herdr part | CP-foreign-ids, HD-foreign-ids |
| Herdr state vocabulary mapped to lane status; idle is not complete; stalled prompt is not unsent | LACKS | HD-status-map |
| Adopt Herdr-created worktrees, never delete, optionally create via `worktree.create` | PLANS generic (adopt, owner=external), LACKS Herdr glue | D-control-planes, HD-adopt-worktree |
| Know it runs inside a Herdr pane (`HERDR_ENV`, `HERDR_PANE_ID`) for identity, doctor, brief | LACKS | HD-env-detect |
| Blocked agent -> operator queue -> answer back via send-keys, never auto-answered | PLANS generic (remote approvals, operator digest), LACKS Herdr source | RA-request, P-operator-digest, HD-blocked-approvals |
| Coexist with `herdr integration install` hooks in the same agent settings files | LACKS (ddflow has per-harness hook installers) | HD-hooks-coexist; ddflow/harnesses/*.toml |
| Resume after a Herdr server restart (native session ref vs ddflow ledger) | PLANS generic (resume per agent) | ATF-resume, DX-fleet-registry; Herdr side in DRV-herdr-lane |
| Usage, spend, limit signals | HAS/PLANS per agent from transcripts; Herdr supplies none | ATF-usage-fields, ATF-limit-signals, TE-limit-aware-routing |
| Which family takes which item; conformance of a lane | PLANS | P-swarm, SW-conformance; Herdr run in HD-swarm-conformance |
| Remote saved SSH machines as untrusted lanes | PLANS | SW-remote-trust; HD-swarm-conformance |
| Threats: socket = full terminal control, remote manifest updates, pane history secrets, screen text injection | PLANS generic (CP-security) | CP-security, HD-security |
| Verify every REPORTED fact above by running a scratch Herdr | LACKS | HD-probe |

### 6.2 Herdr hosts ddflow

| Need | Status | Id |
|---|---|---|
| ddflow MCP server reachable from agents in panes (stdio per agent today; `ddflow serve`) and the lean tool profile in Herdr-started agents | HAS stdio MCP and lean profiles; serve PLANS | P-remote, LS-* lean profiles |
| Hooks in Herdr-managed agents (lease enforcement) | HAS per-harness hooks; coexistence LACKS | ddflow/harnesses, HD-hooks-coexist |
| Event log and git access from inside a pane | HAS (worktree per claim, repo files) | core |
| An operator queue view inside Herdr (plugin or pane) and `report-agent` of lane state into the sidebar | LACKS (optional) | HD-host-recipe, P-awareness, P-daemon |
| Devcontainer/VM wrapper with `HERDR_AGENT` | PLANS | P-devcontainer |

### 6.3 Data to exchange

| Data | Direction | Mechanism | Status |
|---|---|---|---|
| Lane status (working/blocked/done/idle) | Herdr -> ddflow | `events.subscribe`, `agent get` | LACKS (DRV-herdr-lane, HD-status-map) |
| Lane state shown in Herdr | ddflow -> Herdr | `pane.report_agent`, `report_metadata` tokens | LACKS, optional (HD-host-recipe) |
| Session id, resume | both | `pane.report_agent_session`, agent resume command | PLANS (ATF-resume), Herdr part in HD-foreign-ids |
| Transcript | agent -> ddflow | the agent's own transcript files; Herdr `agent read` is a screen snapshot, not a transcript | HAS per agent; Herdr read only for liveness |
| Usage | agent -> ddflow | transcripts (ATF-usage-fields); none from Herdr | PLANS |
| Approvals | blocked in Herdr -> operator -> answer | operator queue plus approval record, then `agent send-keys` | PLANS generic, LACKS Herdr leg (HD-blocked-approvals) |
| Operator queue | ddflow -> Herdr user | digest/inbox in P-awareness; optional pane | PLANS / optional |

## 7. Recommendations

1. Do not build a second control-plane path: Herdr is one more registry entry in `CP-backend-iface` (like Paseo, tmux), and ddflow stays the owner of leases, gates and events. Herdr's `idle` never completes an item.
2. Probe first (`HD-probe`), driver second: several facts here are docs-only (socket permissions, MCP, telemetry, API stability on 0.x).
3. Lowest-risk first deliverable: `HD-env-detect` + `HD-hooks-coexist` (users already run ddflow agents inside Herdr today); the lane driver follows `DRV-lane-interface`.
4. Treat Herdr input as a privileged channel: only the approval flow may send keys into a blocked agent.

## 8. What would invalidate this

A probe showing `agent start` needs a focused client or a TUI session; a socket with no permission model that cannot be locked down per session; API changes within 0.9.x that break `events.subscribe`; Herdr adding an MCP surface or a usage API (then the "no usage/MCP" rows change).

## 9. Sources (retrieved 2026-10-24)

`https://share.google/3Bi883OV2cDJAQ6hz`; `https://herdr.dev/`; `https://github.com/herdrdev/herdr` and `https://api.github.com/repos/herdrdev/herdr`; docs source under `https://raw.githubusercontent.com/herdrdev/herdr/HEAD/docs/next/website/src/content/docs/` (concepts, agents, agent-automation, socket-api, session-state, persistence-remote, connecting-machines, integrations, cli-reference); web search result snippets (REPORTED) from `dsebastien.net/herdr`, `ycombinator.com/companies/herdr`, `innfactory.ai/en/ai-harness/herdr`. Local: `docs/ddflow/decisions/control-planes.md`, `docs/ddflow/research/driver-architecture.md` (4.4 Lane), `docs/ddflow/research/swarm-orchestration.md` (7.3), `docs/ddflow/research/pi-dev.md` (format).
