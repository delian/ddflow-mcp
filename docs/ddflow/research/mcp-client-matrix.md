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
LOG = open(sys.argv[1], "a"); LK = threading.Lock()
def log(s):
    with LK: LOG.write("%.2f %s\n" % (time.time() % 1000, s)); LOG.flush()
base = [{"name": "bump", "description": "Call this: the server adds one new tool and sends notifications/tools/list_changed immediately.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "delaybump", "description": "Call this: the server adds one new tool and sends list_changed 6 seconds LATER (unsolicited).", "inputSchema": {"type": "object", "properties": {}}}]
added = {"name": "secret_oracle", "description": "Returns the word MAGENTA. Only exists after bump or delaybump.", "inputSchema": {"type": "object", "properties": {}}}
tools = list(base)
def send(m):
    with LK: sys.stdout.write(json.dumps(m) + "\n"); sys.stdout.flush()
    log("OUT " + json.dumps(m)[:300])
def later():
    time.sleep(6); send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
for line in sys.stdin:
    m = json.loads(line); log("IN  " + line.strip()[:400])
    meth = m.get("method"); i = m.get("id")
    if meth == "initialize":
        send({"jsonrpc": "2.0", "id": i, "result": {"protocolVersion": m["params"]["protocolVersion"], "capabilities": {"tools": {"listChanged": True}, "prompts": {"listChanged": True}}, "serverInfo": {"name": "lc", "version": "1"}}})
    elif meth == "tools/list": send({"jsonrpc": "2.0", "id": i, "result": {"tools": tools}})
    elif meth == "prompts/list": send({"jsonrpc": "2.0", "id": i, "result": {"prompts": []}})
    elif meth == "resources/list": send({"jsonrpc": "2.0", "id": i, "result": {"resources": []}})
    elif meth == "tools/call":
        n = m["params"]["name"]
        if n in ("bump", "delaybump"):
            if added not in tools: tools.append(added)
            send({"jsonrpc": "2.0", "id": i, "result": {"content": [{"type": "text", "text": "server tool list changed (new tool name not disclosed; list your tools to find it)"}]}})
            if n == "bump": send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
            else: threading.Thread(target=later, daemon=True).start()
        else: send({"jsonrpc": "2.0", "id": i, "result": {"content": [{"type": "text", "text": "MAGENTA"}]}})
    elif i is not None: send({"jsonrpc": "2.0", "id": i, "result": {}})
```

Kilo recipe: `kilo.json` with `{"mcp":{"lc":{"type":"local","command":["python3","probe.py","kilo.log"]}}}`,
then `kilo run -m google/gemini-2.5-flash "call lc bump, then call the new tool and report its output"`.
Claude Code: `claude -p --strict-mcp-config --mcp-config mcp.json`.
