# MCP client matrix: who honours a changed tool list, and what that means for soft upgrades

Task `SU-client-matrix` (phase `P-soft-upgrade`). Extends `R-soft-client-table`. Written 2026-10-21.
Operator question: "what about the other agents - kilo, opencode, grokbot, cursor, copilot,
antigravity, gemini, kimi, qwen, aider, etc?"

## Evidence tags

| Tag | Meaning |
|---|---|
| VERIFIED | a probe run on this machine against the installed client (`probe.py`, appendix A) |
| SOURCE | the client's open-source code was read (repo, path, commit); nothing was run |
| DOCS | official documentation or changelog |
| REPORTED | issue tracker or forum post, not reproduced |
| UNKNOWN | nothing found; never guessed |

SOURCE reads are of the default branch on 2026-10-21, and a shipped release can lag it. A
negative grep ("no handler") is evidence, not proof. Only Claude Code 2.1.296 and Kilo 7.8.12 are installed
here, so they are the only VERIFIED rows. Everything else needs the probe in appendix A run by someone who has the client.

## The matrix

Columns: **tools/list_changed** = re-issues `tools/list` on the notification and the model can use
the result; **cache** = how the tool list is held; **reconnect**; **transport**; **revision** =
MCP protocol revision offered in `initialize` (the SDK default when no override is in the code).

