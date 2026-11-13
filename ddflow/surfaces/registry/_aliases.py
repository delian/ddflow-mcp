"""Aliases and deprecations (D-compat), kept separate from the parser classes.

`Alias` and `Notices` say what an old name became and report each once; `AliasChoices`,
`add_command_alias` and `_hide_from_abbreviation` wire a hidden old name into argparse.
"""

from __future__ import annotations

import argparse
import difflib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ...config_sections._compat import MIN_REMOVED_IN, check_rename

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
        # A positional `nargs='*'` stores a list; it is a value, not a command name, and
        # hashing it raises TypeError (Bab15b39e03). Only a str can name a command or alias.
        return isinstance(key, str) and (super().__contains__(key) or key in self.hidden)

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
