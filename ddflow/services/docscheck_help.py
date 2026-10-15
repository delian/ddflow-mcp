"""[docs].cli_command for the documentation check: resolve `<tool> <verb> --flag` mentions
by walking the tool's own ``--help`` (decision D-docs-stage).

A flag some other command spells proves nothing about THIS verb, so a mention is checked
against the help of the deepest verb it names. The walk descends while ``<chain> <word>
--help`` exits 0: an invalid verb exits non-zero (argparse: 2), so the first word that is
not a verb ends the chain and the rest are positionals. Each chain is run once. A tool that
cannot run (missing, timing out) is a note and its mentions are unchecked, never passed.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from ..infra import proc as P

#: Runs an argv and returns how it ended; injected so the check spawns nothing by default.
Runner = Callable[[list[str]], P.Captured]

FLAG = re.compile(r"--[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
VERB = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_GAP = re.compile(r"\s{2,}")


def command_runner(root, timeout_s: int) -> Runner:
    """A `Runner` that runs argv in ``root`` with ``timeout_s`` per call, no shell."""
    return lambda argv: P.capture(argv, timeout=timeout_s, cwd=Path(root))


def help_flags(text: str) -> set[str]:
    """The long flags a ``--help`` text defines: those in its usage block and in the option
    column of its option lines. Prose that merely mentions a flag defines none."""
    out: set[str] = set()
    in_usage = False
    for line in text.splitlines():
        if line.lower().startswith("usage:"):
            in_usage = True
        elif not line.strip():
            in_usage = False
        if in_usage:
            out.update(FLAG.findall(line))
        elif line.lstrip().startswith("-"):
            out.update(FLAG.findall(_GAP.split(line.strip())[0]))
    return out


def flag_of(token: str) -> str | None:
    """The long flag a command-line token spells (``--reason,`` and ``--reason=x`` both
    spell ``--reason``), or None when it is not one."""
    m = FLAG.match(token)
    return m.group(0) if m and not token[m.end() :][:1].isalnum() else None


class HelpIndex:
    """The flags each verb chain of the configured commands accepts, from their ``--help``."""

    def __init__(self, run: Runner, commands: list[str]) -> None:
        self._run = run
        self.commands = set(commands)
        self._cache: dict[tuple[str, ...], set[str] | None] = {}
        #: Why a command could not be walked, one note each (command or verb chain -> reason).
        self.unavailable: dict[str, str] = {}
        self._broken: set[tuple[str, ...]] = set()  #: chains whose help could not run

    def resolve(self, words: list[str]) -> tuple[list[str], set[str]] | None:
        """For ``<cmd> <word>...``: the verb chain and its flags; None when ``cmd`` is not
        walked or could not run (see ``unavailable``)."""
        if words[0] not in self.commands:
            return None
        chain = [words[0]]
        flags = self._help(chain)
        if flags is None:
            return None
        for word in words[1:]:
            if not VERB.fullmatch(word):
                break
            deeper = self._help([*chain, word])
            if (*chain, word) in self._broken:  # could not tell: judging by the parent would guess
                return None
            if deeper is None:
                break
            chain.append(word)
            flags = deeper
        return chain, flags

    def _help(self, chain: list[str]) -> set[str] | None:
        key = tuple(chain)
        if key not in self._cache:
            done = self._run([*chain, "--help"])
            if not done.ran:
                self.unavailable[" ".join(chain)] = f"could not run: {done.error}"
                self._broken.add(key)
                self._cache[key] = None
            elif done.returncode == 0:
                self._cache[key] = help_flags(done.stdout or done.stderr)
            else:
                if len(chain) == 1:
                    self.unavailable[chain[0]] = f"`--help` exited {done.returncode}"
                self._cache[key] = None
        return self._cache[key]