| Client | tools list_changed | cache / deferred | reconnect, slow initialize | transports | revision, `subscriptions/listen`, `ttlMs` |
|---|---|---|---|---|---|
| **Claude Code 2.1.296** | YES, additions and removals, also unsolicited while idle (VERIFIED, R-soft-claude-listchanged). A changed schema of an already-loaded tool is re-fetched but NOT adopted by the model (VERIFIED). prompts_changed: no re-fetch seen in `-p` (VERIFIED, maybe a `-p` artifact). Older versions ignored it: anthropics/claude-code#50515, #62058 (REPORTED) | per-server cache invalidated by the notification; deferred loading via ToolSearch, same result with it off (VERIFIED) | `/mcp` reconnect (DOCS, not re-fetched today); slow initialize not measured here | stdio, http, sse (DOCS, not re-fetched today) | probes `server/discover` (2026-07-28), falls back to `initialize` 2025-11-25 (VERIFIED); no `subscriptions/listen` seen |
| **Kilo 7.8.12 (CLI)** | YES: tools/list re-fetched within 30 ms of an immediate notification and also after an idle one 6 s after the call returned; the new tool, whose name was not disclosed, was found and called (VERIFIED, 3 runs). Source says no prompts/resources handler (SOURCE, Kilo-Org/kilocode 5cc65d1, `packages/opencode/src/mcp/index.ts:555-583`, handler registered only if the server advertises tools) | `s.defs` cache refreshed on notification. 7.8.12 exposes MCP tools to the model through an `execute` tool (`tools.lc.bump(...)`), a built-in facade (VERIFIED) | server marked failed on close; restart code `index.ts:666-752`; default timeout 30 s (SOURCE) | stdio, http, sse (SOURCE) | `initialize` 2025-11-25, `clientInfo` kilo/7.8.12, `capabilities.roots` only (VERIFIED); no 2026-07-28 in source |
| **opencode** | YES, 100 ms debounce, then `tools.reload()` so the model sees it; prompts re-listed; resources only publish an event (SOURCE, anomalyco/opencode 4d59657, `packages/core/src/mcp/index.ts:416-423`, `tool/mcp.ts:131-145`) | per-server cache refreshed on notification and after session-expiry reconnect (SOURCE) | startup 30 s, list 30 s; auto reconnect on HTTP session expiry; `/mcps` (SOURCE) | stdio, streamable http, no SSE (SOURCE) | SDK `@modelcontextprotocol/client` 2.0.0, config `protocol` legacy/auto/pinned; knows 2026-07-28 and `subscriptions/listen` (comment in `client.ts:155`); default revision UNKNOWN; `ttlMs` UNKNOWN |
| **Gemini CLI** | YES: handlers for tools, prompts and resources, `refreshTools()` removes and re-registers the server's tools, re-loops if another notification lands meanwhile; registered even if the server did not declare listChanged (SOURCE, google-gemini/gemini-cli c1740d2, `packages/core/src/tools/mcp-client.ts:~409-470,699-800`). Whether `setTools` reaches the model mid-turn not traced | registry rebuilt on notification (SOURCE) | `/mcp refresh` restarts all servers, per-server restart; timeout default 600 s, no separate startup timeout (SOURCE) | stdio, SSE, streamable http (DOCS, SOURCE) | SDK 1.23.0, no override; exact revision UNKNOWN (likely 2025-06-18 or 2025-11-25); listen/ttl UNKNOWN |
| **Codex CLI** | NO by default. The handler is behind feature `mcp_tool_list_changed`, stage UnderDevelopment, default off; otherwise log-only (SOURCE, openai/codex 4faf60f, `features/src/lib.rs:1598`, `rmcp-client/src/logging_client_handler.rs:82-92`). How a refreshed catalog reaches the model not traced | on-disk tool catalog cache used at startup (`codex-mcp/src/tool_catalog_cache.rs`) | `startup_timeout_sec` 30, `tool_timeout_sec` 300, background reconnect after failed startup (SOURCE) | stdio, streamable http (SOURCE) | legacy sends 2025-06-18; opt-in flag `mcp_2026_07_28` (default off) prefers 2026-07-28 with fallback; `subscriptions/listen` not found in non-test code (SOURCE) |
| **Qwen Code** | NO in the interactive client: no inbound `tools/list_changed` handler in core `mcp-client.ts` (negative grep, SOURCE, QwenLM/qwen-code a0e4149); the `qwen serve` runtime marks the catalog stale (`cli/src/serve/managed-mcp-runtime.ts:744-775`); design doc mentions list_changed but no code backs it | tools registered as each server finishes discovery (DOCS, SOURCE) | `qwen mcp reconnect`, auto reconnect with 5 s delay, timeout 600 s, `discoveryTimeoutMs` (SOURCE) | stdio, SSE, streamable http (SOURCE) | SDK ^1.30.0, no override; revision UNKNOWN |
| **Kimi CLI** | NO: `list_tools()` once at connect, no message handler (negative grep, SOURCE, MoonshotAI/kimi-cli 9ab1286, `src/kimi_cli/soul/toolset.py:784-812`) | baked in at connect; loading can be deferred to the background (`defer_mcp_tool_loading`) | `/mcp` shows status only; no startup timeout or reconnect command found; tool call timeout 60 s (SOURCE) | stdio, http (SOURCE) | fastmcp 3.2.4, no override; revision UNKNOWN |
| **Cursor IDE** | UNRELIABLE: forum 166216 (2026-08-25) staff call the cache intentional and not cleared on reconnect, `list_changed` a regression in 3.12.17 for non-stdio servers (stdio reportedly unaffected), "only a full restart is guaranteed"; prompts not honoured (forum 126689, 135534) (REPORTED) | snapshot cached for the session (REPORTED) | editing `mcp.json` sometimes reloads; restart guaranteed (REPORTED) | stdio, SSE, streamable http (DOCS) | UNKNOWN |
| **Cursor CLI (cursor-agent)** | NO: new tool hidden until the session restarts (forum 161459, 2026-05-24, no reply) (REPORTED) | session cache (REPORTED) | restart the session (REPORTED) | UNKNOWN | UNKNOWN |
| **Copilot, VS Code agent mode** | YES: tools and prompts re-listed; "Tool list changed, refreshing tools..."; the resources event is fired but its consumer was not traced (SOURCE, microsoft/vscode 3021013, `src/vs/workbench/contrib/mcp/common/mcpServerRequestHandler.ts:368-380`, `mcpServer.ts:~1210-1230`) | per-server persisted cache keyed by a nonce, then refreshed live; deferred loading UNKNOWN | start/restart commands; logs every 5 s while waiting for initialize, no timeout found (SOURCE) | stdio, streamable http, sse (SOURCE) | `initialize` with `LATEST_PROTOCOL_VERSION` 2025-11-25; no `server/discover`, listen or `ttlMs` (SOURCE, negative) |
| **Copilot CLI** (closed) | UNKNOWN. Changelog 1.0.101 mentions "cached MCP tools recheck their definition when a concurrent tool-list refresh changes the catalog" and servers "that advertise list-change capabilities but do not implement subscriptions now connect" (DOCS, github/copilot-cli changelog.md l.114, l.326) | cached tools, deferred MCP tools with tool search (DOCS) | config changes apply between turns without restart; reconnect after idle HTTP session expiry (DOCS) | stdio, http, legacy http+sse (DOCS) | UNKNOWN |
| **Antigravity** | UNKNOWN; a manual Refresh button exists; a reviewer says its MCP client does not fully support the spec (REPORTED) | UNKNOWN | Refresh button or restart (REPORTED) | stdio, streamable http via `serverUrl` (REPORTED) | UNKNOWN |
| **Grok Build (xAI)** | UNKNOWN; docs silent (docs.x.ai/build/features/mcp-servers) | UNKNOWN | refresh is manual: `/mcps` then `r`; `grok mcp doctor`; `startup_timeout_sec` 30, `tool_timeout_sec` 6000 (DOCS) | stdio, http (DOCS) | UNKNOWN |
| **superagent-ai/grok-cli** (the community "grok cli"; no separate "grokbot" project was found) | NO: no handler; tools built once at startup with `client.tools()` (negative grep, SOURCE, fb97af8, `src/mcp/runtime.ts`) | built once | no reconnect code (SOURCE) | stdio; remote passed to `@ai-sdk/mcp` (SOURCE) | `@modelcontextprotocol/sdk` ^1.27.1; revision UNKNOWN |
| **Aider** | n/a: no MCP client in the repo (`grep -rni mcp aider/` empty, SOURCE, Aider-AI/aider 5dc9490). Third-party bridges are the only route | n/a | n/a | none | n/a |
| **Cline** | YES, debounced 300 ms (max wait 2 s), and then it RESTARTS the active session with the same id so the model gets the tools; prompts and resources also re-listed (SOURCE, cline/cline e9dafbb, `apps/vscode/src/services/mcp/McpHub.ts:769-776`, `sdk-mcp-coordinator.ts:31-70`). A second stack under `sdk/` hardcodes protocol 2024-11-05 and has no handler; which one ships is UNKNOWN | cached per connection | `restartConnection`, file watcher, per-server timeout, 10 s connect default in `sdk/` (SOURCE) | stdio, SSE, streamable http (SOURCE) | SDK ^1.25.1 (resolved version UNKNOWN); no 2026-07-28 |
| **Roo Code** | NO: no handler (negative grep, SOURCE, RooCodeInc/Roo-Code b867ec9); tools fetched at connect, restart or manual refresh | fetched at connect | file watcher restarts the server on config change; `refreshAllConnections`; timeout 60 s (SOURCE) | stdio, SSE, streamable http (SOURCE) | SDK pinned 1.12.0, so at most 2025-03-26 |
| **Goose** | YES for tools: a version counter invalidates the cache, `public_tools` re-fetches; a change during a fetch prevents a stale write; no prompts/resources handler (SOURCE, block/goose 7143dd, `mcp_client.rs:358`, `extension_manager/mod.rs:271-296`) | cached, invalidated by the counter | default extension timeout 300 s; step-up auth reconnect; no general reconnect command read (SOURCE) | stdio, streamable http, no SSE (SOURCE) | `rmcp` 3.4.1, `Auto` mode prefers 2026-07-28 then 2025-11-25; listen/ttl in `rmcp` UNKNOWN |
| **Continue** | NO: handler is a commented-out TODO (SOURCE, continuedev/continue 5522c6f, `core/context/mcp/MCPConnection.ts:285-289`; CLI `extensions/cli/src/services/MCPService.ts` has none) | listed once at connect; `refreshConnection` re-runs it | timeout 20 s default | stdio, SSE, streamable http, WebSocket (SOURCE) | SDK 1.29.0 (core), 1.24.1 (CLI); no 2026-07-28 |
| **OpenHands** (software-agent-sdk) | YES for tools only: `_ToolListChangedHandler` re-lists, reconciles added/updated/removed and installs them into the running agent (SOURCE, OpenHands/software-agent-sdk 77c6ee5, `openhands-sdk/openhands/sdk/mcp/utils.py:254-345`); a notification before agent init is skipped | `client._tools` reconciled on notification | timeout 30 s; reconnect not traced | stdio, http, streamable-http, sse (SOURCE) | fastmcp 3.2.0 over `mcp` 1.28.1; no 2026-07-28 |
| **Zed** | YES for tools, gated on the Tools capability: subscribes to `notifications/tools/list_changed` and reloads the registry; prompts/resources types exist but nothing subscribes (SOURCE, zed-industries/zed 3762891, `crates/agent/src/tools/context_server_registry.rs:132-160`) | registry cache | request timeout 60 s default, per-server timeout, settings change restarts affected servers (SOURCE) | stdio, http (SOURCE) | own client; offers 2025-11-25 first; no 2026-07-28 |
| **Windsurf / Cascade** (docs.devin.ai/desktop) | UNKNOWN; only manual refresh documented (REPORTED) | 100-tool cap, `disabledTools` (DOCS) | UNKNOWN | stdio, streamable http, SSE (DOCS) | UNKNOWN |
| **Amp** | UNKNOWN; "running threads refresh remote tool lists in the background" (DOCS) | `tool_search` for remote tools (DOCS) | `amp mcp remote tools <name> --refresh` (DOCS) | stdio; remote transport unnamed (DOCS) | UNKNOWN |
| **Factory Droid** | UNKNOWN | UNKNOWN | auto reload on `mcp.json` change; `connectTimeout` 10 s http/sse, 30 s stdio (DOCS) | stdio, http, sse (DOCS) | UNKNOWN |
| **Devin, Qodo, Tabnine, Replit** | UNKNOWN | UNKNOWN | UNKNOWN | Qodo: SSE by URL; Tabnine: `.tabnine/mcp_servers.json` (DOCS) | UNKNOWN |

