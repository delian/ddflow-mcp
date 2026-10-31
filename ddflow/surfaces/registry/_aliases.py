"""Aliases, deprecations and the argparse parser that understands them (D-compat).

`Alias` and `Notices` say what an old name became and report each once; `AliasChoices`,
`add_command_alias` and `_hide_from_abbreviation` wire a hidden old name into argparse;
`SuggestingParser` is the root parser whose subparsers record the command words taken
(`_WordsRecorder`, read back with `parsed_path`).
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import io
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ...config_sections._compat import MIN_REMOVED_IN, check_rename
from ..argtypes import glob_list_hint

# -- aliases and deprecations (D-compat) ---------------------------------------------

#: What an old name is, in a notice: a CLI command (group), a CLI flag, an MCP tool, a tool argument.
KINDS = ("command", "flag", "tool", "argument")

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


@dataclass(frozen=True)
class Alias:
    """An old name that still works: what it was, what replaced it, and since when.

    Calling by it works; the first use in a session says so in one line (`Notices`); it is
    hidden from ``--help`` and ``tools/list`` and is never removed before 1.0 (`removed_in`
    is checked wherever one is declared).
    """

    kind: str
    old: str
    new: str
    since: str
    removed_in: str = MIN_REMOVED_IN

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"alias {self.old!r}: kind {self.kind!r} not in {KINDS}")
        if not self.old or self.old == self.new:
            raise ValueError(f"alias {self.old!r} must differ from {self.new!r}")
        check_rename(f"{self.kind} {self.old!r}", self.since, self.removed_in)

    def notice(self) -> str:
        """The one line a caller reads. No prefix: the CLI adds ``ddflow: ``, MCP ``note: ``."""
        return (
            f"{self.kind} '{self.old}' is deprecated since {self.since}; use '{self.new}' "
            f"(the old name keeps working until {self.removed_in})."
        )


class Notices:
    """Which aliases a session has already been told about: once each (D-compat)."""

    def __init__(self) -> None:
        self._seen: set[Alias] = set()

    def fresh(self, aliases: Iterable[Alias]) -> list[Alias]:
        """The aliases among ``aliases`` not reported before, now marked reported."""
        out: list[Alias] = []
        for a in aliases:
            if a not in self._seen:
                self._seen.add(a)
                out.append(a)
        return out


def closest(name: str, candidates: Iterable[str]) -> str:
    """The candidate nearest to ``name`` for a 'did you mean', or "" when none is near."""
    near = difflib.get_close_matches(name, sorted(set(candidates)), n=1, cutoff=0.6)
    return near[0] if near else ""


def _did_you_mean(name: str, candidates: Iterable[str]) -> str:
    near = closest(name, candidates)
    return f" Did you mean '{near}'?" if near else ""


def _check_aliases(what: str, aliases: tuple[str, ...], own: str, since: str, removed: str) -> None:
    if not aliases:
        return
    check_rename(what, since, removed)
    if len(set(aliases)) != len(aliases) or own in aliases:
        raise ValueError(f"{what}: aliases must be distinct and differ from {own!r}")
    if bad := [a for a in aliases if not _WORD.fullmatch(a.lstrip("-"))]:
        raise ValueError(f"{what}: {bad[0]!r} is not a usable alias")


class AliasChoices(dict):
    """A subparsers' ``choices`` that can also answer to hidden alias names.

    Lookup (``in``, ``[]``, ``get``) finds an alias; iteration, ``keys``, ``items`` and
    ``len`` do not show it, so ``--help``, usage lines, error messages and every walk over a
    parser list the canonical commands only. The alias maps to the SAME parser.
    """

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.hidden: dict[str, argparse.ArgumentParser] = {}

    def __missing__(self, key: str) -> argparse.ArgumentParser:
        return self.hidden[key]

    def __contains__(self, key: object) -> bool:
        return super().__contains__(key) or key in self.hidden

    def get(self, key: str, default: Any = None) -> Any:
        return self[key] if key in self else default


def _alias_table(subparsers: argparse._SubParsersAction) -> AliasChoices:
    """``subparsers.choices`` as an `AliasChoices`, replacing the plain dict on first use."""
    if not isinstance(subparsers.choices, AliasChoices):
        both = AliasChoices(subparsers._name_parser_map)
        subparsers._name_parser_map = both
        subparsers.choices = both
    return subparsers.choices  # type: ignore[return-value]


def add_command_alias(
    subparsers: argparse._SubParsersAction,
    parser: argparse.ArgumentParser,
    word: str,
    alias: Alias,
) -> None:
    """Make the one word ``word`` reach ``parser`` under ``subparsers``, hidden; ``alias``
    is what a caller who typed it is told."""
    table = _alias_table(subparsers)
    if word in table:
        raise ValueError(f"alias {word!r} is already a command or alias here")
    table.hidden[word] = parser
    if not hasattr(subparsers, "_compat"):
        subparsers._compat = {}  # type: ignore[attr-defined]
    subparsers._compat[word] = alias  # type: ignore[attr-defined]


def _hide_from_abbreviation(parser: argparse.ArgumentParser) -> None:
    """Keep ``parser``'s old flags out of argparse's prefix matching: `--do` matched both
    `--docs` and its old name `--doc` (the same action) and was refused as ambiguous. The old
    flag still matches EXACTLY; only an abbreviation of it is not offered."""
    matches = parser._get_option_tuples

    def visible(option_string: str) -> list[Any]:
        found = matches(option_string)
        if option_string[:2] != option_string[:1] * 2:
            return found  # `-dv`: a short option with its value attached is not an abbreviation
        old = parser._compat_flags  # type: ignore[attr-defined]
        return [m for m in found if m[1] not in old]

    parser._get_option_tuples = visible  # type: ignore[method-assign]


def _stdlib_drops_subcommand_dashdash() -> bool:
    """Does this Python's argparse accept ``prog a -- x`` for a subcommand ``x`` of ``a``?
    (3.13 does; 3.12.3 reports ``invalid choice: '--'``.) Asked of argparse itself, once,
    so a release that backports the fix is not stripped twice."""
    top = argparse.ArgumentParser(exit_on_error=False)
    a = top.add_subparsers(dest="a").add_parser("a")
    a.add_subparsers(dest="x").add_parser("x")
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            top.parse_args(["a", "--", "x"])
    except (argparse.ArgumentError, SystemExit):
        return False
    return True


#: See `_stdlib_drops_subcommand_dashdash`; `SuggestingParser._get_values` reads it.
_STDLIB_DROPS_SUBCOMMAND_DASHDASH = _stdlib_drops_subcommand_dashdash()


class SuggestingParser(argparse.ArgumentParser):
    """The root parser: an unknown command gets a 'did you mean'. Its subparsers inherit it,
    and every level records the command word taken (`_WordsRecorder`)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.register("action", "parsers", _WordsRecorder)
        self._argv: list[str] = []

    def parse_known_args(self, args=None, namespace=None):
        # Kept for `error`: it reads the words that were really parsed, not `sys.argv`.
        self._argv = list(sys.argv[1:] if args is None else args)
        return super().parse_known_args(None if args is None else self._argv, namespace)

    def _get_values(self, action: argparse.Action, arg_strings: list[str]) -> Any:
        # Before Python 3.13 argparse strips a `--` only for ordinary positionals: the
        # values of a subparsers action keep it at their head, and the command word check
        # then refuses `--` itself -- `ddflow task -- list` died with "invalid choice: '--'"
        # on 3.12.3 (B-dashdash-312). The marker means "what follows is positional", which is
        # all a command word is, so drop the one that opens a subcommand's values -- but only
        # where argparse has not already (a second strip would eat a literal `--`).
        if (
            not _STDLIB_DROPS_SUBCOMMAND_DASHDASH
            and action.nargs == argparse.PARSER
            and arg_strings[:1] == ["--"]
            and len(arg_strings) > 1
        ):
            arg_strings = arg_strings[1:]
        return super()._get_values(action, arg_strings)

    def error(self, message: str) -> Any:
        for a in self._actions:
            # only the command word: another option's bad value is not a command name
            if isinstance(a, argparse._SubParsersAction) and (
                m := re.match(rf"argument {re.escape(a.dest)}: invalid choice: '([^']*)'", message)
            ):
                message += _did_you_mean(m.group(1), list(a.choices))
        super().error(glob_list_hint(message, self._argv))


