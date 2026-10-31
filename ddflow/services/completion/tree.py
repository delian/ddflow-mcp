"""The command tree, read off the real argparse parser -- never written down.

`command_tree(parser)` walks the parser `build_parser()` returns and reports, for every
command path, its options, positionals and subcommands as plain JSON-able data. Nothing
here names a command, an option or a choice: they are whatever the parser's own actions
hold, so a command added to the registry or to a hand-written parser module is completable
without touching this file.

What the walk offers is what argparse itself shows:

* a command is offered under the names of its subparsers action's ``choices`` iteration --
  compat aliases (`AliasChoices.hidden`) answer to a lookup but are not iterated;
* an option is offered under its action's ``option_strings`` -- an old flag
  (`parser._compat_flags`) is registered in ``_option_string_actions`` only, so it is
  absent, and an action whose help is ``argparse.SUPPRESS`` is skipped unless it is the
  copy of an ancestor's option (`--json` after a subcommand), which is offered.

Importing this module imports only ``argparse``: it runs on every TAB.
"""

from __future__ import annotations

import argparse
from typing import Any

SCHEMA = "completion-tree@1"


def command_tree(parser: argparse.ArgumentParser) -> dict[str, Any]:
    """The tree of ``parser`` as nested dicts of lists, strings, bools and None, in the
    order argparse declares things (stable for one parser)."""
    return {"schema": SCHEMA, **_node(parser, "", ())}


def walk(tree: dict[str, Any], path: tuple[str, ...] = ()) -> Any:
    """Yield ``(path, node)`` for ``tree`` and every command below it, depth first."""
    yield path, tree
    for name, sub in tree["commands"].items():
        yield from walk(sub, (*path, name))


def at(tree: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any] | None:
    """The node for a command path, or None when no such command exists."""
    node: dict[str, Any] | None = tree
    for word in path:
        node = node["commands"].get(word) if node else None
    return node


def _node(
    parser: argparse.ArgumentParser, help_: str, inherited: tuple[argparse.Action, ...]
) -> dict[str, Any]:
    own = tuple(_visible_options(parser, inherited))
    subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    commands: dict[str, Any] = {}
    seen = (*inherited, *own)
    for sub in subs:
        helps = {c.dest: c.help or "" for c in sub._choices_actions}
        for name, child in sub.choices.items():  # iteration: the aliases are not here
            commands[name] = _node(child, _plain(helps.get(name, "")), seen)
    return {
        "help": _plain(help_),
        "options": [_option(a) for a in own],
        "positionals": [_positional(a) for a in parser._actions if _is_positional(a)],
        "exclusive": _groups(parser),
        "commands": commands,
    }


def _is_positional(a: argparse.Action) -> bool:
    return not a.option_strings and not isinstance(a, argparse._SubParsersAction)


def _visible_options(
    parser: argparse.ArgumentParser, inherited: tuple[argparse.Action, ...]
) -> Any:
    mirrored = {s for a in inherited for s in a.option_strings}
    for a in parser._actions:
        if not a.option_strings:
            continue
        if a.help == argparse.SUPPRESS and not set(a.option_strings) & mirrored:
            continue
        yield a


def _plain(text: str | None) -> str:
    return "" if text in (None, argparse.SUPPRESS) else " ".join(str(text).split())


def _arity(a: argparse.Action) -> str:
    """How many values: ``0``, ``1``, ``?`` , ``*``, ``+``, ``...`` (argparse's REMAINDER and PARSER) or an integer as text."""
    if a.nargs == 0:
        return "0"
    return "1" if a.nargs is None else str(a.nargs)


def _value(a: argparse.Action) -> dict[str, Any]:
    t = getattr(a, "type", None)
    meta = a.metavar
    return {
        "arity": _arity(a),
        "choices": None if a.choices is None else [str(c) for c in a.choices],
        "type": None if t is None else getattr(t, "__name__", type(t).__name__),
        "metavar": meta if meta is None or isinstance(meta, str) else list(meta),
        "dest": a.dest,
    }


def _repeats(a: argparse.Action) -> bool:
    """May the flag be given again? An append, extend or count action does; so does an action argparse does
    not ship that takes a value (`--globs` keeps every value in its own class)."""
    if isinstance(
        a,
        (
            argparse._AppendAction,
            argparse._ExtendAction,
            argparse._AppendConstAction,
            argparse._CountAction,
        ),
    ):
        return True
    return type(a).__module__ != "argparse" and a.nargs != 0


def _option(a: argparse.Action) -> dict[str, Any]:
    return {
        "flags": list(a.option_strings),
        "help": _plain(a.help),
        "required": bool(a.required),
        "repeat": _repeats(a),
        **_value(a),
    }


def _positional(a: argparse.Action) -> dict[str, Any]:
    return {"help": _plain(a.help), **_value(a)}


def _groups(parser: argparse.ArgumentParser) -> list[list[str]]:
    """The flags of each mutually exclusive group, one list per group (first flag each)."""
    out = []
    for g in parser._mutually_exclusive_groups:
        flags = [a.option_strings[0] for a in g._group_actions if a.option_strings]
        if len(flags) > 1:
            out.append(flags)
    return out
