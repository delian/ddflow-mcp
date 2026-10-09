"""An MCP stdio server for ddflow — implemented in the standard library alone.

Why hand-rolled rather than `pip install mcp`: portability is the entire point of this
project. A workflow kernel that only installs where a package index is reachable is not
portable, and the MCP stdio transport is newline-delimited JSON-RPC 2.0 — a few hundred
lines, fully specified, and stable. Taking a dependency to avoid writing them would
trade the property we are optimising for against a convenience we do not need.

The tool surface is deliberately **the same functions the CLI calls**. MCP is a second
door onto one implementation, never a second implementation. That is what keeps an
agent driving ddflow over MCP and an agent driving it over a shell from diverging —
and it is why an agent with neither (a human, a CI job, a `Makefile`) loses nothing.

Notes on protocol handling:

* Which revisions are served and how each shapes a reply -- ``initialize`` for the legacy
  ones, per-request ``_meta`` for ``2026-07-28``, multi round-trip -- is
  ``mcp_protocol``, the one module that knows about revisions. This one routes methods
  to ddflow; neither it nor any tool sees which revision a request came in under.
* Every tool returns text content. Structured results are JSON *inside* that text,
  because ``structuredContent`` support is uneven across clients and a result an agent
  cannot read is worse than a verbose one it can.
* Errors are returned as ``isError: true`` results rather than JSON-RPC errors, which
  is what lets the model see the failure and correct, instead of the transport
  swallowing it.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from ddflow.core import agentname as AN
from ddflow.core.outcome import EXIT_NAMES, NOTHING, OK, REFUSED, declared_exit, exit_for

from ..config import Config
from ..infra import harness_identity
from ..infra import proc as PROC
from ..infra.log import EventLog
from ..infra.worktree import repo_root

# The tool registry lives in `surfaces/tools/` and the protocol engine in `mcp_protocol`;
# this module routes between them. Every name below is re-exported so imports of
# `ddflow.surfaces.mcp.<name>` keep working.
# The protocol engine -- revisions, negotiation, the modern envelope, multi round-trip --
# is `mcp_protocol`; these names are re-exported so `ddflow.surfaces.mcp.<name>` keeps working.
from . import mcp_protocol as protocol
from . import registry as _REGISTRY
from .mcp_bound import BOUNDS
from .mcp_protocol import (  # noqa: F401
    CACHE_SCOPE,
    CACHE_TTL_MS,
    INVALID_PARAMS,
    META_AGENT,
    META_CLIENT_CAPS,
    META_SERVER_INFO,
    META_VERSION,
    MODERN_PROTOCOLS,
    SERVER_INFO,
    SUPPORTED_PROTOCOLS,
    UNSUPPORTED_PROTOCOL_VERSION,
)
from .mcp_protocol import capabilities as _capabilities  # noqa: F401
from .mcp_protocol import err as _err
from .mcp_protocol import modern_check as _modern_check
from .mcp_protocol import modernize as _modernize
from .mcp_protocol import ok as _ok
from .tools import ADD_TOOLS, DEDUPE_PROPERTIES, TOOLS  # noqa: F401
from .tools._common import (  # noqa: F401
    _AGENT_KEYS,
    MCP_WAIT_DEFAULT_S,
    MCP_WAIT_MAX_S,
    _answer,
    _api,
    _bisect,
    _bug_reopen,
    _configure_reported,
    _list_or_none,
    _opt,
    _regression_tests,
    _reopening,
    _wait_timeout,
)
from .tools.tiers import (  # noqa: F401
    CORE_TOOLS,
    DEFAULT_TIER,
    FULL_ONLY_TOOLS,
    STANDARD_EXTRA_TOOLS,
    TIERS,
    resolve_output_schemas,
    resolve_tier,
    tier_note,
    tier_tools,
)

#: The per-CALL identity override every tool accepts except `ddflow_identify` itself.
#:
#: `ddflow_identify` declares identity per CONNECTION, which covers several agents that
#: each spawn their own server. It does not cover the configuration Claude Code actually
#: runs: subagents dispatched by one session share that session's MCP connection, so a
#: subagent that called `ddflow_identify` would re-identify its PARENT and every sibling
#: mid-flight. Their claims then share one holder -- and the glob-conflict check skips a
#: holder's own leases (`leases.acquire`), so two subagents claiming overlapping files
#: are both granted. The CLI has always had the per-call form (`--agent`); this is the
#: same thing on the other surface.
#: The skew-guard override (decision D-upgrade-skew-guard): the REASON an agent was told by the
#: operator to let this older ddflow write anyway. Accepted by every tool, stripped before the
#: tool sees its arguments, and deliberately NOT in any schema -- 90 copies of it would cost
#: more of every session's context than the rare refusal it answers; the refusal message and
#: the connection instructions name it where it is needed.
ALLOW_OLDER = "allow_older_version"
AS_AGENT = _REGISTRY.AS_AGENT
_AS_AGENT_SPEC = _REGISTRY.AS_AGENT_SPEC


def _properties(spec: dict[str, Any]) -> dict[str, tuple[str, str, bool]]:
    """A tool's declared properties plus `as_agent`, which every tool but `identify`
    takes. One function, so the schema a client sees and the argument check the
    dispatcher applies can never disagree about it."""
    props = dict(spec["properties"])
    if not spec.get("identify"):
        props[AS_AGENT] = _AS_AGENT_SPEC
    return props


def _schema(spec: dict[str, Any]) -> dict[str, Any]:
    # Generated by the command registry, the one place that knows the schema's shape.
    return _REGISTRY.properties_schema(_properties(spec), deprecated=spec.get("deprecated"))


#: The first protocol revision with `outputSchema` and `structuredContent`.
_STRUCTURED_SINCE = "2025-06-18"


def _result_declaration(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    """What `tools/list` says about a tool's RESULT (D-compat-json-views): an `outputSchema`
    for an object result; for a bare array, whose schema cannot be a root `outputSchema`
    (that must describe an object), `_meta["ddflow/outputSchema"]`; nothing for text, a
    nested record or a body the call decides."""
    command = _REGISTRY.command_name(name)
    payload, text = spec.get("payload", ""), spec.get("text", False)
    extra = _OPTIONAL_KEYS if isinstance(payload, tuple) else ()
    if (schema := _REGISTRY.output_schema(command, payload, text=text, extra=extra)) is not None:
        return {"outputSchema": schema}
    if (array := _REGISTRY.array_schema(command, payload)) is not None:
        return {"_meta": {"ddflow/outputSchema": array}}
    return {}


def _refusal_said(out: Any, payload_key: Any, body: Any, data: dict[str, Any]) -> dict[str, Any]:
    """The fields that follow the `refusal` lead: what the operation said, in the tool's order."""
    projected = isinstance(payload_key, tuple)
    if not isinstance(body, dict):
        # A string payload key names the one field the body was cut down to; unset, it is
        # the null that used to lead. What the operation did say still rides along.
        said = {k: v for k, v in data.items() if k != payload_key or v is not None}
    else:
        said = dict(body)
        if projected:
            # Declared keys the operation never set stay out (not invented as null) on
            # exit 1 and 3; exit 2 keeps its declared keys. Everything else it said is added.
            if out.exit != NOTHING:
                said = {k: v for k, v in said.items() if k in data}
            said.update({k: v for k, v in data.items() if k not in said})
    return said