Which ddflow harnesses these are: `ddflow/harnesses/` has claude, codex, gemini, qwen, kimi, opencode,
kilo, cursor, copilot, vscode, antigravity, aider, cline, goose, openhands, windsurf, devin, glm, qodo,
tabnine, replit, cody. Amp, Droid and Grok Build are in the agent-compat tiers
(`docs/ddflow/decisions/agent-compat.md`) but have no harness descriptor; Roo, Continue and Zed are in neither.

### What the matrix says

1. Honour tools/list_changed today (tools only): Claude Code 2.1.296 and Kilo 7.8.12 (VERIFIED);
   opencode, Gemini CLI, Copilot in VS Code, Cline, Goose, OpenHands, Zed (SOURCE).
2. Ignore it: Qwen Code, Kimi CLI, Roo, Continue, superagent grok-cli (SOURCE, negative), Cursor CLI and
   probably Cursor IDE (REPORTED), Codex (flag off by default, SOURCE). No MCP at all: Aider.
3. Nobody honours prompts/list_changed reliably: only opencode, Gemini CLI, Cline, VS Code are read to
   re-list prompts. A soft upgrade must never depend on a prompt change reaching the client.
4. Honouring is not the same as adopting. Claude Code re-fetched but kept the old argument name of a
   changed schema (VERIFIED, `ENABLE_TOOL_SEARCH` on and off). Cline restarts the whole session to apply it.
   Only additions and removals are safe to announce; an in-place schema change is not.
5. No client implements `subscriptions/listen` that we could find except through an SDK (opencode comment
   at `client.ts:155`, Goose and Codex 2026-07-28 modes). Codex and Goose can already negotiate 2026-07-28, so
   ddflow's fixed 1 h `ttlMs` on `tools/list` becomes a real problem the day their defaults flip.
6. Spread of revisions: 2025-11-25 (Kilo VERIFIED, Claude Code VERIFIED, VS Code and Zed SOURCE), 2025-06-18
   (Codex legacy), 2025-03-26 or older (Roo, Cline `sdk/`).
7. Kilo 7.8.12 and Claude Code both put a facade between the model and the MCP tool list (`execute` /
   ToolSearch). A stable facade is already how some clients treat MCP tools.

## Design consequence for P-soft-upgrade

### Degraded modes for a client that ignores or caches

| Mode | What it does | Works for a non-honouring client? | Cost |
|---|---|---|---|
| A. Facade (`ddflow_search` + `ddflow_call`, `MTD-search-call`) | the client sees 2 to 3 tools whose names, descriptions and schemas never change; the worker's operation set changes behind them; `ddflow_search` always reads the live set | Yes, any client including Cursor, Qwen, Kimi, Codex, Roo, Continue; no notification needed, and nothing is cached that can go stale | model accuracy (MTD-accuracy-eval measures flat 123 vs families vs dispatcher), one more round trip, loses the client's own per-tool permissions and typed schemas |
| B. Digest on every result | each tool result carries `interface: <digest>`; the shim remembers the digest it served in the last `tools/list`; when the worker's differs and the client is not known to honour, the footer says "ddflow was upgraded: N tools changed, restart your agent to see them" | Yes as a notice; it fixes nothing but makes the hard upgrade visible instead of a silent mismatch | one line per call; must be rate limited |
| C. Description versioning | put the digest in tool descriptions | No: a client that caches does not re-read descriptions, and one that honours does not need it. Reject | |
| D. Observed acknowledgement | after a swap with a changed digest the shim sends `list_changed` and waits a few seconds for a `tools/list` from that client; a re-fetch proves the client honours it and is cached per `clientInfo` name and version; silence classifies the client as ignoring and falls back to B | Detects, does not cure. It turns the SOURCE-level cells into VERIFIED at runtime | needs the shim to see client requests, which it does |
| E. Per-client capability table | `clients.toml` keyed by `clientInfo.name` + version range with `list_changed = honours | ignores | unknown`, evidence tag and date | Static input to the classifier | stale entries; Cursor's behaviour changed between builds, so entries carry a version range |
| F. Old-schema grace | worker accepts the previous schema's arguments for one version | needed whenever a schema changes, for honouring clients too (Claude Code keeps the old arguments) | worker work |

### Recommended rule

An upgrade is **soft** when its class (SU-digest) is one of:

1. the interface digest is unchanged; or
2. the change is additive or a removal (no changed required arguments of an existing tool) AND the
   connected client is known to honour `tools/list_changed`: its (name, version) is in the table as
   honours (VERIFIED or SOURCE), or the shim has observed a re-fetch from it (mode D); or
3. facade mode is on and only the facade's own digest counts (the digest covers `ddflow_search`,
   `ddflow_call` and the instructions, not the backend operations).

Everything else is **hard**: tell the user to restart the agent and say why (client `X` does not refresh tools /
a schema changed / the shim protocol changed). A schema change to an existing tool is soft only with
mode F (grace) AND a client that honours; with an unknown client it stays hard. Order of preference for a
client that does not honour: facade first (makes it soft), notice B second (makes the hard upgrade explicit).

An unknown client is hard by default, but mode D runs anyway: the first upgrade teaches ddflow, and
the table entry for that (name, version) is written as `observed`.

### Consequences for existing tasks

- `SU-client-table`: seed rows from this document (not only claude and cursor), carry an evidence tag
  and version range, and add the observed-acknowledgement cache.
- `SU-announce`: `listChanged` is advertised and notified for tools only; never rely on prompts/resources;
  the shim waits for the acknowledging `tools/list` (mode D); 2026-07-28 clients get a short `ttlMs`
  (seconds, not 1 h) keyed to the digest.
- `SU-upgrade-integration`: classification uses the rule above; the report names the mode used.
- New: `SU-facade-mode` (depends on `MTD-search-call`), `SU-restart-notice` (mode B),
  `SU-client-probe-ci` (the probe in appendix A as `ddflow mcp probe-client` plus a CI matrix).

## UNKNOWN and how to settle it

| Cell | Needs |
|---|---|
| Codex, Gemini, Qwen, Kimi, opencode, Cursor, Copilot CLI, Antigravity, Grok Build, Windsurf, Amp, Droid | the CLI installed and one appendix-A run each; no key needed for `initialize` / `tools/list`, a model for the full loop |
| Whether Gemini's `setTools` and Codex's refresh reach the model mid-turn | probe |
| Default revision of opencode 2.0.0, Gemini, Qwen, Kimi | one `initialize` log line each |
| `prompts/list_changed` in Claude Code outside `-p` | interactive run |
| Antigravity and Cursor IDE exact behaviour | GUI client; fixtures only (agent-compat tier 2) |
| "grokbot" | no project with that name was found; ask the operator which one is meant |

## Side effects seen while probing

`kilo run` printed an `app.kilo.ai/s/...` session-share URL: it appears to upload the session by default.
Probes used only dummy prompts; keep it that way. Anthropic API credit on this machine was exhausted,
so Kilo was probed with `google/gemini-2.5-flash` (negligible cost).

