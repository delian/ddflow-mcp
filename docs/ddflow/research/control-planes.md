# Control planes: the probed facts

Research for task `CP-verify-facts`, phase `P-control-planes`, decision `D-control-planes`. It replaces the guesses in research note `R-control-planes` with facts read or seen on 2026-11-05. The facts themselves live in one place, `ddflow/templates/control_plane_facts.toml`, each with status, source, tool version and date. `ddflow harness facts` renders them in the one facts table:

```sh
ddflow harness facts --agent paseo         # one plane or adapter
ddflow harness facts --unverified          # every question still open
```

## Method

Nothing was installed. Each project's source was downloaded at the commit pinned in `[sources]`, read as text and not executed. Package versions come from the same tree or the npm registry. Two tools were on this machine: `kilo` (7.8.18), which was run, and `claude`, which was not, because its ACP adapter is a separate package that is not installed.

A status says how far to trust a fact:

| Status | Meaning |
|---|---|
| `verified` | Seen running on this machine. The fact names its probe and version. |
| `documented` | Read in the project's docs or source at the pinned commit. Never run. |
| `unverified` | An open question. Its value is a lead and is never read as true. |
| `none` | Looked for and absent at that commit. |

Only two facts are `verified`, both for `acp-kilo`. Everything about Paseo, Harness Remote, AgentOS, Orca and Emdash is `documented` at best, so a kit that relies on one must still say so (`CP-kits`).

## The live probe

`probes/probe_acp_mcp_inject.py` sends `initialize` and `session/new` to an ACP agent, with a stub MCP server and a session directory that differs from the spawn directory. It sends no prompt, so no model is called, and it runs under a temporary `HOME`. It exits 0 only when the stub started in the session directory with the injected environment.

```sh
python3 -I probes/probe_acp_mcp_inject.py 'kilo acp'
```

Result on 2026-11-05: `started`, `env_passed`, `initialized`, `tools_listed` and `cwd_is_session` true, `cwd_is_spawn` false, exit 0. With `/bin/true` as the agent it exits 1, so a dead agent cannot pass. It proves the working directory of the MCP child only; whether the agent's own tools run there needs a model call and stays `unverified` (`acp-kilo.agent_tools_cwd`).

## What this changes

| Finding | Consequence |
|---|---|
| `monperrus/harness-remote`, cited by `R-control-planes`, is a fork last updated 2026-09-10. Upstream is `giuliastro/harness-remote`, version 3.8.0. | Kits target upstream. |
| Paseo reads no `.mcp.json` of its own. A Claude agent inherits one through the SDK's `project` setting source. | The Paseo kit injects `mcpServers` (SDK or `agent.open.before`) for any provider but Claude. |
| Paseo takes a pre-made worktree only as a plain directory: `workspaces.open(dir)` or `--isolation local`. `worktree.create.before` refuses an existing path. | `CP-adopt-worktree` opens the directory. Archive leaves it, because it has no ownership proof. |
| Paseo's `worktree.create.after` runs before workspace registration and carries path, branch and source checkout. | A plugin can claim a Paseo-made worktree in time (`claim --adopt`), for the plugin that `D-control-planes` puts last. |
| Harness Remote and Orca's native chat send `mcpServers: []`. | They get ddflow from the agent's own config, or from the ACP wrapper, which sits between them and can fill the list. |
| Harness Remote offers Basic Auth for the whole account. | Scoped tokens (`CP-scoped-tokens`) cannot pass through it. `--root` is not a sandbox. |
| Emdash adopts every worktree of a registered repository by itself, and `deleteWorktree` force-removes one with no dirty refusal. A ddflow worktree qualifies. | ddflow cannot stop this from its side, so the kit must warn the operator. See "Open for the operator". |
| Orca's delete removes the folder and branch, and no guard for external worktrees was found. | `unverified`: a kit must not promise that a shown worktree survives. |
| AgentOS removes only paths under `~/.agent-os/worktrees`. Its orchestrator and routed providers run with `--strict-mcp-config`. | A pre-made worktree is safe there, but `.mcp.json` does not reach those two kinds of session. |
| `codex-acp` skips an injected server whose name the Codex config already has. | `CP-acp-wrap` must not assume its `ddflow` server won; a user's own entry wins silently. |
| `opencode acp` refuses the whole request on one `sse` server and ignores `cwd` on load, resume and fork. | `CP-acp-wrap` injects stdio or http, and cannot move a resumed session to another worktree. |
| `zed-industries/codex-acp` is archived. `claude-code-acp` moved. | Use `@agentclientprotocol/codex-acp` and `@agentclientprotocol/claude-agent-acp`. |

## Not probed

These need a tool installed, which this task was told not to do. Each is an `unverified` row, so `ddflow harness facts --unverified` lists them with the rest:

- Paseo: `mcp_json_live`, `adopt_live`.
- Harness Remote: `live`.
- AgentOS: `live`.
- Orca: `delete_foreign`, `live`.
- Emdash: `live`.
- ACP: `live` on `acp-claude`, `acp-codex`, `acp-opencode`, `acp-gemini`. Once an adapter is on the machine, the probe above answers it.
- `acp-kilo.agent_tools_cwd`: needs a model call.

Nimbalyst and Zed are outside this task's title and were not probed.

## Recording a new result

Add the fact to `.ddflow/control_plane_facts.toml` in the project. It has the shipped file's shape and overrides one fact at a time. `verified` is refused without `probe` and `ver`, and a bad file is an error, never skipped. When the result is general, move it to the shipped file with the source pinned.

## Open for the operator

Emdash's delete conflicts with "ddflow never deletes a foreign worktree" only in spirit: the actor is Emdash, on the operator's click. `D-control-planes` does not say what the kit should do beyond documenting it. `CP-kits` needs that answer.