def _refusal_body(out: Any, payload_key: Any, body: Any) -> Any:
    """The JSON body of a call that did not do what was asked, led by WHY.

    A refused call used to return the tool's SUCCESS projection: `claim` refused for an
    overlap answered `{"item": null, "holder": null, "worktree": null, ...}` with the
    reason only in a second block, and the data the refusal did carry (the alternatives
    it names, the id) was projected away. An agent reading the first block -- which is
    what the wire contract tells a machine to read -- saw a broken success (B9cf58aaeaa).

    So a JSON body leads with a `refusal` object (reason first, then `outcome` and
    `exit`) on every REFUSAL (exit 3), and on any other non-zero exit whose declared shape
    the operation did not fill -- the null-padded success schema is the bug, whatever
    the code. The lead is named `refusal` for the call that "did not do what was asked",
    not for exit 3 alone: `refusal.exit` and `refusal.outcome` say which (`failed`,
    `nothing` or `refused`), and `_meta.exit` carries the same code. After the lead come
    the declared fields the operation set and every other wire field of its data -- on
    exit 1, 2 and 3 alike (claim's `alternatives`, later duplicate candidates). A field it
    never set is not invented as null on exit 1 and 3. Exit 2 keeps ALL the declared keys
    (null where unset), in the order the tool declares them, because "nothing" is still that
    tool's answer. (An operation that sets every declared key at exit 2 -- `heartbeat` with
    no lease, `workflow_drop` of a gate in no pipeline -- fills its shape and is left
    alone, byte-identical to `--json`; the padded case is one that sets fewer.)

    A tool whose body is ONE data field (`ddflow_decision_show` answers its `decision`) is
    a null or scalar when that field is unset; a non-zero exit then still leads with the
    refusal, followed by the rest of the data (`id`), not the bare `null`.

    An exit 1 or 2 body that fills its declared shape is a RESULT and is left alone,
    byte-identical to the CLI's `--json`: `gate verify` fails WITH its results, `next` on
    an empty queue and `companions` with gaps are answers. So is an array at any exit
    (`loops` exits 1 with its findings). Their reason is the second content block.

    `refusal` is a name no operation's data uses; `outcome` and `reason` are both wire
    fields of some tools (`gate record`, `gate verify`) and could not lead unambiguously.
    Should a data field ever be named `refusal`, it is kept, as `refusal_data` (or that
    name with `_` appended while it is taken), never dropped.
    """
    if out.exit == 0 or isinstance(body, list):
        return body
    data = {k: v for k, v in out.data.items() if not k.startswith("_")}
    scalar = not isinstance(body, dict)
    projected = isinstance(payload_key, tuple)
    padded = projected and any(k not in data for k in payload_key)
    if not scalar and out.exit != REFUSED and not padded:
        return body
    lead: dict[str, Any] = {
        "refusal": {
            "reason": out.reason,
            "outcome": EXIT_NAMES.get(out.exit, str(out.exit)),
            "exit": out.exit,
        }
    }
    said = _refusal_said(out, payload_key, body, data)
    if "refusal" in said:
        key = "refusal_data"
        while key in said:  # never overwrite a field the operation also has
            key += "_"
        said[key] = said.pop("refusal")
    return {**lead, **said}


def _bounds() -> dict[str, Any]:
    return BOUNDS


def _outcome_result(
    out: Any,
    payload_key: str | tuple[str, ...] = "",
    *,
    as_text: bool = False,
    bound: Any = None,
    args: dict[str, Any] | None = None,
    command: str = "",
    structured: bool = False,
) -> dict[str, Any]:
    """An `Outcome` as an MCP tool result: JSON body, `isError` only for a real failure.

    Exit 2 ("nothing to do") and 3 ("coordination refused") are RESULTS the model must
    read and act on, exactly as on the string path. Only 1 is a failure. The reason,
    when there is one, leads the body: a caller that reads the first line has the
    actionable part, which is what the spec means by feedback a model can self-correct
    from.
    """
    # `payload_key` preserves a tool's EXISTING wire shape across migration. Moving
    # `ddflow_loops` to the typed path silently changed its body from a JSON array of
    # findings to an object wrapping them, breaking every consumer that iterated it --
    # two demo scenarios did. B37 exists to remove a duplicated rendering, not to
    # redefine contracts, and a migration that changes the wire format is worse than no
    # migration: the duplication was at least honest about what it returned.
    # `Outcome.body` is the ONE implementation of that projection, shared with the
    # CLI's `--json` -- which is the point, since the property being preserved is that
    # the two are the same parsed body. (MCP encodes it compact, the CLI indented, so
    # "byte-identical" is the parsed value, not the text; five reads are also cut
    # on MCP -- `mcp_bound`.)
    #
    # `as_text` covers the tools whose body is PROSE and always has been: `board` is
    # markdown, `doctor` is a report an operator reads, `replay` is a reconstruction
    # document. Their argv form carries no `--json`, so the string path returned the
    # human rendering -- and JSON-encoding it during migration would hand every existing
    # consumer one quoted string with `\n` in it instead of the document they parse.
    # The rendering itself lives in `views/`, below both surfaces, so this is a choice of
    # ENCODING here and not a second renderer.
    if not as_text and (out.data.get("extended") or out.data.get("check_only")):
        # An add that went onto an existing record, or a dry run: what the check decided
        # IS the answer, and the tool's usual `{"id": ...}` projection would drop it.
        payload_key = ""
    if isinstance(payload_key, tuple):
        # merge / complete carry their optional extras as the CLI's --json does: what the
        # document refresh did (B-export-refresh), the base's health after a merge
        # ([ci].on_merge) and the progress block after a completion. Absent when nothing
        # was produced, so the shape is unchanged.
        payload_key = (
            *payload_key,
            *(k for k in _OPTIONAL_KEYS if k in out.data and k not in payload_key),
        )
    if as_text:
        body = out.body(payload_key)
        if not isinstance(body, str):
            raise TypeError(
                f"{out.kind}: declared `text` but its body is a {type(body).__name__}. "
                f"A text tool's payload must name a rendered string."
            )
        # The reason is NOT prepended to a document. `doctor` ends with "2 problem(s)."
        # and the string path returned the report alone, so prefixing it both duplicates
        # the summary and changes a body consumers already parse. A document's renderer
        # decides its own lead; that is what makes it a document.
        #
        # Unless it is EMPTY — then the reason is all there is, and returning nothing for
        # a failed call is the unavailable-as-success class with no text to hide behind.
        if not body.strip() and out.reason:
            body = out.reason
        return _text(body, error=(out.exit == 1), meta={"exit": out.exit})

    full = _refusal_body(out, payload_key, out.body(payload_key))
    note = None
    if bound is not None:
        # The bounded reads (`mcp_bound`): the full body, cut and said so. The CLI's
        # `--json` is the whole body, and the parsed MCP body equals it except here.
        full, note = bound(full, args or {})
    # The schema tag (D-compat-json-views): an object body names its schema, the same name
    # `--json` gives it (`render.emit_json`); an array or a scalar keeps its exact shape.
    full = _REGISTRY.tag_body(full, command)
    # Compact: a model reads every byte of this and indentation is a quarter of it.
    body = json.dumps(full, separators=(",", ":"), default=str)
    result = _text(body, error=(out.exit == 1), meta={"exit": out.exit})
    if structured and isinstance(full, dict) and out.exit != 1:
        # The same object (as JSON, so a value only `default=str` can write is its string), for a client that validates against the tool's `outputSchema`;
        # the text block stays (a client without structured support reads it).
        result["structuredContent"] = json.loads(body)  # as sent: `default=str` applied
    if out.reason:
        # A SECOND content block, never a prefix. The reason used to be prepended to the
        # JSON, which reads well and breaks every machine consumer: `json.loads` on
        # `content[0].text` fails at character 0. `demos/harness.py::jtool` does exactly
        # that, and three of the six demo scenarios broke silently during the B37
        # migration — for every tool whose outcome is exit 2 or 3, which is most of the
        # read-only ones on a fresh project.
        #
        # The wire-shape test did not catch it because it skipped to the first `{` or `[`
        # before parsing. Its own docstring warns about precisely that kind of
        # accommodation ("validated the contents while accommodating the exact shape
        # change it was written to prevent") and it had one anyway.
        #
        # Both readers are served: a machine indexes `content[0]`, and a model is shown
        # every block, so the reason still reaches the thing that has to act on it. A
        # refusal's JSON body ALSO leads with it (`_refusal_body`): the block a machine
        # reads must not be the success shape in nulls (B9cf58aaeaa). The prose block
        # stays, for the array bodies that cannot carry it and for a model reading text.
        # `_meta.exit` carries the code either way.
        result["content"].append({"type": "text", "text": out.reason})
    if note:
        # After the reason, so the reason keeps the position documented above; the
        # bounded read's statement of what it cut is the block after it.
        result["content"].append({"type": "text", "text": note})
    return result