## Appendix A: the probe server

Stdlib stdio server that logs every inbound and outbound message with a timestamp. `bump` adds a
tool and sends `notifications/tools/list_changed` at once; `delaybump` does it 6 s later while the
client is idle. The new tool's name is not revealed in the tool result so the model has to find it
through its tool list. A client re-fetches if the log shows `tools/list` after the `OUT ...list_changed` line.

```python
#!/usr/bin/env python3
"""Client-matrix probe: stdio MCP server that logs every request/notification and
changes its tool list on demand. argv[1]=logfile. Tools: bump (add 'secret_oracle' and send
list_changed NOW), delaybump (add it, notify after 6 s while client is idle), get_color."""

import sys, json, threading, time

LOG = open(sys.argv[1], "a")
LK = threading.Lock()


def log(s):
    with LK:
        LOG.write("%.2f %s\n" % (time.time() % 1000, s))
        LOG.flush()


base = [
    {
        "name": "bump",
        "description": "Call this: the server adds one new tool and sends notifications/tools/list_changed immediately.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "delaybump",
        "description": "Call this: the server adds one new tool and sends list_changed 6 seconds LATER (unsolicited).",
        "inputSchema": {"type": "object", "properties": {}},
    },
]
added = {
    "name": "secret_oracle",
    "description": "Returns the word MAGENTA. Only exists after bump or delaybump.",
    "inputSchema": {"type": "object", "properties": {}},
}
tools = list(base)


def send(m):
    with LK:
        sys.stdout.write(json.dumps(m) + "\n")
        sys.stdout.flush()
    log("OUT " + json.dumps(m)[:300])


def later():
    time.sleep(6)
    send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})


for line in sys.stdin:
    m = json.loads(line)
    log("IN  " + line.strip()[:400])
    meth = m.get("method")
    i = m.get("id")
    if meth == "initialize":
        send(
            {
                "jsonrpc": "2.0",
                "id": i,
                "result": {
                    "protocolVersion": m["params"]["protocolVersion"],
                    "capabilities": {
                        "tools": {"listChanged": True},
                        "prompts": {"listChanged": True},
                    },
                    "serverInfo": {"name": "lc", "version": "1"},
                },
            }
        )
    elif meth == "tools/list":
        send({"jsonrpc": "2.0", "id": i, "result": {"tools": tools}})
    elif meth == "prompts/list":
        send({"jsonrpc": "2.0", "id": i, "result": {"prompts": []}})
    elif meth == "resources/list":
        send({"jsonrpc": "2.0", "id": i, "result": {"resources": []}})
    elif meth == "tools/call":
        n = m["params"]["name"]
        if n in ("bump", "delaybump"):
            if added not in tools:
                tools.append(added)
            send(
                {
                    "jsonrpc": "2.0",
                    "id": i,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": "server tool list changed (new tool name not disclosed; list your tools to find it)",
                            }
                        ]
                    },
                }
            )
            if n == "bump":
                send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
            else:
                threading.Thread(target=later, daemon=True).start()
        else:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": i,
                    "result": {"content": [{"type": "text", "text": "MAGENTA"}]},
                }
            )
    elif i is not None:
        send({"jsonrpc": "2.0", "id": i, "result": {}})
```

Kilo recipe: `kilo.json` with `{"mcp":{"lc":{"type":"local","command":["python3","probe.py","kilo.log"]}}}`,
then `kilo run -m google/gemini-2.5-flash "call lc bump, then call the new tool and report its output"`.
Claude Code: `claude -p --strict-mcp-config --mcp-config mcp.json`.

# Part 2 (2026-10-21, task `SU-reconnect-research`): asking the client, and soft upgrade by reconnect

Operator questions, verbatim:

1. "Is it possible for the MCP server to ask the agent if it supports list_changed and, if so, enable
   soft upgrade for those agents for more features, leaving hard upgrade only for agents that don't
   support list_changed AND where the facade of pre-existing commands/prompts changed?"
2. "Separately, is there support in the agents to tell the MCP client to disconnect, reload the MCP
   server and re-connect? If that is supported, we could do soft upgrade using this capability."

Tags are as above. "installed" clients are Claude Code 2.1.296 and Kilo 7.8.12 (VERIFIED = run here
with the probes in appendices B and C). Source reads of the other clients were made on 2026-10-21 at
the commits named in each cell; negative greps are evidence, not proof.

## Q1. Can the server learn that the client handles list_changed?

### What the protocol gives the server

| Revision | What the server sees | Signal for "this client re-fetches on list_changed" |
|---|---|---|
| 2025-11-25 and earlier (`initialize`) | `protocolVersion`, `clientInfo` {name, title, version}, `capabilities` (roots, sampling, elicitation, tasks, extensions, experimental) | NONE. No client capability means "I handle `notifications/tools/list_changed`". `roots.listChanged` is about the client's own roots list and is not a proxy. The server's `tools.listChanged` is the server's promise, not the client's (DOCS, spec 2025-11-25 `server/tools`: the server "SHOULD" send; no client duty is stated) |
| 2026-07-28 (no handshake; `_meta` on every request) | per-request `io.modelcontextprotocol/protocolVersion`, `clientInfo`, `clientCapabilities`; a client that wants notifications must open `subscriptions/listen` with `notifications.toolsListChanged: true`, and the server "MUST NOT send notification types the client has not explicitly requested" and acknowledges the subset it honours (DOCS, spec 2026-07-28 `basic/patterns/subscriptions`) | YES, as an explicit opt-in: a `subscriptions/listen` carrying `toolsListChanged: true` is a negotiated declaration. It says the client will receive the notification; that it then re-fetches `tools/list` is the normal reading but is not stated by the spec |

So: **no reliable negotiated signal exists on the revision every installed client speaks today**
(Claude Code probes `server/discover` for 2026-07-28 and falls back to `initialize` 2025-11-25; Kilo
sends 2025-11-25; VERIFIED), and one exists only on 2026-07-28. Claude Code's docs say a "v2 runtime"
holds a notification stream open on the newer revision and reopens it with limits (DOCS, code.claude.com
mcp, "list_changed and dynamic tool updates"). I did not exercise that runtime against a modern server.

### What each client sends at `initialize` (identification inputs)

