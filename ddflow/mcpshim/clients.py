"""Which MCP clients honour `notifications/tools/list_changed` (SU-client-table).

The shim consults this to decide whether an interface change can be announced (rung 2 of
D-soft-upgrade-rule) or only takes effect after the agent restarts. Keyed by the lower-cased
`clientInfo.name` a client sends at `initialize` (R-su-client-key); every row carries its evidence
(docs/ddflow/research/mcp-client-matrix.md). Stdlib only: the shim must stay small and independent.

    capability(client_info, ...) -> "honours" | "ignores" | "unknown"      (unknown = hard)

Ways to learn, strongest first: a `subscriptions/listen` opt-in (2026-07-28, the `listen_optin`
argument) -> honours; a runtime observation (SU-observe-client-capability: `honours` or `ignores`;
anything else means nothing was learned) -> what it saw; the table. A table row is trusted as
follows:

- a ranged row (`min_version`) only for versions at or above it (Claude Code and Kilo, whose
  versions are real and were probed); below it, or with no usable version, the answer is unknown;
- any other row by name alone (opencode sends `"unknown"`, Qwen a constant `0.0.1`, Continue a
  constant `1.0.0`: their versions say nothing), and an `honours` row nobody verified expires
  (`MAX_AGE_DAYS` after `checked`) to unknown, because a client can drop support; an `ignores`
  row does not (the safe reading outlives the evidence).

Consumers: the shim (SU-shim) and the announcement (SU-announce); nothing calls it before them.
Prompts' `list_changed` is never relied on. The harness link (`harness`) names the descriptor in
`ddflow/harnesses/` of the same agent, so the two can be joined without a second copy of the data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

HONOURS, IGNORES, UNKNOWN = "honours", "ignores", "unknown"
LIST_CHANGED = (HONOURS, IGNORES, UNKNOWN)
#: Where a row's value comes from: probed here, read in the client's source, reported by a user
#: or maintainer, read in its docs, or learned at runtime.
SOURCES = ("verified", "source", "reported", "docs", "observed")
RECONNECT = ("respawn+relist", "respawn", "manual", "none", "unknown")
HTTP_404 = ("reinit+retry", "none", "unknown")
#: How long an unverified `honours` row is trusted (a window on the data, not a user tunable).
MAX_AGE_DAYS = 180


@dataclass(frozen=True)
class Entry:
    key: str
    list_changed: str
    source: str
    verified_version: str  # the version probed ("" = never run here)
    min_version: str | None  # a ranged row: versions below this are unknown
    checked: str  # ISO date the evidence was gathered
    evidence: str
    names: tuple[str, ...] = ()  # the clientInfo.name strings (lower case); () = not known
    reconnect: str = "unknown"
    http_404: str = "unknown"
    harness: str = ""  # the ddflow/harnesses/<id>.toml of the same agent


def _e(key, lc, source, verified, minv, checked, evidence, **kw) -> Entry:
    kw.setdefault("names", ())
    return Entry(key, lc, source, verified, minv, checked, evidence, **kw)


_M = "docs/ddflow/research/mcp-client-matrix.md"
ENTRIES: tuple[Entry, ...] = (
    _e(
        "claude-code",
        HONOURS,
        "verified",
        "2.1.296",
        "2.1.296",
        "2026-10-21",
        f"{_M}: R-soft-claude-listchanged; additions and removals, not a changed schema",
        names=("claude-code",),
        reconnect="respawn",
        http_404="reinit+retry",
        harness="claude",
    ),
    _e(
        "kilo",
        HONOURS,
        "verified",
        "7.8.12",
        "7.8.12",
        "2026-10-21",
        f"{_M}: tools/list re-fetched within 30 ms, 3 runs",
        names=("kilo",),
        reconnect="manual",
        http_404="reinit+retry",
        harness="kilo",
    ),
    _e(
        "opencode",
        HONOURS,
        "source",
        "",
        None,
        "2026-10-21",
        f"{_M}: anomalyco/opencode 4d59657 mcp/index.ts:416-423; clientInfo.version is 'unknown'",
        names=("opencode",),
        reconnect="manual",
        http_404="reinit+retry",
        harness="opencode",
    ),
    _e(
        "gemini-cli-mcp-client",
        HONOURS,
        "source",
        "",
        None,
        "2026-10-21",
        f"{_M}: google-gemini/gemini-cli c1740d2 mcp-client.ts refreshTools",
        names=("gemini-cli-mcp-client",),
        reconnect="manual",
        http_404="none",
        harness="gemini",
    ),
    _e(
        "vscode-copilot",
        HONOURS,
        "source",
        "",
        None,
        "2026-10-21",
        f"{_M}: microsoft/vscode 3021013 mcpServerRequestHandler.ts:368-380",
        reconnect="respawn+relist",
        http_404="reinit+retry",
        harness="vscode",
    ),
    _e(
        "cline",
        HONOURS,
        "source",
        "",
        None,
        "2026-10-21",
        f"{_M}: cline/cline e9dafbb McpHub.ts:769-776",
        names=("cline",),
        reconnect="manual",
        http_404="none",
        harness="cline",
    ),
    _e(
        "goose",
        HONOURS,
        "source",
        "",
        None,
        "2026-10-21",
        f"{_M}: block/goose 7143dd mcp_client.rs:358",
        names=("goose",),
        reconnect="manual",
        http_404="none",
        harness="goose",
    ),
    _e(
        "openhands",
        HONOURS,
        "source",
        "",
        None,
        "2026-10-21",
        f"{_M}: OpenHands/software-agent-sdk 77c6ee5 mcp/utils.py:254-345 (tools only)",
        reconnect="respawn",
        http_404="none",
        harness="openhands",
    ),
    _e(
        "zed",
        HONOURS,
        "source",
        "",
        None,
        "2026-10-21",
        f"{_M}: zed-industries/zed 3762891 context_server_registry.rs:132-160",
        names=("zed",),
        reconnect="manual",
        http_404="none",
    ),
    _e(
        "codex-mcp-client",
        IGNORES,
        "source",
        "",
        None,
        "2026-10-21",
        f"{_M}: openai/codex 4faf60f: handler behind feature mcp_tool_list_changed, default off",
        names=("codex-mcp-client",),
        reconnect="manual",
        http_404="reinit+retry",
        harness="codex",
    ),
    _e(
        "qwen-code-mcp-client",
        IGNORES,
        "source",
        "",
        None,
        "2026-10-21",
        f"{_M}: QwenLM/qwen-code a0e4149: no inbound tools/list_changed handler; version is a constant",
        names=("qwen-code-mcp-client",),
        reconnect="respawn+relist",
        http_404="reinit+retry",
        harness="qwen",
    ),
    _e(
        "kimi",
        IGNORES,
        "source",
        "",
        None,
        "2026-10-21",
        f"{_M}: MoonshotAI/kimi-cli 9ab1286: list_tools once at connect, no message handler",
        reconnect="none",
        harness="kimi",
    ),
    _e(
        "roo code",
        IGNORES,
        "source",
        "",
        None,
        "2026-10-21",
        f"{_M}: RooCodeInc/Roo-Code b867ec9: no handler (negative grep)",
        names=("roo code",),
        reconnect="manual",
        http_404="none",
    ),
    _e(
        "continue",
        IGNORES,
        "source",
        "",
        None,
        "2026-10-21",
        f"{_M}: continuedev/continue 5522c6f: handler is a commented-out TODO; version is a constant",
        names=("continue-client", "continue-cli-client"),
        reconnect="manual",
        http_404="none",
    ),
    _e(
        "grok-cli",
        IGNORES,
        "source",
        "",
        None,
        "2026-10-21",
        f"{_M}: superagent-ai/grok-cli fb97af8: tools built once at startup",
    ),
    _e(
        "cursor-cli",
        IGNORES,
        "reported",
        "",
        None,
        "2026-10-21",
        f"{_M}: forum 161459: a new tool stays hidden until the session restarts",
        reconnect="manual",
        harness="cursor",
    ),
    _e(
        "cursor-ide",
        IGNORES,
        "reported",
        "",
        None,
        "2026-10-21",
        f"{_M}: forum 166216: cache intentional, list_changed unreliable (stdio maybe ok)",
        reconnect="manual",
        harness="cursor",
    ),
    _e(
        "aider",
        IGNORES,
        "source",
        "",
        None,
        "2026-10-21",
        f"{_M}: no MCP client in the repository (n/a)",
        reconnect="none",
        harness="aider",
    ),
    _e(
        "copilot-cli",
        UNKNOWN,
        "docs",
        "",
        None,
        "2026-10-21",
        f"{_M}: changelog mentions refresh but not a handler",
        harness="copilot",
    ),
    _e(
        "antigravity",
        UNKNOWN,
        "reported",
        "",
        None,
        "2026-10-21",
        f"{_M}: manual Refresh button",
        reconnect="manual",
        harness="antigravity",
    ),
    _e(
        "grok-build",
        UNKNOWN,
        "docs",
        "",
        None,
        "2026-10-21",
        f"{_M}: docs silent; refresh is manual",
        reconnect="manual",
    ),
    _e(
        "windsurf",
        UNKNOWN,
        "reported",
        "",
        None,
        "2026-10-21",
        f"{_M}: only manual refresh documented",
        reconnect="manual",
        harness="windsurf",
    ),
    _e(
        "amp",
        UNKNOWN,
        "docs",
        "",
        None,
        "2026-10-21",
        f"{_M}: background refresh, no handler named",
    ),
    _e(
        "droid",
        UNKNOWN,
        "docs",
        "",
        None,
        "2026-10-21",
        f"{_M}: reloads on mcp.json change; nothing on list_changed",
    ),
)

_BY_NAME = {n: e for e in ENTRIES for n in (e.key, *e.names)}
_BY_KEY = {e.key: e for e in ENTRIES}


def version_tuple(text: object) -> tuple[int, ...] | None:
    """`v2.1.296-beta.1` -> (2, 1, 296); None when there is no numeric version."""
    m = re.match(r"v?(\d+(?:\.\d+)*)", str(text or "").strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def _prerelease(text: object) -> bool:
    return re.match(r"v?\d+(?:\.\d+)*-", str(text or "").strip()) is not None


def _name(client_info: object) -> str:
    name = client_info.get("name") if isinstance(client_info, dict) else None
    return name.strip().lower() if isinstance(name, str) else ""


def lookup(client_info: object) -> Entry | None:
    """The row of the client that sent `client_info` (by name), or None."""
    return _BY_NAME.get(_name(client_info))


def effective(
    entry: Entry, version: object, today: date, *, max_age_days: int = MAX_AGE_DAYS
) -> str:
    """What `entry` says for a client of `version` on `today` (see the module docstring)."""
    if entry.min_version:
        have, need = version_tuple(version), version_tuple(entry.min_version)
        if not (have and need and have >= need):
            return UNKNOWN
        if have == need and _prerelease(version):
            return UNKNOWN  # 2.1.296-beta.1 comes before the verified 2.1.296
        return entry.list_changed
    if entry.list_changed == HONOURS and not entry.verified_version:
        try:
            age = (today - date(*map(int, entry.checked.split("-")))).days
        except (TypeError, ValueError):
            return UNKNOWN
        return entry.list_changed if age <= max_age_days else UNKNOWN
    return entry.list_changed


def capability(
    client_info: object,
    *,
    listen_optin: bool = False,
    observed: str | None = None,
    today: date | None = None,
) -> str:
    """`honours`, `ignores` or `unknown` (treated as hard) for the connected client."""
    if listen_optin:
        return HONOURS
    if observed in (HONOURS, IGNORES):
        return observed
    entry = lookup(client_info)
    if entry is None:
        return UNKNOWN
    version = client_info.get("version") if isinstance(client_info, dict) else None
    return effective(entry, version, today or date.today())