def _identity() -> Any:
    """`api.identity`, imported on first use: a module-level import would pull the whole api
    layer (and its jinja2) into `import ddflow.surfaces.mcp`, which `scripts/bump.sh` runs
    under a bare interpreter."""
    from ..api import identity  # deferred: the api package reaches jinja2

    return identity


def _surf() -> Any:
    """`api.surf_mcp`, imported on first use for the same reason as `_identity`: it reaches
    the template engine, and everything the engine reads from the domain layers goes
    through it."""
    from ..api import surf_mcp  # deferred: reaches jinja2 (scripts/bump.sh has none)

    return surf_mcp


def _default_agent(repo: Path) -> tuple[str, str]:
    """(identity, where it came from) for a connection that declared none.

    The SOURCE matters as much as the name. "you are `alpha`, from DDFLOW_AGENT" and
    "you are `alpha`, because that is this directory's name" call for different
    reactions: the first was set deliberately by whatever spawned you, the second is a
    guess that every sibling in this tree will make identically.
    """
    # The EFFECTIVE default, not the tree-derived one. Reporting the tree name while
    # `DDFLOW_AGENT` was set made `ddflow_identify` misreport the single thing it
    # exists to make visible.

    try:
        cfg = Config.load(repo)
    except Exception:
        cfg = None
    who, layer = _identity().resolve(repo, cfg)
    return who, {
        "env": "from DDFLOW_AGENT",
        "config": "from [agent].id in config",
        "derived": "derived from the working tree",
        "explicit": "declared",
    }[layer]


@dataclass(frozen=True)
class Resource:
    """One MCP resource: what `resources/list` says of it and how `resources/read` serves it.

    Declared once, in `RESOURCES`: the URI list and the read map used to be two literals that
    had to agree, and a dead entry in one that the other shadowed is how a second path hides.
    Every resource is read through the api, like every tool (B97)."""

    uri: str
    name: str
    description: str
    read: Callable[[Path], str]
    mime: str = "text/markdown"

    def listing(self) -> dict[str, str]:
        return {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime,
        }


def _rendered(show: str) -> Callable[[Path], str]:
    return lambda repo: _api().render(repo, show=show).data["text"]


#: The resources a client can read, in the order `resources/list` shows them.
RESOURCES: tuple[Resource, ...] = (
    Resource(
        "ddflow://board",
        "Work queue",
        "The full queue with the critical path.",
        lambda repo: _api().board(repo).data["text"],
    ),
    Resource(
        "ddflow://brief",
        "Session brief",
        "Budgeted session-start pack.",
        lambda repo: _api().brief(repo).data["text"],
    ),
    Resource("ddflow://lessons", "Lessons", "Everything learned so far.", _rendered("lessons")),
    Resource(
        "ddflow://lessons-summary",
        "Lessons summary",
        "Every live lesson in one paragraph, by tag.",
        _rendered("lessons-summary"),
    ),
    Resource(
        "ddflow://research",
        "Research log",
        "Findings with verdicts and probes.",
        _rendered("research"),
    ),
    Resource(
        "ddflow://bugs",
        "Bugs",
        "Every bug with its item, its regression tests and its lesson.",
        _rendered("bugs"),
    ),
    Resource(
        "ddflow://decisions",
        "Decisions",
        "The architectural decisions in force.",
        _rendered("decisions"),
    ),
    Resource(
        "ddflow://sessions",
        "Sessions",
        "The sessions, when they ran and what they were for.",
        _rendered("sessions"),
    ),
)
RESOURCE_BY_URI: dict[str, Resource] = {r.uri: r for r in RESOURCES}


@dataclass
class _Call:
    """What one checked `tools/call` carries from `_m_tools_call` to `_call_api`."""

    name: str
    spec: dict[str, Any]
    args: dict[str, Any]
    agent: str
    per_call: str
    params: dict[str, Any]
    modern: bool
    allow_older: Any
    retired: dict[str, str]
    used: Any