| Client | clientInfo name / version | capabilities | protocolVersion | Evidence |
|---|---|---|---|---|
| Claude Code 2.1.296 | `claude-code` (title `Claude Code`) / `2.1.296` | `roots.listChanged`, `elicitation` form+url | 2025-11-25 after a `server/discover` probe (2026-07-28 `_meta`) that a legacy server may answer with anything | VERIFIED |
| Kilo 7.8.12 | `kilo` / `7.8.12` | `roots: {}` | 2025-11-25 | VERIFIED |
| opencode | `opencode` / `"unknown"` unless overridden | `roots`, `elicitation` if a handler exists | legacy or 2026-07-28 by config `protocol` | SOURCE, anomalyco/opencode 3a1f9a9 `core/src/mcp/client.ts:152,187-192` |
| Gemini CLI | `gemini-cli-mcp-client` / CLI version or `unknown` | `roots.listChanged` | SDK 1.23.0 default | SOURCE, c1740d2 `mcp-client.ts:1869-1913` |
| Codex CLI | `codex-mcp-client` (title `Codex`) / crate version | `elicitation`, `extensions` (OpenAI ids, MCP-app UI); no roots | 2025-06-18; 2026-07-28 behind `mcp_2026_07_28` | SOURCE, c419fd0 `codex-mcp/src/rmcp_client.rs:1204-1222`, `rmcp-client/src/protocol_mode.rs:20-46` |
| Qwen Code | `qwen-code-mcp-client` / constant `0.0.1` | `extensions` (MCP apps), `roots.listChanged` | SDK 1.30.0 default | SOURCE, a0e4149 `mcp-client.ts:448-461,2113` |
| Kimi CLI | fastmcp default, UNKNOWN | none passed | UNKNOWN | SOURCE, 9ab1286 (negative) |
| Cline | `Cline` / extension version | `{}` | SDK default | SOURCE, e9dafbb `McpHub.ts:466-473` |
| Roo Code | `Roo Code` / extension version | `{}` | 2025-03-26 (SDK 1.12.0) | SOURCE, b867ec9 `McpHub.ts:688-695` |
| Continue | `continue-client` or `continue-cli-client` / constant `1.0.0` | `{}` | SDK default | SOURCE, 5522c6f `MCPConnection.ts:88-96` |
| Zed | `Zed` / crate version | all `None` | 2025-11-25 | SOURCE, 70277c0 `context_server.rs:155-160`, `protocol.rs:42-47` |
| VS Code Copilot | product name / product version | `roots.listChanged`, `sampling`, `elicitation`, `tasks`, `extensions` (MCP apps UI) | `LATEST_PROTOCOL_VERSION` | SOURCE, 4f8853f `mcpServerRequestHandler.ts:113-140` |
| Goose | host-supplied name / `GOOSE_MCP_CLIENT_VERSION` or crate version | roots, extensions, elicitation | Auto: prefers 2026-07-28 | SOURCE, 7143ddd `mcp_client.rs:274-304,534-557` |
| OpenHands SDK | fastmcp default, UNKNOWN | none set | UNKNOWN | SOURCE, 77c6ee5 (negative) |

Consequences for keying a table:

- `clientInfo.name` is usable for the clients whose name is distinctive (`claude-code`, `kilo`,
  `gemini-cli-mcp-client`, `codex-mcp-client`, `qwen-code-mcp-client`, `Cline`, `Roo Code`, `Zed`).
- The **version is not reliable**: opencode reports `unknown`, Qwen a constant `0.0.1`, Continue a constant
  `1.0.0`. A version range in the table is meaningful only for Claude Code, Kilo, Gemini, Codex, Zed, Goose.
  For the others the entry must be keyed by name alone and treated as expiring (re-observe).
- Capabilities do not separate honouring from ignoring clients: Qwen (ignores) and Gemini (honours) both
  send `roots.listChanged`; Cline (honours) and Roo (ignores) both send `{}`.
- VS Code, Cursor and others that host several agents share one `clientInfo`; the table row is per
  application, not per model.

### The practical alternatives, with what they can and cannot prove

| Method | Works on | Cost / failure mode | Verdict |
|---|---|---|---|
| T. Identify by `clientInfo` against the verified table (SU-client-table) | any client with a distinctive name | stale rows; version constants; an unknown client yields no answer | the baseline; cheap; keep |
| L. 2026-07-28 `subscriptions/listen` with `toolsListChanged` | modern clients (Codex and Goose can already negotiate it, opencode config-driven, Claude Code v2 runtime) | none beyond serving listen | treat as a positive signal "honours" when present; absence on a modern connection means no notification may be sent at all |
| O. Observe: send `list_changed` after a real change and watch for `tools/list` from the same connection within a timeout; record per client name (+version when meaningful) | any client | silence is ambiguous: lazy clients, debounce (Cline 300 ms to 2 s, opencode 100 ms), a client that re-fetches only at the next turn, or a client that ignores. A re-fetch is conclusive for "fetches", never for "adopts a changed schema" | adopt, with a conservative state machine (below) |
| P. Handshake probe: right after `notifications/initialized`, send `list_changed` with an unchanged list and watch for a re-fetch | any client | **false negative measured here**: on the second connection of a Claude Code session (after the server was killed and respawned), a `tools/list_changed` sent 0.3 s after `initialized` produced no `tools/list` (one run, cause UNKNOWN; the first connection of the same client is known to honour it). Also a wasted `tools/list` for every honouring client at every connect | reject as a gate. It may be used only as a hint recorded as `probe-silent`, never as `ignores` |
| A. Ask the model/agent in text | no | the model is not the MCP client; nothing reliable | reject |

Recommended state machine for observation (per client key): `unknown -> honours(observed)` after
one re-fetch within 10 s of a notification; `unknown -> ignores(observed)` only after TWO silent
notifications in different sessions; any later re-fetch overrides silence; observed entries carry the date
and client version and expire after N upgrades or on a version change; the table's VERIFIED/SOURCE rows are
never overwritten by an observation, a contradiction is logged as a defect against the table.

### Answer to the operator's rule

Yes in effect, but not by asking: identify (T), use the 2026-07-28 opt-in where present (L), and learn the rest
by observing (O). And the proposed split is right, with one refinement the evidence supports: the cost of a
non-honouring client is not "no new features until restart" for every change. See "Refined soft/hard rule" below:
an additive change is safe for a client that never re-fetches, because everything it already knows still
works; only the new tools stay invisible until it restarts. Hard is needed when what the client already holds
is invalidated.

## Q2. Do the clients support disconnect, reload, reconnect, and can a server ask for it?

### Server-initiated reconnect request: none exists

