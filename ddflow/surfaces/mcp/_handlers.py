"""The `METHODS` dispatch half of `ddflow.surfaces.mcp.Server` (`_Handlers`).

Every JSON-RPC method handler (`tools/list`, `tools/call`, `resources/*`, `prompts/*`,
`ping`), the argument checking, identity declaration and the answer assembly
(`_call_api`). `Server` inherits it, exactly as `infra.log.EventLog` inherits its
`_GuardMixin` and `_ReadMixin` halves.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from ddflow.core import agentname as AN
from ddflow.core.outcome import NOTHING, OK, REFUSED, declared_exit, exit_for

from ...api.harness import HR
from ...infra import harness_identity
from .. import mcp_next as _NEXT
from .. import mcp_protocol as protocol
from .. import mcp_upgrade as _upgrade
from .. import registry as _REGISTRY
from ..mcp_heartbeat import heartbeat
from ..mcp_protocol import err as _err
from ..mcp_protocol import ok as _ok
from ..tools import TOOLS
from ..tools import family_schema as _FAMILIES
from ..tools import profiles as _PROFILES
from ..tools._common import _api
from ..tools.aliases import alias_for as _alias_for
from ..tools.tiers import tier_note, tier_tools
from ._core import (
    _FIRST_CALL_TRIES,
    _STALE_CHECK_EVERY_S,
    ALLOW_OLDER,
    OFFLOADED,
    RESOURCE_BY_URI,
    RESOURCES,
    _default_agent,
    _modern_identify_note,
    _progress_token,
    _surf,
)
from ._instructions import _obligation_footer
from ._reply import (
    _bounds,
    _finish_list_read,
    _outcome_result,
    _properties,
    _resolve_call,
    _result_declaration,
    _schema,
    _text,
    deprecation_note,
)


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


class _Handlers:
    """The JSON-RPC method handlers `_METHODS` dispatches to; `Server` inherits them."""

    def _m_initialize(self, msg: dict[str, Any], modern: bool) -> dict[str, Any] | None:
        params = msg.get("params") or {}
        want = params.get("protocolVersion", "")
        self.protocol = protocol.legacy_version(want)
        ci = params.get("clientInfo")
        self.client_info = dict(ci) if isinstance(ci, dict) else {}
        if self._tier_auto:
            budget, limits = _surf().profile_inputs(self.repo, self.client_info)
            self.tier = _PROFILES.settle(
                _PROFILES.AUTO, str(self.client_info.get("name", "")), budget=budget, limits=limits
            )
        return _ok(
            msg.get("id"),
            protocol.initialize_result(self.protocol, self._handshake_text()),
        )

    def _m_silent(self, msg: dict[str, Any], modern: bool) -> dict[str, Any] | None:
        """`notifications/initialized` and `notifications/cancelled`: nothing is answered."""
        return None

    def _m_ping(self, msg: dict[str, Any], modern: bool) -> dict[str, Any] | None:
        return _ok(msg.get("id"), {})

    def _m_tools_list(self, msg: dict[str, Any], modern: bool) -> dict[str, Any] | None:
        advertised = tier_tools(self.tier)
        tools = [
            {
                "name": n,
                "description": s["description"],
                "inputSchema": _schema(s),
                **(_result_declaration(n, s) if self._structured(modern) else {}),
            }
            for n, s in sorted(TOOLS.items())
            if n in advertised
        ]
        if self.tier in _PROFILES.PROFILES:  # the family tools are not in `TOOLS`
            tools = sorted((*tools, *_PROFILES.family_entries(self.tier)), key=lambda t: t["name"])
        return _ok(msg.get("id"), {"tools": tools})

    def _resolve(self, asked: str, args: dict[str, Any]):
        """``(routed, name, spec, args, used, clash)`` for a call to ``asked``.

        A family call (`ddflow_gate action=record`) is its member's call: routed here, so
        everything after sees the member; `route` has already refused a missing action, a
        missing required argument or an unknown one, each naming the action (MTD-family-route)
        -- ``routed`` is then that refusal, a string. An old tool name the profile does not list
        is said once per session to be the family action that replaces it (D-compat;
        MTD-aliases)."""
        routed = _FAMILIES.route(asked, args)
        if isinstance(routed, str):
            return routed, asked, None, args, [], ""
        name, spec, args, used, clash = _resolve_call(
            *((routed.tool, routed.args) if routed else (asked, args))
        )
        listed = _PROFILES.direct_tools(self.tier) if self.tier in _PROFILES.PROFILES else None
        if spec is not None and not routed and listed is not None and name not in listed:
            if alias := _alias_for(name):
                used = [*used, alias]
        return routed, name, spec, args, used, clash

    def _m_tools_call(self, msg: dict[str, Any], modern: bool) -> dict[str, Any] | None:
        """`tools/call`: check the call (`_check_call`), then identify or run the tool."""
        mid = msg.get("id")
        params = msg.get("params") or {}
        args = params.get("arguments") or {}
        allow_older: Any = None
        if ALLOW_OLDER in args:
            args = dict(args)
            allow_older = args.pop(ALLOW_OLDER)
        routed, name, spec, args, used, clash = self._resolve(params.get("name", ""), args)
        if isinstance(routed, str):
            return _ok(mid, _text(routed, error=True))
        # A routed call was checked by `route` (one rule, on the family's names); only an argument
        # given under both its current and an old spelling is left to say here.
        problem = (
            (f"{routed.display}: bad arguments: {clash}" if clash else "")
            if routed
            else self._check_call(name, spec, args, clash)
        )
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
        # A per-call name (`as_agent`, or a `_meta` agent) is the caller's real identity;
        # its shell is a different process and derives the tree name, which split the
        # review's attribution from the reviewer (B-ddflow-review-wrong-agent-name).
        # Recorded for the harness, exactly as a legacy `ddflow_identify` is (Bfad021e8d9),
        # so the same agent's shell commands — a shelled-out `ddflow review` above all —
        # take the same name instead of a derived one.
        if per_call:
            harness_identity.declare(self.repo, per_call, scope=HR.session_fingerprint(os.environ))
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

    def _first_call_note(self, tool: str, agent: str) -> str:
        """The session start, once, on the first call of this connection (api.firstcall).
        A start that could not be built is tried again on the next call, at most
        `_FIRST_CALL_TRIES` times, so a transient failure does not cost the connection it."""
        if self._first_call_done:
            return ""
        note = _api().first_call_preamble(self.repo, agent, tool, self.client_info)
        self._first_call_tries += 1
        if note is not None or self._first_call_tries >= _FIRST_CALL_TRIES:
            self._first_call_done = True
        return note or ""

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
        missing = [
            n for n, (_t, _d, req) in spec["properties"].items() if req and args.get(n) is None
        ]
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

        shell = harness_identity.declare(self.repo, want, scope=HR.session_fingerprint(os.environ))
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
            with heartbeat(
                self.repo, name, self._progress(call.params) if name in OFFLOADED else None
            ):
                result = self._invoke(spec, args, agent, call.per_call, call.params)
                if name == "ddflow_list":
                    result = _finish_list_read(self.repo, result, args)
        except Exception as exc:
            # One table with the CLI (`exit_for`, B5f3a650c40). A refusal -- a
            # class declaring exit 3 -- is a result to act on, not a failed call;
            # an undeclared Key/Type/ValueError is a malformed call; anything else
            # is a bug, answered as an internal error below.
            code = exit_for(exc)
            if code == REFUSED:
                if (stale := _upgrade.refusal(self, exc)) is not None:
                    return _ok(mid, stale)
                return _ok(mid, _text(str(exc), meta={"exit": REFUSED}))
            if declared_exit(exc) is None and isinstance(exc, (KeyError, TypeError, ValueError)):
                return _ok(mid, _text(f"bad arguments: {exc}", error=True))
            if code is None:
                raise
            failed = code not in (OK, NOTHING)  # 2 is "nothing", not an error
            body = f"{type(exc).__name__}: {exc}"  # as the CLI prints it
            return _ok(mid, _text(body, error=failed, meta={"exit": code}))
        finally:
            self._says.pop(_progress_token(call.params), None)
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
            hint=(
                (lambda body: _NEXT.hints(name, args, body, self.tier, limit=self._next_limit))
                if self._next_limit
                else None
            ),
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
        for note in (
            self._first_call_note(name, agent),
            self._deferred_notice(),
            _obligation_footer(self),
            _upgrade.footer(self, _STALE_CHECK_EVERY_S),
        ):
            if note:
                out["content"].append({"type": "text", "text": note})
        return _ok(mid, out)

    def _m_resources_list(self, msg: dict[str, Any], modern: bool) -> dict[str, Any] | None:
        declared = _api().record_resource_listing()  # the kinds that declare a resource
        return _ok(msg.get("id"), {"resources": [r.listing() for r in RESOURCES] + declared})

    def _m_resources_read(self, msg: dict[str, Any], modern: bool) -> dict[str, Any] | None:
        mid = msg.get("id")
        uri = (msg.get("params") or {}).get("uri", "")
        resource = RESOURCE_BY_URI.get(uri)
        if resource is None:
            text = _api().record_resource_read(self.repo, uri)
            if text is None:
                return _err(mid, -32602, f"unknown resource {uri!r}")
            return _ok(mid, {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": text}]})
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