class Server:
    """One connection. Which, deliberately, is not the same thing as one agent.

    Identity is how every attribution in the log works -- who holds a lease, who ran a
    gate, whether the reviewer was a different agent than the author. The default is
    derived from the working tree (`EventLog.default_agent_id`), and that is right for
    the ordinary case of one agent per worktree.

    It is WRONG, silently, for the case this tool exists to support: several agents or
    subagents working the same tree at once. Each spawns its own stdio server, every
    one of them resolves the same cwd to the same identity, and their events merge into
    one indistinguishable stream. Nothing errors. `brief` then reports another agent's
    item as "what you were doing", and reviewer-independence compares an agent with
    itself and is satisfied.

    There is no signal that can distinguish them -- so identity has to be DECLARED:
    `DDFLOW_AGENT` in the environment, or `ddflow_identify` on the connection, or an
    `as_agent` argument on the individual call. Explicit beats derived, innermost wins.
    """

    def __init__(self, repo: Path, agent: str = "", *, called_from: Path | None = None) -> None:
        self.repo = Path(repo)
        #: WHERE THE CALLER IS, unresolved. `self.repo` is the primary checkout -- that
        #: is what makes every worktree share one event log -- and resolving to it threw
        #: away the fact `claim` needs: whether the caller was already inside a worktree.
        #: Worktree ADOPTION was therefore unreachable from MCP, which is the surface the
        #: harnesses it was written for (Claude Code, Cursor) actually drive.
        self.called_from = Path(called_from) if called_from else self.repo
        self.protocol = SUPPORTED_PROTOCOLS[0]
        #: Old names (aliases) this connection has already been told are deprecated: said
        #: once per session (D-compat).
        self._notices = _REGISTRY.Notices()
        #: The stale-server note (`_stale_footer`): said once, looked for once a minute.
        self._stale_said = False
        self._stale_checked_at = float("-inf")
        #: Declared identity for this connection; empty means "use the process
        #: default", which is the backward-compatible single-agent behaviour.
        self.agent = agent
        if not agent:
            # A server restarted under the same harness keeps what the agent declared
            # there, or its shell (which still reads the record) and it would split.
            self.agent = harness_identity.own(self.repo)
        #: Which tools `tools/list` advertises: `[mcp].tools`, read ONCE here. Start-time
        #: only -- `listChanged` is false and there is no call that widens it.
        self.tier = resolve_tier(self.repo)
        #: `[mcp].output_schemas`, read ONCE here like the tier: declare an `outputSchema`
        #: and return `structuredContent` (D-compat-json-views).
        self.output_schemas = resolve_output_schemas(self.repo)
        #: What the client called itself at `initialize`. A LABEL, never an identity:
        #: every subagent of one harness reports the same `clientInfo.name`, so using
        #: it as an id would reproduce the exact collapse above while looking specific.
        self.client_info: dict[str, Any] = {}
        #: Re-instruction cadence, per CONNECTION. `initialize` delivers the rules once and
        #: nothing re-states them afterwards; after a context compaction the model may
        #: retain none of it, and MCP has no server->client context-injection primitive. A
        #: footer on tool results is the only channel that survives, so these two counters
        #: decide how often it is allowed to speak. Per-connection because that is the
        #: lifetime of the context it is compensating for.
        self._calls_since_footer = 0
        self._last_footer_at = 0.0
        #: Writes one JSON-RPC frame to the client; `serve` sets it. Without it (a bare
        #: `handle` call) a long tool simply sends no progress.
        self.notify: Callable[[dict[str, Any]], None] | None = None

    def _structured(self, modern: bool) -> bool:
        """Whether this request is served with `outputSchema` / `structuredContent`: the knob
        is on and the client's revision has them (every modern revision does)."""
        return self.output_schemas and (modern or self.protocol >= _STRUCTURED_SINCE)

    def _progress(self, params: dict[str, Any]) -> Callable[[str], None] | None:
        """A per-line `notifications/progress` sender for this call, or None.

        A client that sent `_meta.progressToken` resets its idle timer on each one; a
        review of 2055 s was aborted at 1800 s with none (bug B206)."""
        meta = params.get("_meta")
        token = meta.get("progressToken") if isinstance(meta, dict) else None
        send = self.notify
        if token is None or send is None:
            return None
        count = [0]

        def say(line: str) -> None:
            count[0] += 1
            try:
                send(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/progress",
                        "params": {
                            "progressToken": token,
                            "progress": count[0],
                            "message": line.strip()[:500],
                        },
                    }
                )
            except Exception:  # a closed pipe must not fail the review
                pass

        return say

    def _someone_else(self, per_call: str) -> bool:
        """Does a per-call name -- `as_agent`, or a stateless request's `_meta` agent --
        name an agent OTHER than this connection's own?

        The connection's own identity is the declared one, else the derived default --
        the same answer `ddflow_identify` reports. Naming it again per call is the same
        agent; naming anyone else is a subagent riding this connection.
        """
        if not per_call:
            return False
        own = self.agent or _default_agent(self.repo)[0]
        return per_call != own

    def _caller(
        self, params: dict[str, Any], args: dict[str, Any], modern: bool
    ) -> tuple[str, str, dict[str, Any], str]:
        """(the agent this call runs as, the name the call itself gave -- "" for none --,
        the arguments without `as_agent`, why the name cannot be used -- "" when it can).

        The connection's identity, unless the call names one: on a stateless request its
        `_meta` agent (D-mcp-identity-per-call), and on any request the `as_agent`
        argument, which wins over both. Either is a PER-CALL name everywhere it counts:
        naming anyone but the connection's own identity, a claim does not adopt the tree
        the server stands in (`_someone_else`) -- a stateless caller cannot be told
        apart from a subagent riding the connection (B7c7a0d9222)."""
        if AS_AGENT in args:  # the argument first: when it is given, `_meta` is not read
            args = dict(args)
            want = args.pop(AS_AGENT)
            if not isinstance(want, str):
                return self.agent, "", args, f"{AS_AGENT} must be a string"
            want = want.strip()
            if want and not AN.is_valid(want):
                return self.agent, "", args, AN.refusal(want)
            if want:
                return want, want, args, ""
        if not modern:
            return self.agent, "", args, ""
        per_call, bad = _meta_agent(params)
        return per_call or self.agent, per_call, args, bad

    def _invoke(self, spec, args, agent, per_call, params):
        """Run a tool's typed `api`; the connection facts it needs are passed in."""
        # `called_from` only where the tool asks for it. `claim` is the one
        # operation whose behaviour depends on WHERE the caller is standing
        # rather than which repo it is in: an agent whose harness already put
        # it in a worktree should have that tree ADOPTED, and resolving to the
        # primary loses the only fact that says so. `main()` computed it and
        # discarded it, which is why adoption was unreachable from MCP.
        if spec.get("wants_called_from"):
            where = self.called_from
            if self._someone_else(per_call):
                # Where the connection stands is where the CONNECTION's
                # identity works, for EVERY tool that asks. A subagent sharing
                # it (Claude Code's do) is not standing in its parent's
                # harness tree: `claim` adopted that tree and branch
                # (B7c7a0d9222), and for an item claimed --no-worktree,
                # `gate_run` ran the parent's tree as the subagent's pass and
                # `merge` landed the parent's branch (B11e4c5a185). Asked from
                # the primary, the ITEM decides -- its own tree and branch,
                # else the "name the branch" answers -- as from the CLI.
                where = self.repo
            extra = {}
            if spec.get("wants_progress") and (say := self._progress(params)):
                extra["on_progress"] = say
            result = spec["api"](self.repo, args, agent, called_from=where, **extra)
        else:
            result = spec["api"](self.repo, args, agent)
        return result

    def _override_skew(self, reason: Any, agent: str) -> None:
        """Record the session-scoped skew override this call carries (`allow_older_version`)."""

        if not isinstance(reason, str):
            raise ValueError("allow_older_version must be a string: the reason")
        cfg = Config.load(self.repo)
        log = EventLog(self.repo, _identity().resolve(self.repo, cfg, agent).id, log_cfg=cfg.log)
        log.override_skew(reason)

    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """One JSON-RPC message, in whichever era the message itself declares.

        A request carrying the modern `_meta` is checked and answered by the `2026-07-28`
        rules (`_modern_check`, `_modernize`); anything else -- `initialize` and every
        request after it -- is served exactly as before. The era is read from each
        message, never from the connection: a modern request relies on nothing an
        earlier request said.
        """
        method = msg.get("method", "")
        if method == "initialize":
            return self._dispatch(msg)
        era = _modern_check(msg)
        if era is None:
            return self._dispatch(msg)
        if "id" not in msg:
            # A notification is never answered -- not with a refusal, and not with a
            # result either (`server/discover` sent without an id would otherwise get one).
            if not isinstance(era, dict):
                self._dispatch(msg)
            return None
        if isinstance(era, dict):
            return era
        if method == "server/discover":
            reply: dict[str, Any] | None = _ok(msg.get("id"), self._discover())
        else:
            reply = self._dispatch(msg, modern=True)
        return _modernize(method, reply)

    def _discover(self) -> dict[str, Any]:
        """`server/discover`: the versions, capabilities, identity and instructions a
        modern client would otherwise have learned from `initialize`. `resultType`, the
        identity in `_meta` and the cache hints are added by `_modernize`, like every
        modern result's."""
        return protocol.discover(_instructions(self.repo, self.agent, self.tier))

    def _dispatch(self, msg: dict[str, Any], *, modern: bool = False) -> dict[str, Any] | None:
        """Answer one JSON-RPC message: the method's own handler, else `method not found`.

        One handler per method (`_METHODS`), each taking the message and the era it was
        sent in. The era is the message's own; only `_structured` falls back on the
        protocol a legacy connection negotiated at `initialize`."""
        method = msg.get("method", "")
        handler = self._METHODS.get(method) if isinstance(method, str) else None
        if handler is None:
            return _err(msg.get("id"), -32601, f"method not found: {method}")
        return handler(self, msg, modern)

    def _m_initialize(self, msg: dict[str, Any], modern: bool) -> dict[str, Any] | None:
        params = msg.get("params") or {}
        want = params.get("protocolVersion", "")
        self.protocol = protocol.legacy_version(want)
        ci = params.get("clientInfo")
        self.client_info = dict(ci) if isinstance(ci, dict) else {}
        return _ok(
            msg.get("id"),
            protocol.initialize_result(
                self.protocol, _instructions(self.repo, self.agent, self.tier)
            ),
        )

    def _m_silent(self, msg: dict[str, Any], modern: bool) -> dict[str, Any] | None:
        """`notifications/initialized` and `notifications/cancelled`: nothing is answered."""
        return None

    def _m_ping(self, msg: dict[str, Any], modern: bool) -> dict[str, Any] | None:
        return _ok(msg.get("id"), {})

    def _m_tools_list(self, msg: dict[str, Any], modern: bool) -> dict[str, Any] | None:
        advertised = tier_tools(self.tier)
        return _ok(
            msg.get("id"),
            {
                "tools": [
                    {
                        "name": n,
                        "description": s["description"],
                        "inputSchema": _schema(s),
                        **(_result_declaration(n, s) if self._structured(modern) else {}),
                    }
                    for n, s in sorted(TOOLS.items())
                    if n in advertised
                ]
            },
        )

    def _m_tools_call(self, msg: dict[str, Any], modern: bool) -> dict[str, Any] | None:
        """`tools/call`: check the call (`_check_call`), then identify or run the tool."""
        mid = msg.get("id")
        params = msg.get("params") or {}
        args = params.get("arguments") or {}
        allow_older: Any = None
        if ALLOW_OLDER in args:
            args = dict(args)
            allow_older = args.pop(ALLOW_OLDER)
        name, spec, args, used, clash = _resolve_call(params.get("name", ""), args)
        problem = self._check_call(name, spec, args, clash)
        if problem:
            return _ok(mid, _text(problem, error=True))
        # An argument kept only for callers of an older release: dropped here, so no api
        # lambda sees it, and said once in the reply (`deprecation_note`).
        retired = {n: why for n, why in (spec.get("deprecated") or {}).items() if n in args}
        if retired:
            args = {k: v for k, v in args.items() if k not in retired}
        # The per-call identity, stripped BEFORE the tool sees its arguments so no
        # api lambda has to know it exists. Validated with the same rule as a
        # declaration: it becomes a log shard filename either way.
        agent, per_call, args, bad = self._caller(params, args, modern)
        if bad:
            return _ok(mid, _text(bad, error=True))
        # The typed path, when this tool has one. No argv, no re-parsing, no
        # scraping stdout, and no swapping process-global streams -- which is what
        # made the string path non-reentrant. `api` is where a protocol adapter
        # belongs: above the domain, beside the other surface, not THROUGH it.
        if spec.get("identify") and modern:
            return _ok(mid, _text(_modern_identify_note(agent)))
        if spec.get("identify"):
            return _ok(mid, self._identify(args))
        if "api" in spec:
            return self._call_api(
                mid,
                _Call(
                    name, spec, args, agent, per_call, params, modern, allow_older, retired, used
                ),
            )
        # No argv fallback. Every tool declares `api`, `ARGV_TOOLS_CEILING` is 0, and
        # `test_every_tool_has_exactly_one_dispatch_mechanism` requires exactly one
        # mechanism per tool — so a tool arriving here has NO dispatch, which is a
        # packaging fault rather than a caller error. Said plainly instead of falling
        # through to a path that no longer exists.
        return _ok(
            mid,
            _text(
                f"{name} declares no dispatch mechanism. This is a ddflow bug, not a "
                f"problem with the call.",
                error=True,
            ),
        )

    @staticmethod
    def _check_call(name: str, spec: Any, args: dict[str, Any], clash: Any) -> str:
        """Why a `tools/call` cannot be run as asked ("" when it can): an unknown tool, a
        clashing spelling, a missing required argument, or an argument the tool lacks."""
        if spec is None:
            return f"unknown tool {name!r}. Available: {', '.join(sorted(TOOLS))}" + (
                _REGISTRY.unknown_tool_hint(TOOLS, name)
            )
        if clash:
            return f"bad arguments: {clash}"
        missing = [n for n, (_t, _d, req) in spec["properties"].items() if req and not args.get(n)]
        if missing:
            return f"missing required argument(s): {', '.join(missing)}"
        # An argument this tool does not have is an ERROR, not something to drop.
        # Every schema here declares `additionalProperties: false` and nothing
        # enforced it, so a caller passing `id="D1"` to a tool with no `id` got a
        # success and a decision under a generated id — then `supersedes: D1`
        # pointed at nothing. Silence at an API boundary is the silent-knob-drop
        # class, and an agent cannot see it at all: it has only the reply.
        known = _properties(spec)
        unknown = sorted(set(args) - set(known))
        if unknown:
            return (
                f"unknown argument(s) for {name}: {', '.join(unknown)}. "
                f"Known: {', '.join(sorted(set(known) - set(spec.get('deprecated') or {})))}"
                + _REGISTRY.unknown_arg_hint(
                    set(known) - set(spec.get("deprecated") or {}), unknown
                )
            )
        return ""

    def _identify(self, args: dict[str, Any]) -> dict[str, Any]:
        """`ddflow_identify` on a legacy connection: declare who this connection is."""
        want = args.get("agent", "")
        if not isinstance(want, str):
            return _text("agent must be a string", error=True)
        want = want.strip()
        # A name that is not usable as a log shard filename is refused HERE,
        # where the agent can read the reason and retry, rather than at the
        # first write -- by which point the caller believes it is identified.
        if want and not AN.is_valid(want):
            return _text(AN.refusal(want), error=True)
        self.agent = want
        # The same agent's shell commands take it too (Bfad021e8d9).

        shell = harness_identity.declare(self.repo, want)
        if want:
            detail = "declared on this connection" + (
                f"; {shell}: pass --agent to the CLI" if shell else ", and for its shell"
            )
        else:
            want_who, detail = _default_agent(self.repo)
            who = want_who
            detail = f"not declared; {detail}"
        who = want or who
        note = ""
        if not want and detail.endswith("working tree"):
            note = (
                " Every agent in this tree derives the SAME name, so if you are "
                "one of several here, declare one."
            )
        if not want and shell:
            note += f" Its shell may still use the previous name ({shell})."
        return _text(
            f"identified as {who!r} ({detail}). Claims, gate outcomes and "
            f"reviews on this connection are attributed to it.{note}"
        )

    def _call_api(self, mid: Any, call: _Call) -> dict[str, Any]:
        """Run a tool's typed `api` and shape its reply, footers last."""
        name, spec, args, agent = call.name, call.spec, call.args, call.agent
        try:
            if call.allow_older is not None:
                self._override_skew(call.allow_older, agent)
            result = self._invoke(spec, args, agent, call.per_call, call.params)
        except Exception as exc:
            # One table with the CLI (`exit_for`, B5f3a650c40). A refusal -- a
            # class declaring exit 3 -- is a result to act on, not a failed call;
            # an undeclared Key/Type/ValueError is a malformed call; anything else
            # is a bug, answered as an internal error below.
            code = exit_for(exc)
            if code == REFUSED:
                return _ok(mid, _text(str(exc), meta={"exit": REFUSED}))
            if declared_exit(exc) is None and isinstance(exc, (KeyError, TypeError, ValueError)):
                return _ok(mid, _text(f"bad arguments: {exc}", error=True))
            if code is None:
                raise
            failed = code not in (OK, NOTHING)  # 2 is "nothing", not an error
            body = f"{type(exc).__name__}: {exc}"  # as the CLI prints it
            return _ok(mid, _text(body, error=failed, meta={"exit": code}))
        # `text` may be a bool or a predicate on the arguments: `render`
        # returns a document with `--show` and a file list without it, and which
        # it is cannot be known until the call.
        # Both may be a value or a predicate on the arguments: `render` returns
        # a document with `--show` and a file list without it, and which it is
        # cannot be known until the call. Resolved together so the two can never
        # disagree -- a text encoding over a tuple payload is a TypeError.
        wants_text = spec.get("text", False)
        payload = spec.get("payload", "")
        if callable(wants_text):
            wants_text = wants_text(args)
        if callable(payload):
            payload = payload(args)
        out = _outcome_result(
            result,
            payload,
            as_text=bool(wants_text),
            bound=_bounds().get(name),
            args=args,
            command=_REGISTRY.command_name(name),
            structured=self._structured(call.modern)
            and "outputSchema" in _result_declaration(name, spec),
        )
        # The footer goes on LAST, after the reason block, so it never comes between
        # a caller and the answer it asked for — `content[0]` is still the body and
        # `jtool`-style consumers are untouched.
        if name == "ddflow_help" and (tiered := tier_note(self.tier)):
            out["content"].append({"type": "text", "text": tiered})
        if call.retired:
            out["content"].append({"type": "text", "text": deprecation_note(name, call.retired)})
        for alias in self._notices.fresh(call.used):
            out["content"].append({"type": "text", "text": f"note: {alias.notice()}"})
        for note in (_obligation_footer(self), _stale_footer(self)):
            if note:
                out["content"].append({"type": "text", "text": note})
        return _ok(mid, out)

    def _m_resources_list(self, msg: dict[str, Any], modern: bool) -> dict[str, Any] | None:
        return _ok(msg.get("id"), {"resources": [r.listing() for r in RESOURCES]})

    def _m_resources_read(self, msg: dict[str, Any], modern: bool) -> dict[str, Any] | None:
        mid = msg.get("id")
        uri = (msg.get("params") or {}).get("uri", "")
        resource = RESOURCE_BY_URI.get(uri)
        if resource is None:
            return _err(mid, -32602, f"unknown resource {uri!r}")
        return _ok(
            mid,
            {
                "contents": [
                    {"uri": uri, "mimeType": resource.mime, "text": resource.read(self.repo)}
                ]
            },
        )

    def _m_prompts_list(self, msg: dict[str, Any], modern: bool) -> dict[str, Any] | None:
        return _ok(
            msg.get("id"),
            {
                "prompts": [
                    {
                        "name": name,
                        "title": title,
                        "description": desc,
                        "arguments": [
                            {
                                "name": arg,
                                "description": f"Optional: narrow the workflow to {arg}.",
                                "required": False,
                            }
                            for arg in args
                        ],
                    }
                    # `all_commands`, not `COMMANDS`: operator-defined `[[macro]]`
                    # blocks are listed beside the shipped workflows, because a mode
                    # that has to be asked for by name is a mode nobody finds.
                    for name, (title, desc, args) in sorted(
                        _surf().prompt_commands(self.repo).items()
                    )
                ]
            },
        )

    def _m_prompts_get(self, msg: dict[str, Any], modern: bool) -> dict[str, Any] | None:
        mid = msg.get("id")
        params = msg.get("params") or {}
        name = params.get("name", "")
        args = params.get("arguments") or {}
        surf = _surf()
        known = surf.prompt_commands(self.repo)
        if name not in known:
            return _err(
                mid,
                -32602,
                f"unknown prompt {name!r}. Known: {', '.join(sorted(known))}"
                + surf.prompt_not_loaded_note(self.repo),
            )
        try:
            # The one rendering `ddflow prompts get` and the `ddflow_prompts` tool share.
            text = surf.render_prompt(name, self.repo, args)
        except (surf.TemplateError, surf.MacroError) as exc:
            return _err(mid, -32602, str(exc))
        return _ok(
            mid,
            {
                "description": known[name][1],
                "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
            },
        )

    #: Method name -> handler: what `_dispatch` looks a message up in.
    _METHODS: ClassVar[dict[str, Callable[..., dict[str, Any] | None]]] = {
        "initialize": _m_initialize,
        "notifications/initialized": _m_silent,
        "notifications/cancelled": _m_silent,
        "ping": _m_ping,
        "tools/list": _m_tools_list,
        "tools/call": _m_tools_call,
        "resources/list": _m_resources_list,
        "resources/read": _m_resources_read,
        "prompts/list": _m_prompts_list,
        "prompts/get": _m_prompts_get,
    }


