"""[docs].cli_command for the documentation check: resolve `<tool> <verb> --flag` mentions
by walking the tool's own ``--help`` (decision D-docs-stage).

A flag some other command spells proves nothing about THIS verb, so a mention is checked
against the help of the deepest verb it names. The walk descends while ``<chain> <word>
--help`` exits 0: an invalid verb exits non-zero (argparse: 2), so the first word that is
not a verb ends the chain and the rest are positionals. Each chain is run once. A tool that
cannot run (missing, timing out) is a note and its mentions are unchecked, never passed.

ddflow's own help comes from `in_process`: the argparse registry this process already
built (Bc621160a82), so the check never spawns `ddflow <verb> --help` once per verb —
only a non-ddflow tool keeps spawning.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from ..infra import proc as P

#: Runs an argv and returns how it ended; injected so the check spawns nothing by default.
Runner = Callable[[list[str]], P.Captured]

FLAG = re.compile(r"--[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
VERB = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_GAP = re.compile(r"\s{2,}")

#: This process's own argparse root, built once, on first use: the CLI import drags in
#: the whole surface, and one parser answers every chain of one walk.
_parser = None

#: ``redirect_stdout``/``redirect_stderr`` are process-global, so overlapping ``in_process``
#: runs could capture each other's output: every run takes the lock, and that overlap
#: serializes. What it does NOT cover is a concurrent thread writing to the real
#: stdout/stderr; the docs check is single-threaded, so none does during a walk.
_capture_lock = threading.Lock()


def in_process(verbs: list[str]) -> P.Captured:
    """Run `ddflow <verbs> --help` without a spawn: this process's parser, replayed.

    The very ``parse_args`` the spawned CLI makes, so the answer is its ``--help`` and its
    flags are exactly the spawned text's (Bc621160a82; the equivalence is pinned by
    tests/test_docscheck_help_in_process.py). Every chain answers from one parser."""
    global _parser
    with _capture_lock:  # serializes redirect+parse: a run answers from its own buffers
        if _parser is None:
            from ..surfaces.cli import build_parser  # deferred: the CLI pulls the whole tree

            _parser = build_parser()
        out, err = io.StringIO(), io.StringIO()
        code: int | None = 0
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                _parser.parse_args([*verbs, "--help"])
        except SystemExit as exc:  # --help exits 0, a bad verb chain exits 2, as the spawn does
            #: a non-int code (argparse never raises one -- ``--help`` is 0, ``error()``
            #: 2 -- so this branch is a guard, not a path): None reads "did not finish",
            #: never a success folded into 0 (standards roborev job 3705).
            code = exc.code if isinstance(exc.code, int) else None
    return P.Captured(code, out.getvalue(), err.getvalue())


def _ddflow_help_verbs(argv: list[str]) -> list[str] | None:
    """The verbs to replay when ``argv`` spells `ddflow [<verb>...] --help` naming THIS
    ddflow; None when it names another tool (then the spawn is the walk). This process IS
    ddflow, so the registry it built is the authority when the command is spelled by name
    (`ddflow`) or run as this very interpreter's module ``sys.executable -m ddflow``; any
    other spelling -- a different interpreter or a path to some binary -- names a
    specific executable and is run as before."""
    if len(argv) < 2 or argv[-1] != "--help":
        return None
    if argv[0] == "ddflow":  # bare name: THIS process is assumed to be that ddflow (deliberate)
        return argv[1:-1]
    if argv[1:3] == ["-m", "ddflow"] and argv[0] == sys.executable:
        return argv[3:-1]
    return None


def command_runner(root, timeout_s: int) -> Runner:
    """A `Runner` that runs argv in ``root`` with ``timeout_s`` per call, no shell.

    A `ddflow [<verb>...] --help` naming this process goes to `in_process` instead of a
    spawn (Bc621160a82): it is this ddflow's own help, and nothing runs, so no
    ``timeout_s`` applies; any argv naming another tool still runs in ``root``."""

    def run(argv: list[str]) -> P.Captured:
        verbs = _ddflow_help_verbs(argv)
        if verbs is not None:
            return in_process(verbs)
        return P.capture(argv, timeout=timeout_s, cwd=Path(root))

    return run


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
