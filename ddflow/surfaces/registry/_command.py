"""`CliPolicy` and `Command`: how one command prints and how the operation is declared.

`Command` names an operation once -- its CLI path, MCP tool, parameters and the shape both
surfaces print. `CliPolicy` says how the CLI executor differs from its default for one
command.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ...config_sections._compat import MIN_REMOVED_IN
from ._aliases import Alias, _check_aliases, add_command_alias
from ._param import AS_AGENT, AS_AGENT_SPEC, Param, params_schema

#: The longest CLI path: `group command`.
GROUP_DEPTH = 2
#: `via` is a tool, optionally with the selector value.
MAX_VIA = 2


@dataclass(frozen=True)
class CliPolicy:
    """How the CLI executor (`cliexec.run`) differs from its default for one command.

    A command with no policy prints as the rule verbs do: the caller is the resolved
    identity, a refusal under ``--json`` is the body with the MCP tool's lead, and the human
    reads ``render``. The work-loop verbs keep what they always did, and a command with a
    policy prints as these fields say (``typed_agent`` is on unless the policy turns it off):

    * ``typed_agent``: the ``agent`` given to ``call`` is the ``--agent`` the caller typed
      (``""`` for none), not the resolved id: passing the resolved one makes a person at
      a terminal look like an agent that said so, which `agent_marker` reads;
    * ``shown``: the exit codes whose result is printed (stdout, or the ``--json`` body), or
      a predicate on the Outcome; any other exit prints only ``out.reason`` on stderr (or
      what ``reason`` makes of it). ``None``: every exit;
    * ``notes(out, args, ctx)``: lines for stderr, printed first whatever the exit and
      the mode (a caveat that on stdout would corrupt ``--json``);
    * ``body_always``: under ``--json`` the body is printed whatever the exit, a refusal's
      reason follows on stderr, and ``notes`` are not printed (the flow commands' contract);
    * ``body(out, args)``: the ``--json`` body, when the CLI's has never been the MCP
      tool's ``payload``;
    * ``human(out, args, ctx)``: the human text when it needs the context (the identity,
      the tree the caller stands in) that ``render(out, args)`` is not given; ``None``
      prints nothing;
    * ``call_kwargs(ctx)``: more keyword arguments for ``call``, which only the CLI knows (the
      directory the caller's shell stands in).
    """

    typed_agent: bool = True
    shown: tuple[int, ...] | Callable[[Any], bool] | None = None
    reason: Callable[[Any], str] | None = None
    body_always: bool = False
    notes: Callable[..., Iterable[str]] | None = None
    body: Callable[..., Any] | None = None
    human: Callable[..., str] | None = None
    call_kwargs: Callable[..., Mapping[str, Any]] | None = None


@dataclass(frozen=True)
class Command:
    """One operation, declared once.

    ``path`` is the CLI words (``("gate", "record")``); ``tool`` the MCP tool name (empty:
    not exposed, with ``reason`` saying why -- the parity test's exemption as a field).
    ``call(repo, args, agent)`` returns an `Outcome`; ``payload`` is the field tuple both
    surfaces print; ``prose`` says the MCP body is text, not JSON; ``tier`` is the MCP
    tool tier (`tools/tiers.py` reads it for every tool outside its legacy sets),
    ``help_group`` the `ddflow_help` inventory heading when no legacy prefix claims the tool,
    and ``render`` the optional human renderer. `tool_entry` carries the group (for
    `services/help.py`) but not the tier: the engine reads tiers from the tier table.
    """

    path: tuple[str, ...]
    summary: str = ""
    description: str = ""
    params: tuple[Param, ...] = ()
    tool: str = ""
    call: Callable[..., Any] | None = None
    payload: str | tuple[str, ...] = ""
    #: True, or a predicate over the call's arguments (``render``: text only with ``show``).
    prose: bool | Callable[..., bool] = False
    #: The MCP tool tier (``core`` | ``standard`` | ``all``) of a tool NOT in the legacy sets of
    #: `tools/tiers.py`; "" there is a registration error, so a tool cannot land unplaced.
    tier: str = ""
    render: Callable[..., Any] | None = None
    #: The `ddflow_help` inventory heading for this tool (see `services/help.py`).
    help_group: str = ""
    #: ``(argv, arguments)``: the CLI call whose `--json` body this tool must reproduce, for
    #: `tests/test_api_layer_wire.py`'s wire-shape check (table in `tests/api_layer_support.py`) (the same row `MIGRATED_WIRE_SHAPES` holds).
    wire_shape: tuple[list[str], dict[str, Any]] | tuple[()] = ()
    #: How the CLI executor differs from its default for this command (`CliPolicy`).
    cli: CliPolicy | None = None
    handler: Callable[..., Any] | None = None
    kind: str = ""
    identify: bool = False
    #: A server-lifecycle tool (`restart` | `kill`): the engine applies it to the running
    #: connection after the call answers (`mcp_lifecycle.arm`), which an ordinary `call`
    #: cannot do (its signature has no server to re-exec or end).
    lifecycle: str = ""
    deprecated: Mapping[str, Any] = field(default_factory=dict)
    #: Why this command has no MCP tool of its own (the parity exemption), or "".
    reason: str = ""
    #: The tool that serves this command when it has none of its own, and the value of the
    #: argument that selects it: ``("ddflow_list", "task")``; a one-element form names a
    #: tool that covers it whole (``init`` -> ``("ddflow_setup",)``).
    via: tuple[str, ...] = ()
    #: CLI flags this command's tool omits on purpose: ``{"--force": reason}``.
    flag_exempt: Mapping[str, str] = field(default_factory=dict)
    #: Why the body is text and not JSON (required of every ``prose`` tool).
    prose_reason: str = ""
    #: Earlier names that still work (D-compat): ``aliases`` are old spellings of the LAST
    #: word of ``path`` (a renamed group is `add_commands`' ``group_aliases``), ``tool_aliases``
    #: old MCP tool names. Both are hidden from ``--help`` and ``tools/list``, always
    #: callable, and need ``deprecated_since``.
    aliases: tuple[str, ...] = ()
    tool_aliases: tuple[str, ...] = ()
    deprecated_since: str = ""
    removed_in: str = MIN_REMOVED_IN
    #: What an old name should be replaced by when that is not simply this command.
    replacement: str = ""
    #: The order ``tools/list`` has always served the properties in, when it is not the order
    #: of ``params`` (which is the CLI's, and so the order of ``--help``). Empty: the same.
    tool_order: tuple[str, ...] = ()
    #: Attributes the CLI parser sets on every parse of this command, beside the handler.
    defaults: Mapping[str, Any] = field(default_factory=dict)
    #: The tool is told where the caller stands (``called_from``), for a tool that creates
    #: or lands a worktree (the engine reads ``wants_called_from`` from the table entry).
    wants_called_from: bool = False
    #: The tool reports progress while it runs (``on_progress``), for a call that takes minutes.
    wants_progress: bool = False
    #: The tool may act only as the CONNECTION's own identity: over MCP the per-call
    #: ``as_agent`` is a claim the surface cannot authenticate, so a call naming a
    #: DIFFERENT agent than the connection is refused by the engine before this tool runs
    #: (B6335d3482d). The `ddflow_session` ``asks``/``withdraw`` verbs bound "my own asks"
    #: to it; ``ask`` files a request under the identity it is given, which is attribution,
    #: not a read of another agent's record.
    own_identity: bool = False
    #: argparse's ``formatter_class`` and ``epilog`` for this command's help.
    formatter_class: Any = None
    epilog: str = ""
    #: The family tool that serves this command and its ``action`` there (D-mcp-tool-families,
    #: `tools/families.py`): the CLI's ``noun verb``. Empty: the legacy placement table decides.
    family: str = ""
    action: str = ""

    def __post_init__(self) -> None:
        self._check_names()
        self._check_params()
        if not self.path and not self.tool:
            raise ValueError("a command needs a CLI path or an MCP tool name")
        if len(self.via) > MAX_VIA:
            raise ValueError(f"{'/'.join(self.path)}: via is (tool,) or (tool, selector)")

    def _check_names(self) -> None:
        """The old names this command answers to are well formed."""
        _check_aliases(
            f"{'/'.join(self.path) or self.tool}",
            self.aliases,
            self.path[-1] if self.path else "",
            self.deprecated_since,
            self.removed_in,
        )
        _check_aliases(
            f"tool {self.tool}",
            self.tool_aliases,
            self.tool,
            self.deprecated_since,
            self.removed_in,
        )
        if self.aliases and not self.path:
            raise ValueError(f"{self.tool}: CLI aliases need a CLI path")
        if self.tool_aliases and not self.tool:
            raise ValueError(f"{'/'.join(self.path)}: tool aliases need a tool")

    def _check_params(self) -> None:
        """The parameters are distinct, and ``tool_order`` names each tool one once."""
        names = [p.name for p in self.params]
        if len(set(names)) != len(names):
            raise ValueError(f"{'/'.join(self.path)}: duplicate parameter names")
        spelled = names + [a for p in self.params for a in p.aliases]
        if len(set(spelled)) != len(spelled):
            raise ValueError(
                f"{'/'.join(self.path) or self.tool}: a parameter alias is also another "
                "parameter's name or alias"
            )
        tool_names = sorted(p.name for p in self.params if not p.cli_only)
        if self.tool_order and sorted(self.tool_order) != tool_names:
            raise ValueError(
                f"{self.tool or '/'.join(self.path)}: tool_order must name each tool parameter once"
            )

    @property
    def mcp_params(self) -> tuple[Param, ...]:
        own = tuple(p for p in self.params if not p.cli_only)
        if not self.tool_order:
            return own
        by_name = {p.name: p for p in own}
        return tuple(by_name[n] for n in self.tool_order if n in by_name)

    @property
    def cli_params(self) -> tuple[Param, ...]:
        return tuple(p for p in self.params if not p.mcp_only)

    def properties(self) -> dict[str, tuple[str, str, bool]]:
        """The tool-table ``properties`` (without ``as_agent``, which the engine adds)."""
        return {p.name: p.spec() for p in self.mcp_params}

    def input_schema(self) -> dict[str, Any]:
        """The MCP ``inputSchema``, ``as_agent`` included for every tool but identify."""
        params = self.mcp_params
        if not self.identify:
            t, d, req = AS_AGENT_SPEC
            params = (*params, Param(AS_AGENT, type=t, help=d, required=req))
        return params_schema(params, deprecated=self.deprecated)

    def tool_listing(self) -> dict[str, Any]:
        """One entry of the ``tools/list`` result."""
        return {
            "name": self.tool,
            "description": self.description,
            "inputSchema": self.input_schema(),
        }

    def tool_entry(self) -> dict[str, Any]:
        """The entry the MCP engine's ``TOOLS`` table holds for this command."""
        entry: dict[str, Any] = {
            "description": self.description,
            "properties": self.properties(),
            "api": self.call,
            "payload": self.payload,
        }
        if self.prose:
            entry["text"] = self.prose if callable(self.prose) else True
        if self.kind:
            entry["kind"] = self.kind
        if self.identify:
            entry["identify"] = True
        if self.lifecycle:
            entry["lifecycle"] = self.lifecycle
        if self.help_group:
            entry["help_group"] = self.help_group
        if self.wants_called_from:
            entry["wants_called_from"] = True
        if self.wants_progress:
            entry["wants_progress"] = True
        if self.own_identity:
            entry["own_identity"] = True
        if self.deprecated:
            entry["deprecated"] = dict(self.deprecated)
        if aliases := self.tool_alias_list():
            entry["aliases"] = aliases
        if arg_aliases := {a: al for p in self.mcp_params for a, al in p.arg_aliases().items()}:
            entry["arg_aliases"] = arg_aliases
        return entry

    def tool_alias_list(self) -> tuple[Alias, ...]:
        """The old names of this command's MCP tool, as `Alias`es."""
        return tuple(
            Alias("tool", a, self.replacement or self.tool, self.deprecated_since, self.removed_in)
            for a in self.tool_aliases
        )

    def add_to(self, subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
        """Add this command's subparser to ``subparsers`` (the parent group's)."""
        kwargs: dict[str, Any] = {}
        if self.summary:
            kwargs["help"] = self.summary
        if self.formatter_class is not None:
            kwargs["formatter_class"] = self.formatter_class
        if self.epilog:
            kwargs["epilog"] = self.epilog
        sub = subparsers.add_parser(self.path[-1], **kwargs)
        required_groups = {p.exclusive for p in self.cli_params if p.exclusive_required}
        groups: dict[str, Any] = {
            name: sub.add_mutually_exclusive_group(required=True)
            for name in sorted(n for n in required_groups if n)
        }
        for p in self.cli_params:
            p.add_to(sub, groups)
        if self.handler is not None:
            sub.set_defaults(fn=self.handler)
        if self.defaults:
            sub.set_defaults(**self.defaults)
        for word in self.aliases:
            old = " ".join((*self.path[:-1], word))
            add_command_alias(
                subparsers,
                sub,
                word,
                Alias(
                    "command",
                    old,
                    self.replacement or " ".join(self.path),
                    self.deprecated_since,
                    self.removed_in,
                ),
            )
        return sub