def _meta_agent(params: dict[str, Any]) -> tuple[str, str]:
    """(the agent a stateless request names in its `_meta`, "" for none; why it cannot
    be used, "" when it can)."""
    meta = params.get("_meta")
    want = meta.get(META_AGENT, "") if isinstance(meta, dict) else ""
    if isinstance(want, str) and (not want.strip() or AN.is_valid(want.strip())):
        return want.strip(), ""
    return "", (f"_meta {META_AGENT!r} = {want!r} is not a usable agent name: {AN.USABLE}")


def _modern_identify_note(agent: str) -> str:
    """What `ddflow_identify` answers on a stateless request: nothing is declared."""
    who = agent or "the tree-derived default"
    return (
        f"not declared: on a {MODERN_PROTOCOLS[0]} request identity is per request, so "
        f"nothing persists for the connection (D-mcp-identity-per-call). Name yourself on "
        f"EVERY call: the `{AS_AGENT}` argument, or {META_AGENT!r} in params._meta "
        f"({AS_AGENT} wins when both are given). A request that names nobody is "
        f"attributed to the tree-derived default. This request: {who!r}."
    )


#: How often a running server looks at the installed package and the log's stamps.
_STALE_CHECK_EVERY_S = 60.0


def _stale_footer(server) -> str:
    """``restart the server`` once, when this process runs older code than the installed package
    or the project's log (`services.upgrade_notice.stale_server_note`), else "".

    A server is a long-lived process: one started before an upgrade keeps answering with the old
    code, and nothing else tells the caller. Checked at most once a minute (the installed version
    is read from disk), said once, and swallows everything -- a courtesy, never a failure."""
    if server._stale_said:
        return ""
    now = time.monotonic()  # not the wall clock: a clock stepped back must not mute the note
    if now - server._stale_checked_at < _STALE_CHECK_EVERY_S:
        return ""
    server._stale_checked_at = now
    try:
        note = _api().stale_server_note(server.repo, agent=server.agent)
    except Exception:
        return ""
    server._stale_said = bool(note)
    return note