Across every client read (Claude Code docs and probes, Kilo, opencode, Gemini, Codex, Qwen, Kimi, Cline, Goose,
Zed, Roo, VS Code, Continue, OpenHands) I found **no** client that reacts to a server-sent notification, a
special exit code, SIGHUP or a socket by reconnecting. SIGHUP appears only as a shutdown handler of the client
itself (Gemini `cleanup.ts:146`, Kilo `serve.ts:58`). The spec has none either; in 2026-07-28 a graceful end of a
`subscriptions/listen` response lets the client "MAY treat" an abrupt close as a trigger to re-listen, which
re-establishes the subscription and does not re-initialize or re-list (DOCS, subscriptions page, "Graceful
Closure"). The only levers are indirect:

| Lever | Clients | Why it does not give soft upgrade |
|---|---|---|
| Process exit (stdio) | Claude Code `-p` respawns lazily; VS Code starts on the next call; OpenHands reconnects on the next call; Qwen health monitor | the connection is dropped, the in-flight call fails, and most of them keep the old tool list (table below) |
| HTTP 404 on a session id | Claude Code (VERIFIED), opencode, Codex, VS Code, Kilo (patched SDK), Qwen (SOURCE) | re-initializes and retries once; tool list usually not re-fetched |
| Touch the client's config so its watcher restarts the server | opencode, Qwen, Cline, Roo, Zed, VS Code, Droid (docs), Continue only if the entry changed (SOURCE) | all of them compare the server entry and restart only if it differs (opencode deep-equality, Cline connection fingerprint, Continue command/args/env/url, Roo changed config). It means rewriting a file the user owns, per client, with different paths. Cline and Roo also watch the server's own `build/index.js` (`watchPaths`), which a Python server does not have. THEORETICAL, invasive, not recommended |
| `/mcp reconnect`, `/mcp reload`, `qwen mcp reconnect`, `/mcps`, Kilo `POST /mcp/:name/connect`, Codex `config/mcpServer/reload` | all manual or app-server | a person or host must issue it |

### What happens when the server exits or the connection drops

| Client | stdio server exits mid-session | HTTP/SSE drop or 404 | Re-initialize and re-list after it | In-flight call | Evidence |
|---|---|---|---|---|---|
| **Claude Code 2.1.296** | `-p`: next tool call respawns the process 1.2 to 1.5 s after the exit, 10 ms before the pending call is sent, with `server/discover` + `initialize` again; the model got `Connection closed` for the call that was in flight and succeeded on the next. Docs say stdio servers are "not reconnected automatically"; that is true of the interactive panel at best, it is false for `-p` here | docs: remote servers reconnect 5 times, 1 s doubling; failed after that, `/mcp reconnect all`; `-p` keeps retrying about 30 min. HTTP 404 on the session id: transparent re-initialize and the SAME call is retried, no error to the model, 40 ms (VERIFIED) | `initialize` yes; **`tools/list` NOT re-fetched**: the model still saw generation 1's tool and not generation 2's marker tool (VERIFIED, stdio) | `Connection closed` error to the model, no retry (VERIFIED) | VERIFIED (appendices B, C); DOCS code.claude.com/docs/en/mcp "Automatic reconnection" |
| **Kilo 7.8.12** | server marked failed on close, its tools DROPPED (the `execute` tool vanished within the same run), no respawn within the run; the model got "unavailable tool" (VERIFIED, 1 run). Network-restore triggers `mcp.connect` for failed servers (SOURCE, kilocode 5cc65d1 `session/network.ts:384-427`) | patched SDK 1.31.0 re-initializes on a session 404 and retries once (SOURCE patch lines 94-100, 266-284); no tool re-fetch on that path | a manual `POST /mcp/:name/connect` runs a full initialize and lists tools; resolution is per step so the same conversation sees them (SOURCE `session/tools.ts:473`, `prompt.ts:1766`) | `McpError ConnectionClosed` (SOURCE `protocol.ts:644-668`) | VERIFIED + SOURCE `mcp/index.ts:555-568,741-765` |
| **opencode** | failed, tools cleared, `ToolsChanged` published, "Reconnect it from /mcps"; no respawn (SOURCE 3a1f9a9 `mcp/client.ts:381`, `index.ts:402-410,481-493`) | session-expiry (404 or 400 "not initialized") recovers: stop, start (initialize + `tools/list` + prompts) and the foreground call is retried once (SOURCE `client.ts:224-244`, `index.ts:340-361`); 2 connect retries 250 ms and 1 s | yes on manual connect and on recovery; reaching the model on the next request UNKNOWN | `ToolCallError`; no retry | SOURCE |
| **Gemini CLI** | `onerror` only marks DISCONNECTED; tools probably stay registered; no respawn; `/mcp reload` restarts all (SOURCE c1740d2 `mcp-client.ts:211-224,1238-1246`, `mcpCommand.ts:139-146,381`) | Streamable HTTP then SSE on 404 at first connect; no session-expiry handling (negative grep `sessionId`) | restart re-runs discovery and `setTools`; mid-conversation effect probable, not traced | error returned to the model, no retry | SOURCE |
| **Codex CLI** | "MCP server is not connected" error; no stdio retry; refresh builds a new connection if `is_closed` and is triggered by the app-server request `config/mcpServer/reload` (SOURCE c419fd0 `connection_manager.rs:112-114,1189-1197`, `app-server-protocol/common.rs:1265`) | 404 on a session: new transport, full initialize, operation retried once; initialize and `tools/list` retried 250 ms, 1 s (SOURCE `rmcp_client.rs:1440-1451,1554-1640`, `streamable_http_retry.rs:20-100`) | initialize yes; `tools/list` after session recovery UNKNOWN | `tool call failed ... transport_closed` | SOURCE |
| **Qwen Code** | health monitor: check every 30 s, 3 failures, 5 s delay, `autoReconnect` true, no cap; but the status flips only via `onerror`, whether a stdio exit reaches it is UNKNOWN (SOURCE a0e4149 `mcp-client-manager.ts:65-70,2080-2146`, `mcp-client.ts:575-598`) | 404 treated as a dead session: the next call triggers full rediscovery (SOURCE `mcp-tool.ts:114-120,455-484`) | yes: `discoverMcpToolsForServer` clear-then-register, instructions re-read | replayed up to 3 times only if the tool is read-only/idempotent, else error to the model (SOURCE `mcp-tool.ts:359,486-557`) | SOURCE |
| **Kimi CLI** | no supervisor; `failed` is terminal; tools stay; each call re-enters the fastmcp client, which may start a fresh session (partly UNKNOWN) | UNKNOWN | **tools not re-listed**; instructions not read | exception, 60 s timeout | SOURCE 9ab1286 `toolset.py:784-812,860,901-904` |
| **Cline** | transport close sets `disconnected`; no respawn; restart by UI, settings-file change or watched script (SOURCE e9dafbb `McpHub.ts:497-516,1255-1264,1469-1485`) | SSE `ReconnectingEventSource` 5 s; streamable HTTP 6 attempts, 2 s doubling, new client each time (SOURCE `StreamableHttpReconnectHandler.ts:43-47`) | new `connectToServer` re-initializes and re-lists; list_changed restarts the SDK session with the same id so the model sees it (SOURCE `sdk-mcp-coordinator.ts:31-80`) | error with timeout text, no retry | SOURCE |
| **Goose** | `Failed` cached permanently per slot; a running extension whose child dies is UNKNOWN; recovery is remove and add | only the 401/403 step-up reconnect | list_changed bumps a version and re-lists; instructions captured at initialize | `TransportClosed`, no retry | SOURCE 7143ddd `extension_manager/mod.rs:173-233`, `mcp_client.rs:620-640` |
| **Zed** | nothing happens: store still says Running with a dead client; tools stay listed; only a settings change restarts (SOURCE 70277c0 `context_server_store.rs:475-498,~795-830`) | no 404 handling, 401 token refresh only | status to Running triggers tools/prompts reload | waits for the 60 s timeout (inferred) | SOURCE |
| **Roo Code** | Roo's close handlers are probably overwritten by the SDK, so status may stay connected; stale tools stay; manual restart or config-file change (SOURCE b867ec9 `McpHub.ts:735-751,1254,1158-1165`) | SSE reconnecting; SDK 1.12.0 has no 404 handling | a new Client re-initializes and re-lists tools and resources, not prompts | `ConnectionClosed`, 60 s timeout | SOURCE |
| **VS Code Copilot** | state `Error`/`Stopped`; the next tool call calls `server.start()` and respawns (no backoff); `workbench.mcp.restartServer`; `dev.watch` globs (SOURCE 4f8853f `mcpServer.ts:353-354`, `extHostMcpNode.ts:139-149`) | GET backchannel retries `n` s capped at 30 s, no cap; 404/400 with a session id sets `shouldRetry` and the call is retried once via a fresh `initialize` (SOURCE `extHostMcp.ts:518-526,595-660`) | new handler runs `initialize` and lists tools and prompts (SOURCE `mcpServer.ts:1211-1240`) | stdio exit: error to the model; HTTP session loss: one retry | SOURCE |
| **Continue** | no close handler; status stays connected and tools stay listed; manual `mcp/reloadServer` or CLI restart; an unchanged config entry is not respawned (SOURCE 5522c6f `MCPManagerSingleton.ts:76-128`) | none in Continue; SDK GET reconnect | re-fetches resources, tools, prompts; no instructions; reloadConfig rebuilds tools | `ConnectionClosed`, no retry | SOURCE |
| **OpenHands SDK** | lazy: the next call runs `client.connect()` once, errors become an observation; no supervisor | same lazy path, no 404 handling | `initialize` yes; **`tools/list` NOT re-run** (only on list_changed); instructions ignored | error observation, no retry | SOURCE 77c6ee5 `mcp/tool.py:126-150`, `mcp/utils.py:248-369` |
| Cursor, Copilot CLI, Antigravity, Grok Build, Windsurf, Amp, Droid | Cursor: "only a full restart is guaranteed" (REPORTED); Droid reloads on `mcp.json` change (DOCS); others UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | see matrix |

### Gap length and in-flight calls

- Claude Code `-p` respawn: process start to ready about the server's own startup, then the call proceeds (VERIFIED:
  `START` and the first `tools/call` 10 ms apart; the failed call's `Connection closed` was 1.2 to 1.5 s earlier). With
  ddflow's own `initialize` at 6 to 20 s today (B-mcp-initialize-folds-log), the user-visible gap of an
  exit-and-respawn upgrade is that startup, plus a failed in-flight call.
- No client resumes or replays an in-flight `tools/call` after a stdio exit (the model gets an error in every
  case read), except Qwen's idempotent replay and one retry on a 404 session loss in opencode, Codex, VS Code,
  Kilo and Claude Code.
- A stable shim never drops the connection, so the in-flight call is handled by SU-drain (worker side) and no
  client behaviour matters.

### Comparison: soft via reconnect vs soft via list_changed vs facade

| Property | Reconnect (exit and respawn the whole server) | list_changed (shim stays, worker swaps) | Facade |
|---|---|---|---|
| Clients where it works without a person | Claude Code `-p` and VS Code on stdio; opencode, Codex, Claude Code, VS Code, Kilo only for HTTP session loss; Qwen possibly | Claude Code, Kilo (VERIFIED), opencode, Gemini, VS Code, Cline, Goose, OpenHands, Zed (SOURCE) | all clients |
| New tool list reaches the model | only VS Code, Qwen (re-lists on reconnect); Claude Code, OpenHands, Kimi keep the stale list (VERIFIED/SOURCE); Kilo, opencode, Gemini, Roo, Continue, Cline need a manual command | yes for the honouring set, additions and removals only | not needed |
| In-flight call | fails (`Connection closed`) | survives (shim holds the connection) | survives |
| Gap | server startup (6 to 20 s for ddflow today) | worker swap time, hidden behind the shim | none |
| Needs a stable shim | no (it is the thing being replaced) | yes | yes |
| Changed schema of an existing tool | reaches the model only where the list is re-fetched | not adopted (Claude Code) | transparent |

Reconnect adds nothing that list_changed does not give, apart from one case: a client that ignores
list_changed but re-lists on reconnect (Qwen, VS Code is already in the honouring set). It is strictly weaker
because it drops the in-flight call and re-pays ddflow's startup. It is also what ddflow already has:
`[mcp].on_upgrade = reexec`. Conclusion: reconnect is a **fallback tier**, not a primary mechanism, and
only for clients the table marks `reconnect = respawn+relist`. Today the only candidates are VS Code (honours
anyway) and Qwen (UNKNOWN whether a stdio exit is noticed). So `SU-reconnect-fallback` is justified only as
"verify Qwen and any client that appears in the table with `ignores` and `respawn+relist`", and as the
policy that selects `reexec` over the restart notice for those clients.

## Refined soft/hard rule (proposal for SU-decision)

Inputs known to the shim: digest class of the change (unchanged | additive | removal | compatible-schema |
incompatible), `facade` on/off, client capability state (`honours` / `ignores` / `unknown`, source
VERIFIED, SOURCE, REPORTED, observed, or `listen` opt-in), `reconnect` state (`respawn+relist`, `other`).

1. **Soft, silent**: digest unchanged, or facade on and only backend operations changed. Any client.
2. **Soft, announced**: additive, removal, or compatible schema change (worker grace of one version accepts the old
   arguments), and capability is `honours` (table VERIFIED/SOURCE, `listen` opt-in on 2026-07-28, or observed).
   Report: "announced to <client>".
3. **Soft, features pending**: additive or removal and the client is `ignores`, or `unknown` and not yet observed.
   Everything the client already holds keeps working: removed tools stay served for one version (deprecated
   stub or grace). The report says "soft upgrade applied; N new tools appear after you restart the agent".
   Announce anyway when `unknown` and run the observation: a re-fetch upgrades the row to `honours(observed)`
   and reports "announced".
4. **Soft via reconnect**: the change is hard by rule 5 but the client is `respawn+relist` (and not a shim
   connection): exit the server and let the client respawn it (`reexec`), reporting that one in-flight call may fail.
5. **Hard**: an existing tool or prompt that the client already holds changes incompatibly (required arguments, renamed,
   changed meaning) AND (the client does not honour list_changed OR the change is a schema change that
   honouring clients do not adopt, Claude Code VERIFIED) AND no facade; or the shim or the shim-worker protocol
   changed. Tell the user to restart the agent and name the reason and the client.

This is the operator's split ("hard only for agents that don't support list_changed AND where the facade of
pre-existing commands/prompts changed"), with two additions: a changed schema of an existing tool is
hard even for honouring clients unless the worker grace covers it (Claude Code re-fetches but does not
adopt, VERIFIED); and an additive change is soft for everyone because a client that never re-fetches simply
does not see the addition. Prompts are never relied on to announce (few clients re-list them).

## UNKNOWN after this pass

| Cell | Needs |
|---|---|
| Claude Code interactive: does stdio respawn on the next call in the TUI (docs say not automatic) | an interactive session |
| Why Claude Code ignored a `list_changed` sent right after a respawn (one run) | repeat with a delay and in the TUI; read `-p` logs |
| Claude Code v2 runtime and `subscriptions/listen` with a modern server | a stateless 2026-07-28 probe server |
| Kilo idle respawn after the run | a long-lived `kilo serve` session |
| Whether a stdio exit raises `onerror` in the Qwen client (health monitor then reconnects) | run `qwen` with `rc.py` |
| Gemini, Codex, opencode, Cline, Roo, Zed, Continue behaviours marked "inferred" or UNKNOWN | the probes in appendices B and C |
| Interactive `/mcp reconnect` effect on the model's tool list in Claude Code | interactive run |

## Appendix B: stdio reconnect probe

Logs `initialize` params and every message with the process pid and a start counter stored in a file. The tool
list contains `genN_marker` where N is the start count, so a client that re-fetched `tools/list` after a respawn
shows the new marker. Tools: `whoami` (generation and pid), `die` (reply, then exit), `die_before_reply`
(exit with the call in flight), `slow` (sleep 8 s). `RC_LC=1` makes every start after the first send
`tools/list_changed` 0.3 s after `initialized`. Run: `claude -p --strict-mcp-config --mcp-config cc.json
--allowedTools="mcp__rc__*" -- "<prompt>"` with `{"mcpServers":{"rc":{"command":"python3","args":["rc.py","logdir"]}}}`;
Kilo: `{"mcp":{"rc":{"type":"local","command":["python3","rc.py","logdir"]}}}` and `kilo run -m google/gemini-2.5-flash`.

```python
#!/usr/bin/env python3
"""Reconnect probe. argv[1]=dir for log+counter."""

import sys, json, os, time, threading

D = sys.argv[1]
os.makedirs(D, exist_ok=True)
cf = os.path.join(D, "count")
n = int(open(cf).read()) + 1 if os.path.exists(cf) else 1
open(cf, "w").write(str(n))
LOG = open(os.path.join(D, "log"), "a")
LK = threading.Lock()


def log(s):
    with LK:
        LOG.write("%.2f pid=%d gen=%d %s\n" % (time.time() % 10000, os.getpid(), n, s))
        LOG.flush()


log("START")


def send(m):
    with LK:
        sys.stdout.write(json.dumps(m) + "\n")
        sys.stdout.flush()
    log("OUT " + json.dumps(m)[:200])


def tools():
    t = lambda name, d: {
        "name": name,
        "description": d,
        "inputSchema": {"type": "object", "properties": {}},
    }
    return [
        t("whoami", "Returns the server generation number and pid."),
        t("die", "Replies then exits the server process immediately (exit 0)."),
        t("die_before_reply", "Exits the server process without replying."),
        t("slow", "Sleeps 8s then replies."),
        t("gen%d_marker" % n, "Marker tool that exists only in server generation %d." % n),
    ]


def sig(*a):
    log("SIGNAL %s" % (a,))
    os._exit(0)


import signal

for s in (signal.SIGTERM, signal.SIGHUP):
    signal.signal(s, sig)
for line in sys.stdin:
    m = json.loads(line)
    meth = m.get("method")
    i = m.get("id")
    log("IN  " + (json.dumps(m["params"]) if meth == "initialize" else line.strip()[:200]))
    if meth == "initialize":
        send(
            {
                "jsonrpc": "2.0",
                "id": i,
                "result": {
                    "protocolVersion": m["params"]["protocolVersion"],
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {"name": "rc", "version": "1"},
                    "instructions": "Probe generation %d." % n,
                },
            }
        )
    elif meth == "notifications/initialized" and n >= 2 and os.environ.get("RC_LC"):
        threading.Timer(
            0.3, lambda: send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        ).start()
    elif meth == "tools/list":
        send({"jsonrpc": "2.0", "id": i, "result": {"tools": tools()}})
    elif meth == "tools/call":
        nm = m["params"]["name"]
        if nm == "die_before_reply":
            log("EXIT")
            os._exit(0)
        if nm == "slow":
            time.sleep(8)
        send(
            {
                "jsonrpc": "2.0",
                "id": i,
                "result": {
                    "content": [{"type": "text", "text": "gen=%d pid=%d" % (n, os.getpid())}]
                },
            }
        )
        if nm == "die":
            log("EXIT")
            os._exit(0)
    elif i is not None:
        send({"jsonrpc": "2.0", "id": i, "result": {}})
log("STDIN EOF")
```

## Appendix C: streamable-HTTP session-expiry probe

`expire` invalidates the current session id after replying, so the next request with it gets 404. Claude Code
re-initialized and retried the call without an error (VERIFIED). Run `python3 http_rc.py 18931` and
`claude -p --strict-mcp-config --mcp-config h.json ...` with `{"mcpServers":{"hrc":{"type":"http","url":"http://127.0.0.1:18931/mcp"}}}`.

```python
"""Streamable-HTTP reconnect probe. argv[1]=port. After tool 'expire' the current session id is invalid: requests with it get 404."""

import sys, json, time, uuid, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LK = threading.Lock()
LOG = open("log", "a")
valid = set()
gen = [0]


def log(s):
    with LK:
        LOG.write("%.2f %s\n" % (time.time() % 10000, s))
        LOG.flush()


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body=None, sid=None):
        b = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        if sid:
            self.send_header("Mcp-Session-Id", sid)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        log("GET sid=%s" % self.headers.get("Mcp-Session-Id"))
        self._send(405)

    def do_DELETE(self):
        log("DELETE")
        self._send(200)

    def do_POST(self):
        m = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        sid = self.headers.get("Mcp-Session-Id")
        meth = m.get("method")
        i = m.get("id")
        log(
            "POST sid=%s %s"
            % (sid and sid[:6], json.dumps(m)[:300] if meth == "initialize" else meth)
        )
        if meth == "initialize":
            sid = uuid.uuid4().hex
            valid.add(sid)
            gen[0] += 1
            return self._send(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": i,
                    "result": {
                        "protocolVersion": m["params"]["protocolVersion"],
                        "capabilities": {"tools": {"listChanged": True}},
                        "serverInfo": {"name": "hrc", "version": "1"},
                    },
                },
                sid,
            )
        if meth == "server/discover":
            return self._send(404)
        if sid not in valid:
            log("-> 404 expired")
            return self._send(
                404,
                {
                    "jsonrpc": "2.0",
                    "id": i,
                    "error": {"code": -32001, "message": "Session not found"},
                },
            )
        if i is None:
            return self._send(202)
        if meth == "tools/list":
            t = lambda n, d: {
                "name": n,
                "description": d,
                "inputSchema": {"type": "object", "properties": {}},
            }
            return self._send(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": i,
                    "result": {
                        "tools": [
                            t("whoami", "Returns session generation"),
                            t("expire", "Invalidate the current session after replying"),
                            t(
                                "sess%d_marker" % gen[0],
                                "exists for session generation %d" % gen[0],
                            ),
                        ]
                    },
                },
            )
        if meth == "tools/call":
            n = m["params"]["name"]
            self._send(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": i,
                    "result": {
                        "content": [
                            {"type": "text", "text": "session-gen=%d sid=%s" % (gen[0], sid[:6])}
                        ]
                    },
                },
            )
            if n == "expire":
                valid.discard(sid)
                log("session invalidated")
            return
        self._send(200, {"jsonrpc": "2.0", "id": i, "result": {}})


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
```
