"""CLI command words that are not the name of the tool that serves them.

`command_for_path` names one result alike on both surfaces: a CLI command's schema name
is the MCP tool that serves it.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping

from ._schemas import command_name

#: CLI commands whose words are not the name of the tool that serves them and which no
#: `via` declares: ``ddflow research`` (no verb) files what ``ddflow_research_add`` files.
CLI_COMMAND_NAMES: dict[tuple[str, ...], str] = {
    ("research",): "research_add",
    ("companions",): "companions",
    ("companions", "list"): "companions",
    ("hooks",): "hooks",
    ("hooks", "status"): "hooks",
    ("hooks", "install"): "hooks",
    ("hooks", "uninstall"): "hooks",
    ("prompts",): "prompts",
    ("prompts", "list"): "prompts",
    ("prompts", "get"): "prompts",
    ("prompts", "show"): "prompts",
}
#: A flag that makes a command another tool's: ``(path, parsed attribute) -> name``. Read from
#: what argparse parsed (an abbreviation, ``--verif``, sets the same attribute), not from the
#: words typed.
CLI_FLAG_COMMANDS: dict[tuple[tuple[str, ...], str], str] = {
    (("companions",), "verify"): "companions_verify",
    (("companions", "list"), "verify"): "companions_verify",
    (("import",), "verify"): "import_verify",
    (("doctor",), "upgrade"): "upgrade",
}
#: A verb (a positional word inside one noun, not a subcommand) that makes the command
#: another tool's: ``(path, verb) -> name``. `research` is the one noun whose verbs are
#: not subcommands, so its schema must follow the verb (``research list`` reads like
#: ``task list`` -> ``ddflow_list``; ``research show`` like ``show`` -> ``ddflow_show``),
#: not the verb-less ``research_add`` (B33b1871f7b).
CLI_VERB_COMMANDS: dict[tuple[tuple[str, ...], str], str] = {
    (("research",), "list"): "list",
    (("research",), "show"): "show",
}


def command_for_path(
    path: tuple[str, ...],
    routed: Mapping[tuple[str, ...], tuple[str, str]],
    covering: Mapping[str, tuple[str, ...]],
    args: argparse.Namespace | None = None,
) -> str:
    """The schema name of a CLI command: the name of the MCP tool that serves it, so the two
    surfaces tag one result alike. ``routed`` maps a path a selector serves to its tool
    (``task list`` -> ``ddflow_list``), ``covering`` a one-word command a differently named
    tool covers (``init`` -> ``ddflow_setup``), ``args`` the parsed line for a flag that picks the
    tool; anything else is its words joined by ``_``."""
    for (where, dest), name in CLI_FLAG_COMMANDS.items():
        if where == path and args is not None and getattr(args, dest, False) is True:
            return name
    verb = getattr(args, "verb", None) if args is not None else None
    if verb and (path, verb) in CLI_VERB_COMMANDS:
        return CLI_VERB_COMMANDS[(path, verb)]
    if path in CLI_COMMAND_NAMES:
        return CLI_COMMAND_NAMES[path]
    if path in routed:
        return command_name(routed[path][0])
    if len(path) == 1 and path[0] in covering:
        return command_name(covering[path[0]][0])
    return "_".join(path).replace("-", "_")