def _obligation_footer(server) -> str:
    """The re-instruction footer, or "" — see `services/obligations.py` and `[reinstruct]`.

    Two guards, in this order, because the cheap one comes first: the CADENCE is counters on
    the connection and costs nothing, and only once it opens does this fold the log. Both
    `every_calls` and `every_seconds` must be satisfied, so a burst of calls does not
    produce a burst of footers.

    Swallows everything. A footer is a courtesy on top of an answer the caller asked for,
    and a broken courtesy must never turn a successful tool call into a failure.
    """
    try:
        cfg = Config.load(server.repo)
        if not cfg.reinstruct.enabled:
            return ""
        server._calls_since_footer += 1
        now = time.time()
        if server._calls_since_footer < cfg.reinstruct.every_calls:
            return ""
        if now - server._last_footer_at < cfg.reinstruct.every_seconds:
            return ""
        text = _surf().obligation_footer(server.repo, cfg)
        if not text:
            # Nothing to say. The counters are NOT reset: a quiet project should not have
            # to wait another twelve calls once something does come up.
            return ""
        server._calls_since_footer = 0
        server._last_footer_at = now
        return text
    except Exception:
        return ""


#: Keys a tool's payload carries only when the operation produced them.
_OPTIONAL_KEYS = ("export_refresh", "ci", "progress", "guidance")


_VAR_DEFAULTS: dict[str, Any] = {
    "setup_todo": [],
    "companions": [],
    "missing_companions": [],
    "unregistered_companions": [],
    "uninstalled_companions": [],
    # Seeded here, with every sibling, because the block that computes these sits
    # AFTER the `if not adopted: return v` below -- so an unadopted repository got
    # a variable set the template could not render, and the whole handshake became
    # "ddflow's instruction template could not be loaded". The template guards the
    # use (`{% if adopted %}`), but a guard is only as good as the engine's
    # willingness to short-circuit, and one of the two did not. Defaults do not
    # depend on which branch ran.
    # The rules surface: present, stripped, drifted or gone. Seeded with its siblings
    # so the unadopted path renders — the lesson B153 cost a broken handshake for every
    # first-time user.
    "rules_drift": [],
    "unchecked_companions": [],
    "actionable_companions": [],
    "gate_gaps": [],
    "recoverable": 0,
    "ready": 0,
    "running": 0,
    "blocked": 0,
    "open_bugs": 0,
    "loops": 0,
    "task_pipeline": [],
    # Set by `_instructions` from the connection's tier; seeded here so the template's
    # variable contract holds on every path.
    "tool_tier_note": "",
    "require_outcome": True,
    "importable": 0,
    "queue_is_empty": True,
    "imported_total": 0,
    "imported_no_globs": 0,
    "imported_shipped_drift": 0,
}


