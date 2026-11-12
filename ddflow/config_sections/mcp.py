"""The `[mcp]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ._docs import declare, int_at_least, knob

#: The tool tiers `[mcp].tools` accepts. Kept here, not imported from the MCP surface, so
#: config validation does not depend on the surface above it; `tests/test_mcp_tool_tiers.py`
#: asserts the two lists agree.
MCP_TOOL_TIERS = ("core", "standard", "all")

#: What `[mcp].tools` accepts: the profiles of `surfaces/tools/profiles.py` (`auto` is the lean
#: core unless the client table names another) and the flat tiers above.
MCP_TOOL_CHOICES = ("auto", "lean", "families", "hot+families", *MCP_TOOL_TIERS)
#: `[mcp].next_hints`: how many next-step hints a state-changing result may carry.
MCP_NEXT_HINTS = ("off", "on", "1", "2", "3")
#: `[mcp].output_schemas`.
MCP_OUTPUT_SCHEMAS = ("off", "on")
#: `[mcp].on_upgrade`.
MCP_ON_UPGRADE = ("notify", "reexec", "off")
#: A lane name of `[mcp].lane_tools` becomes part of a file name: no separators, and it starts
#: with a letter or digit, so never a dot or an option-like dash.
_LANE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def parse_lane_tiers(spec: str) -> dict[str, str]:
    """`[mcp].lane_tools` (`lane=tier,lane=tier`) as a dict; ValueError names the bad entry."""
    if not isinstance(spec, str):
        raise ValueError("must be a string of lane=tier pairs")
    out: dict[str, str] = {}
    seen: set[str] = set()
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        lane, sep, tier = (x.strip() for x in part.partition("="))
        if not sep or not _LANE_NAME.fullmatch(lane) or ".." in lane:
            raise ValueError(f"{part!r}: expected lane=tier with a plain lane name")
        if tier not in MCP_TOOL_TIERS:
            raise ValueError(f"{part!r}: tier must be one of {', '.join(MCP_TOOL_TIERS)}")
        if lane.casefold() in seen:  # two names one file on a case-insensitive filesystem
            raise ValueError(f"lane {lane!r} is given twice (names compare without case)")
        seen.add(lane.casefold())
        out[lane] = tier
    return out


def _lane_tools_problem(v: object) -> str:
    """The `check` of `[mcp].lane_tools`: empty when it parses."""
    try:
        parse_lane_tiers(v)  # type: ignore[arg-type]
    except ValueError as e:
        return str(e)
    return ""


@declare("mcp")
@dataclass
class McpConfig:
    """The MCP server's own knobs. A START-TIME choice: read once when a connection starts."""

    tools: str = knob(
        "auto",
        doc="Which tools `tools/list` advertises. `auto` (default) is `lean`, the 9 hot single tools plus the gate, bug, session, task, view and memory families (15 tools, about 30 KB), unless the client table names another profile for the connecting client. `families` is every family (24 tools), `hot+families` adds the 12 most-called direct tools (36). A family is ONE tool with an `action` (`ddflow_gate` with `action=record`); nothing is removed by a profile: every tool, every family action and every old tool name stays callable, and `ddflow_help` names what a profile hides. The flat tiers: `core` (the ~30 tools of the daily loop: brief, next, claim, gates, complete, merge, recall, bugs, lessons, decisions, sessions, setup, help), `standard` (core plus the commonly used rest) or `all` (every tool). A tool outside the tier is NOT removed: it stays callable by name, and `ddflow_help` and the connection instructions name what the tier hides. Read once at server start, so change it and restart the server; `listChanged` stays false. Set it to cut the ~90 KB tool list a client without deferred tool search pays in context every session (core is under 40 KB). A newer release's tier in a config file is tolerated (see below).",
        choices=MCP_TOOL_CHOICES,
        strictest=("all", "no safety dimension; every tool advertised, as without the knob"),
    )

    worker_tools: str = knob(
        "core",
        doc="The tool tier of the MCP config `ddflow adopt` writes for lane workers (`.ddflow/local/worker-mcp.json`, started with `claude --mcp-config <file> --strict-mcp-config`): it sets `DDFLOW_MCP_TOOLS` in the server's environment. `core` (default) saves ~27K first-turn tokens on a host that sends every schema (84.2K against 54.7K measured); `standard` or `all` for workers that need the rest. It hides schemas, never tools: every tool stays callable by name.",
        choices=MCP_TOOL_TIERS,
        strictest=(
            "core",
            "no safety dimension; fewer schemas advertised, every tool still callable",
        ),
    )

    lane_tools: str = knob(
        "",
        doc="Per-lane tool tier for the worker MCP config: comma-separated `lane=tier` pairs (tier `core`, `standard` or `all`), e.g. `impl=standard,review=all`. `ddflow adopt` writes `.ddflow/local/worker-mcp-<lane>.json` for each, the same server as `worker-mcp.json` with `DDFLOW_MCP_TOOLS` set to that lane's tier, so a lane that needs more than `[mcp].worker_tools` starts with `--mcp-config=` on its own file. Empty (default) writes none. A lane name starts with a letter or digit, then letters, digits, `.`, `_` or `-`; names compare without case. A file for a lane dropped from the list is named by adopt, not deleted. `ddflow doctor` counts a lane on `all` as it counts `[mcp].worker_tools`. It hides schemas, never tools.",
        check=_lane_tools_problem,
    )

    profile_lean_budget_tokens: int = knob(
        16000,
        doc="With `[mcp].tools = auto`, a session whose effective start budget (`[session].context_budget_tokens`, or its agent type's entry in `[session.context_budget]`, the smaller) is at most this many tokens gets the `lean` profile whatever the client table says. 0 turns it off. Keep it at or below `profile_mid_budget_tokens` (the first limit that fits wins). The profile is chosen once at `initialize` and a budget only ever narrows it. A client on the modern `server/discover` handshake sends no `clientInfo`, so it keeps the lean default, as it does for the client table.",
        check=int_at_least(0),
    )
    profile_mid_budget_tokens: int = knob(
        32000,
        doc="With `[mcp].tools = auto`, a session whose effective start budget is at most this many tokens (and above `profile_lean_budget_tokens`) gets no profile wider than `families`: a client table entry of `hot+families` is lowered to it. 0 turns it off.",
        check=int_at_least(0),
    )

    output_schemas: str = knob(
        "off",
        doc='Whether `tools/list` declares an `outputSchema` for every tool whose result is a JSON object of declared fields (not `show`, `decision_show` or `recall`, which answer one record or null, nor text), and a call returns that object also as `structuredContent` (not when it failed; MCP 2025-06-18 and later; an older client is served as with `off`). `off` (default): the result is the JSON text in the first content block only, and `tools/list` is as small as before. `on`: each object tool lists `{"type": "object", "properties": {"schema": {"const": "<tool>@<n>"}, <its declared fields>: {}}}` -- additive, nothing required -- and a bare-array tool lists its schema under `_meta["ddflow/outputSchema"]`, since an `outputSchema` must be an object. Costs about 16 KB more `tools/list` and every object result is sent twice (the text block and `structuredContent`). Every object result carries its `schema` tag either way. Read once at server start, so change it and restart the server.',
        choices=MCP_OUTPUT_SCHEMAS,
        strictest=("off", "no safety dimension; the smaller, unchanged tool list and result"),
    )

    next_hints: str = knob(
        "off",
        doc="Whether a state-changing MCP result carries an advisory `next` field: up to 3 hints (and 400 bytes) of `{tool, why, args_hint}` naming what usually follows, from the result already computed (a claim points at `gate status`; `gate status` at the first open gate by kind, and at `merge` only once every gate carries an outcome; `merge` at `complete`; `complete` at `next`; `bug found` at its fix task; `next` at claiming the first ready item). `off` (default: whether hints save wrong calls was not measured); `on` is 3; `1`, `2` or `3` cap the count. A hint names a tool the connection's profile lists (a family action on a family profile, dropped when the profile does not list it); `why` is a fixed sentence, never record text. Advisory: the tool it names still checks the world. Read once at server start.",
        choices=MCP_NEXT_HINTS,
        strictest=("off", "no safety dimension; the unchanged result"),
    )

    on_upgrade: str = knob(
        "notify",
        doc="What a running MCP server does once ddflow was upgraded under it (the installed package, or this project's log, is newer than the code the server imported). `notify` (default): a write the old code would refuse on version skew is answered with the one fix instead (reconnect the MCP server), flagged `restart_required` in the result meta, and every other call carries a one-line note; reads keep working. `reexec`: after answering the current call, once no other request is in flight or waiting, the server replaces itself with the installed code on the same stdio (same protocol, client and declared identity), so the next call just works; with no newer installed package (a restart would change nothing) it behaves as `notify`. `off`: no check, the skew refusal is shown as it is. Checked on a call at most once per `stale_check_every_s`, and on every skew refusal. Unlike `tools`, read per call, so a change applies to a running server.",
        choices=MCP_ON_UPGRADE,
        strictest=("notify", "no restart of the process; the agent is told what to do"),
    )

    stale_check_every_s: float = knob(
        60.0,
        doc="Seconds between a running MCP server's looks at the installed package and the project log's version stamps (`on_upgrade`), on the calls that succeed: each look reads the installed version from disk and folds the log, so it is bounded, not per call. A skew refusal always looks at once. 0 looks on every call.",
    )

    progress_s: float = knob(
        20.0,
        doc="Seconds between the `notifications/progress` an offloaded MCP tool (`ddflow_gate_run`, `ddflow_gate_verify`, `ddflow_ci`, `ddflow_verify`, `ddflow_bisect`, `ddflow_wait`, `ddflow_review`) sends while it runs, whatever it prints, when the client asked for progress (`_meta.progressToken`). Claude Code resets its idle timer on each one and aborts a call that stays silent for its window (30 s in a probe), so a quiet gate would be cut off even though its worker finishes. 0 sends none beyond a tool's own per-line progress. Read per call.",
        check=lambda v: (
            ""
            if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < float("inf")
            else "must be a number, 0 or more, and finite"
        ),
    )

    init_snapshot_max_age_s: float = knob(
        300.0,
        doc="`initialize` and `server/discover` answer from the cached interface snapshot (`.ddflow/local/mcp-interface.json`, written by a server after a handshake it rendered) when it is fresh: written by this ddflow version, for this connection's tool tier and interface digest, and at most this many seconds old. The instruction template is then not rendered, so the handshake costs milliseconds instead of seconds; the queue, companion and rules lines of the instruction text are those of the handshake that wrote the snapshot, so they may lag by up to this many seconds until the handshake text is made byte-stable (B-mcp-instructions-volatile-prefix), which is why the default is short; the volatile upgrade notice is said on the first tool result instead of in the handshake. A snapshot that is not fresh, or is unreadable, is ignored and the handshake is rendered live (and the snapshot rewritten). 0 turns it off: every handshake is rendered live. Read once per connection.",
        check=lambda v: (
            ""
            if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < float("inf")
            else "must be a number, 0 or more, and finite"
        ),
    )