def used_aliases(
    parser: argparse.ArgumentParser, args: argparse.Namespace, argv: Iterable[str] = ()
) -> list[Alias]:
    """The aliases a parsed command line went through: the command words typed (from the
    namespace the subparsers filled) and the flags typed (from ``argv``, up to ``--``)."""
    out: list[Alias] = []
    node = parser
    flags: dict[str, Alias] = dict(getattr(node, "_compat_flags", {}))
    while True:
        sub = next((a for a in node._actions if isinstance(a, argparse._SubParsersAction)), None)
        typed = getattr(args, sub.dest, None) if sub is not None else None
        if sub is None or typed is None or typed not in sub.choices:
            break
        if hit := getattr(sub, "_compat", {}).get(typed):
            out.append(hit)
        node = sub.choices[typed]
        flags.update(getattr(node, "_compat_flags", {}))
    for token in argv:
        if token == "--":
            break
        if (hit := flags.get(token.split("=", 1)[0])) and hit not in out:
            out.append(hit)
    return out


class _WordsRecorder(argparse._SubParsersAction):
    """A subparsers action that also records which command word was taken, on the namespace
    under `WORDS`: ``("gate", "record")``. Read from argparse's own decision, not rebuilt from
    the words typed (an option's value can spell a command, ``--`` makes the rest positional,
    and an option can share a subparser's ``dest``: ``bisect --cmd`` overwrote ``cmd``)."""

    def __call__(self, parser, namespace, values, option_string=None):  # type: ignore[no-untyped-def]
        word = values[0] if isinstance(values, (list, tuple)) else values
        super().__call__(parser, namespace, values, option_string)
        target = self._name_parser_map.get(word)
        # the sub-namespace was copied over ours by now and carries the deeper words
        canonical = next((n for n, p in self.choices.items() if p is target), word)
        setattr(namespace, WORDS, (canonical, *getattr(namespace, WORDS, ())))


#: The namespace attribute `_WordsRecorder` fills.
WORDS = "_command_words"


def parsed_path(args: argparse.Namespace) -> tuple[str, ...]:
    """The command words a parsed command line went through, aliases resolved to the
    command they name: ``("gate", "record")``; empty when no subcommand was taken."""
    return tuple(getattr(args, WORDS, ()))