def _instruction_vars(repo: Path, agent: str = "") -> dict[str, Any]:
    """Everything `mcp_instructions.md` can render from.

    Gathered defensively: this runs inside the `initialize` handshake, which must
    succeed even in a repository that is broken, half-configured or not adopted at all.
    Every lookup that can fail contributes its own default rather than taking the whole
    handshake down, because a server that refuses to start cannot tell anyone why.

    It also must not WRITE anything to the project — a handshake that adopts the repository
    is the bug this file already fixed once, and `Store` learned the same lesson separately.
    (The one exception is the ready count: `plan_for` reads the waiters' registry, which
    prunes an expired entry under `.ddflow/local/`, git-ignored advisory state that the next
    `next` or `wait` would prune the same way.)
    """
    adopted = (repo / ".ddflow" / "config.toml").is_file()
    v: dict[str, Any] = copy.deepcopy(_VAR_DEFAULTS)
    v["adopted"] = adopted
    # Cheap enough for a handshake: `glob` on a handful of known paths, no parsing.
    # The point is only to know whether to OFFER the import, not to do it.
    try:
        v["importable"] = _surf().importable_count(repo)
    except Exception:
        pass
    if not adopted:
        return v

    try:
        cfg = Config.load(repo)
    except Exception:
        return v
    s = _surf()
    # Each block is independent, and a failure in one must not cost the others: a
    # project with a bad reviewer block should still be told what is ready to work. The
    # rules surface goes first: it is the one fact that decides whether the agent has any
    # project rules at all.
    for fill in (
        s.fill_rules_drift,
        _fill_pipeline,
        s.fill_unit_test_todo,
        s.fill_reviewer_todo,
        s.fill_companions,
        s.fill_queue,
    ):
        try:
            fill(v, repo, cfg, agent)
        except Exception:
            pass
    return v


def _fill_pipeline(v: dict[str, Any], repo: Path, cfg: Config, agent: str) -> None:
    """The gate pipeline the template names, and whether a gate outcome is required."""
    v["task_pipeline"] = list(cfg.gates.task_pipeline)
    v["require_outcome"] = bool(cfg.gates.require_outcome)


def _upgrade_line(repo: Path, agent: str) -> str:
    """What the handshake says about an upgrade, set off by a blank line, or "".

    First the start report (`services.upgrade_start`, `[upgrade].on_start`): ddflow newer than
    the project -> what the start did and the proposal the operator is asked about. Else the
    one-line notice, said once per version on this machine, per project
    (`services.upgrade_notice`). A courtesy that can never fail a connect."""
    notice = ""
    for name in ("upgrade_start", "upgrade_notice"):
        try:  # each on its own: a start report that fails must not silence the notice
            notice = getattr(_api(), name)(repo, agent=agent)
        except Exception:
            continue
        if notice:
            break
    return f"\n\n{notice}" if notice else ""


def _instructions(repo: Path, agent: str = "", tier: str = DEFAULT_TIER) -> str:
    """What the client injects into the model's context on connect.

    **State-aware on purpose.** A fixed blurb describing a workflow the project has not
    adopted is noise the model learns to skip; the useful instruction is the next
    concrete action, and that depends on whether `.ddflow/` exists, whether a test
    command is set, whether a reviewer and the companion tools are configured, and
    whether anything is waiting to be recovered. This is the only place the server gets
    to speak unprompted, so it says the one thing that is true right now.

    **And it is a TEMPLATE, not a string literal.** Everything it says — the workflow,
    the reporting duties, which companion tools to reach for — is
    `templates/prompts/mcp_instructions.md`, overridable per project
    (`.ddflow/prompts/mcp_instructions.md`) or per config (`[prompts]
    mcp_instructions`). That is the difference between a tool whose behaviour you
    configure and one you have to fork.
    """
    vars_ = _instruction_vars(repo, agent)
    vars_["tool_tier_note"] = tier_note(tier, names=False)
    overrides: dict[str, str] = {}
    if vars_["adopted"]:
        try:
            path = _surf().instructions_override(repo)
            if path:
                overrides["mcp_instructions"] = path
        except Exception:
            pass
    try:
        text = _surf().render_instructions(repo, overrides, vars_)
    except _surf().TemplateError as exc:
        # A broken override must not silence the server: say what is wrong, in the one
        # place the operator will see it, and still hand over the essentials.
        text = (
            f"ddflow's instruction template could not be loaded: {exc}\n\n"
            "Call `ddflow_brief` for the state of the queue, and `ddflow_prompts` to "
            "inspect the template configuration."
        )
    # After the try, not in it: a broken template is exactly when the upgrade may be why.
    return text + _upgrade_line(repo, agent)


def _resolve_call(
    name: str, args: dict[str, Any]
) -> tuple[str, dict[str, Any] | None, dict[str, Any], list[_REGISTRY.Alias], str]:
    """``(tool name, its spec, args, aliases used, a clash)`` for a ``tools/call``.

    An old tool name and old argument names still work (D-compat): the call is the current
    tool's, with the arguments under their current names. ``spec`` is None for a name that
    is neither a tool nor an alias; ``clash`` is set when an argument was given under both
    its current and its old name.
    """
    used: list[_REGISTRY.Alias] = []
    spec = TOOLS.get(name)
    if spec is None:
        canonical, alias = _REGISTRY.resolve_tool(TOOLS, name)
        if alias is None:
            return name, None, args, used, ""
        name, spec = canonical, TOOLS[canonical]
        used.append(alias)
    args, used_args, clash = _REGISTRY.rename_args(spec, args)
    return name, spec, args, used + used_args, clash


def deprecation_note(tool: str, retired: dict[str, str]) -> str:
    """The one line a call answers with when it passed arguments a release retired: they
    were ignored, and why, so the caller can stop sending them."""
    named = "; ".join(f"`{n}` ({why})" for n, why in sorted(retired.items()))
    return (
        f"note: {tool} argument(s) {named} are deprecated and ignored (kept so callers of an "
        f"older ddflow keep working until 1.0); stop sending them."
    )


def _text(body: str, *, error: bool = False, meta: dict | None = None) -> dict[str, Any]:
    """One tool result.

    An error carries `_meta.exit = 1` as well as `isError`, so the exit-code vocabulary
    the whole package speaks — 0 healthy · 1 failure · 2 nothing · 3 refused — holds at
    the protocol boundary too. Without it, a schema-level rejection (a missing required
    argument, an unknown one) arrived as exit 0, and an agent reading only the exit code
    could not tell a refused call from a successful one.
    """
    res: dict[str, Any] = {"content": [{"type": "text", "text": body}], "isError": error}
    if meta:
        res["_meta"] = meta
    elif error:
        res["_meta"] = {"exit": 1}
    return res


#: Tools that can run for minutes -- a gate command, a reviewer, a wait. A real stdio
#: server runs each in a worker PROCESS so the loop keeps answering: a 534 s unit_tests
#: run once timed out every other call (B5f209cb092); a reviewer that crashes or eats
#: the machine takes its worker down, not the server and the session's tools with it
#: (B55e649ca6e); and the worker sits in its own session, so a client that times out or
#: disconnects does not lose the run -- it finishes and records its outcome (B9abc247444).
#: A process, not a thread: the event log's caches are process-global and unlocked.
OFFLOADED = frozenset(
    {
        "ddflow_bisect",
        "ddflow_ci",
        "ddflow_gate_run",
        "ddflow_gate_verify",
        "ddflow_review",
        "ddflow_verify",
        "ddflow_wait",
    }
)

#: Workers running at once ON ONE CONNECTION. A burst beyond it is answered "busy"
#: (exit 2) rather than started: each is a whole interpreter. Per connection, not per
#: machine: what it bounds is one client's burst.
MAX_WORKERS = 4


def _offloaded(msg: dict[str, Any]) -> bool:
    params = msg.get("params")
    return (
        msg.get("method") == "tools/call"
        and msg.get("id") is not None
        and isinstance(params, dict)
        and isinstance(params.get("name"), str)  # a list name must not end the loop
        and params["name"] in OFFLOADED
    )


def _worker_stderr(repo: Path):
    """Where a worker's diagnostics go: a log of its own, never the client's stderr pipe.

    Inherited, a client that disconnected left the worker writing to a broken pipe, and
    a gate or reviewer child got SIGPIPE before the run could record its outcome."""
    local = repo / ".ddflow" / "local"
    log = local / "mcp-workers.log"
    try:
        if (repo / ".ddflow").is_dir():
            local.mkdir(exist_ok=True)
            # Kept small: overwritten once past a megabyte rather than rotated.
            big = log.exists() and log.stat().st_size > 1 << 20
            return open(log, "w" if big else "a")  # the worker owns it
    except OSError as exc:
        print(f"ddflow mcp: worker log {log} unavailable ({exc})", file=sys.stderr)
    return PROC.DEVNULL


def _offload(srv: Server, msg: dict[str, Any], send: Callable[[dict[str, Any]], None], busy: int):
    """Run one call in a worker process; a thread relays its frames. Returns the thread.

    The worker is given the connection's identity and location -- the only per-connection
    state a tool reads (`client_info` is a label, the footer counters a courtesy). A
    `notifications/cancelled` is deliberately NOT passed on: a review or gate a client
    stopped waiting for still finishes and records, which is the point (B9abc247444).
    """
    mid = msg.get("id")
    name = (msg.get("params") or {}).get("name", "")
    if busy >= MAX_WORKERS:
        send(
            _ok(
                mid,
                _text(
                    f"{name}: {busy} long calls are already running on this connection "
                    f"(at most {MAX_WORKERS}). Call again when one has answered.",
                    meta={"exit": 2},
                ),
            )
        )
        return None
    job = {
        "repo": str(srv.repo),
        "called_from": str(srv.called_from),
        "agent": srv.agent,
        "msg": msg,
        "protocol": srv.protocol,
        # A `requestState` the worker issues must verify on the retry, which another
        # worker -- or this process -- serves (`mcp_protocol.state_key`).
        "state_key": protocol.state_key().hex(),
    }
    # The worker must import THIS code, not whatever ddflow the path would find first.
    root = str(Path(__file__).resolve().parents[2])
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(filter(None, [root, os.environ.get("PYTHONPATH", "")])),
    }
    err = _worker_stderr(srv.repo)
    try:
        p = PROC.popen(
            [sys.executable, "-c", "from ddflow.surfaces.mcp import _worker_main as m; m()"],
            stdin=PROC.PIPE,
            stdout=PROC.PIPE,
            stderr=err,
            text=True,
            env=env,
            start_new_session=True,
        )
        assert p.stdin is not None and p.stdout is not None
        p.stdin.write(json.dumps(job))
        p.stdin.close()
    except (OSError, ValueError) as exc:
        send(_ok(mid, _text(f"{name}: could not start its worker: {exc}", error=True)))
        return None
    finally:
        if err is not PROC.DEVNULL:
            err.close()  # the worker holds its own copy

    def relay() -> None:
        answered = False
        for raw in p.stdout:
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                print(raw, end="", file=sys.stderr)
                continue
            answered = answered or ("method" not in frame and frame.get("id") == mid)
            try:
                send(frame)
            except Exception:  # the client is gone; keep draining so the worker never blocks
                pass
        rc = p.wait()
        if not answered:
            try:
                send(
                    _ok(
                        mid,
                        _text(
                            f"{name}'s worker process ended (exit {rc}) without an answer. "
                            "This server is still up. What the run recorded is in the log: "
                            "check ddflow_gate_status.",
                            error=True,
                        ),
                    )
                )
            except Exception:
                pass

    t = threading.Thread(target=relay, name=f"ddflow-{name}-{mid}", daemon=True)
    t.start()
    return t


def _worker_main() -> None:
    """A worker process: one offloaded call, read as a job from stdin (see `_offload`).

    Protocol frames go to a private copy of stdout, and fd 1 is pointed at stderr, so a
    stray print or a child's output can never corrupt them. A parent that died is no
    reason to stop: writes to it are dropped and the run finishes and records.
    """
    job = json.loads(sys.stdin.read())
    out = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    def write(frame: dict[str, Any]) -> None:
        try:
            out.write(json.dumps(frame) + "\n")
            out.flush()
        except (OSError, ValueError):
            pass

    msg = job["msg"]
    if job.get("state_key"):
        protocol.state_key(bytes.fromhex(job["state_key"]))
    srv = Server(Path(job["repo"]), job.get("agent", ""), called_from=Path(job["called_from"]))
    srv.protocol = job.get("protocol", srv.protocol)  # what the client negotiated, not the default
    srv.notify = write
    try:
        reply = srv.handle(msg)
    except BaseException as exc:  # the caller is owed an answer either way
        print(traceback.format_exc(), file=sys.stderr)
        reply = _err(msg.get("id"), -32603, f"internal error: {exc}")
    if reply is not None:
        write(reply)
    try:
        out.close()
    except OSError:
        pass


def serve(
    repo: Path,
    stdin=None,
    stdout=None,
    *,
    called_from: Path | None = None,
    offload: bool | None = None,
) -> None:
    """Newline-delimited JSON-RPC over stdio, until EOF.

    Nothing may be written to stdout except protocol frames — a stray print corrupts
    the stream and the client sees a hung server. Diagnostics go to stderr.

    `offload` runs the `OFFLOADED` tools in worker processes; by default only a real
    stdio session does, and a caller handing in its own streams gets the plain loop.
    At EOF the loop waits for its workers' answers before returning.
    """
    srv = Server(repo, called_from=called_from)
    inp = stdin or sys.stdin
    outp = stdout or sys.stdout
    if offload is None:
        offload = stdin is None

    out_lock = threading.Lock()

    def notify(frame: dict[str, Any]) -> None:
        with out_lock:
            outp.write(json.dumps(frame) + "\n")
            outp.flush()

    srv.notify = notify
    relays = []
    for raw in inp:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            notify(_err(None, -32700, f"parse error: {exc}"))
            continue
        if offload and _offloaded(msg):
            era = _modern_check(msg)
            if isinstance(era, dict):  # refused before a worker is spent on it
                notify(era)
                continue
            # The worker's answer is already modern (it runs `handle`); this also shapes
            # the replies `_offload` makes itself -- busy, failed to start, no answer.
            send = notify if era is None else lambda f: notify(_modernize("tools/call", f))
            relays = [r for r in relays if r.is_alive()]
            if (relay := _offload(srv, msg, send, len(relays))) is not None:
                relays.append(relay)
            continue
        try:
            reply = srv.handle(msg)
        except Exception as exc:
            print(traceback.format_exc(), file=sys.stderr)
            reply = _err(msg.get("id"), -32603, f"internal error: {exc}")
        if reply is not None:
            notify(reply)
    for relay in relays:
        relay.join()


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point: ``ddflow-mcp [--repo PATH]``.

    Kept argument-light on purpose. An MCP client spawns this with no arguments and a
    working directory, so the default path -- resolve the repository from the cwd, and
    from there to the PRIMARY checkout even if the cwd is a linked worktree -- has to
    be the one that needs no configuration. ``DDFLOW_REPO`` overrides for clients that
    spawn servers from a fixed directory.
    """

    ap = argparse.ArgumentParser(
        prog="ddflow-mcp",
        description="ddflow MCP stdio server. Add to your agent's MCP config as:\n"
        '  {"mcpServers": {"ddflow": {"command": "uvx", '
        '"args": ["ddflow-mcp"]}}}',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--repo",
        default=os.environ.get("DDFLOW_REPO", ""),
        help="repository root (default: cwd, resolved to the primary checkout)",
    )
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args(argv)
    if args.version:
        print(SERVER_INFO["version"])
        return 0

    start = Path(args.repo) if args.repo else Path.cwd()
    try:
        repo = repo_root(start)
    except Exception:
        # Not a git repository, or git is absent. Serve anyway: `ddflow_doctor` will
        # say so in a way the model can read and relay, which is far more useful than
        # a server that refuses to start and shows the client only "exited 1".
        repo = start.resolve()
    # `start` is passed ON, not discarded. It is the caller's ACTUAL location -- the
    # worktree an agent harness spawned this server in -- and `repo` is deliberately the
    # primary so every worktree shares one event log. Resolving and forgetting meant
    # `claim` over MCP never saw that the caller was already isolated, so worktree
    # ADOPTION was unreachable from the one surface the harnesses it was written for
    # actually drive.
    serve(repo, called_from=start)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
