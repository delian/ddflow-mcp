"""[docs].cli_command for the documentation check: resolve `<tool> <verb> --flag` mentions
by walking the tool's own ``--help`` (decision D-docs-stage).

A flag some other command spells proves nothing about THIS verb, so a mention is checked
against the help of the deepest verb it names. The walk descends while ``<chain> <word>
--help`` exits 0: an invalid verb exits non-zero (argparse: 2), so the first word that is
not a verb ends the chain and the rest are positionals. Each chain is run once. A tool that
cannot run (missing, timing out) is a note and its mentions are unchecked, never passed.

ddflow's own help is NOT walked by spawning ``ddflow <verb> --help``: the declared command
registry (``ddflow.surfaces.cli.build_parser``) already knows every verb and flag, and each
spawn pays the import (~1.5 s), which turned `ddflow docs check` on this repository into a
~4 minute wait (DX-docs-check-speed). The in-process walk replays the same argparse tree's
``parse_args … --help`` per chain instead -- the exact text and exit the spawned process
would produce -- so only a tool that is NOT ddflow is spawned, bounded by ``timeout_s`` (the
per-call timeout knob), and the walk of ddflow's own tree is cached per ddflow version.
"""

from __future__ import annotations

import argparse
import re
import threading
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

from ..infra import proc as P

#: Runs an argv and returns how it ended; injected so the check spawns nothing by default.
Runner = Callable[[list[str]], P.Captured]

#: The commands whose ``<verb> --help`` is walked from THIS process's argparse registry
#: instead of a spawn. ddflow's registry is its help: spawning it again pays an import per
#: verb; any other tool (ddflow-mcp, a project's own CLI) has no registry here and spawns.
IN_PROCESS_COMMANDS: tuple[str, ...] = ("ddflow",)

FLAG = re.compile(r"--[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
VERB = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_GAP = re.compile(r"\s{2,}")


def command_runner(
    root, timeout_s: int, *, in_process_commands: Iterable[str] | None = None
) -> Runner:
    """A `Runner` that runs argv in ``root`` with ``timeout_s`` per call, no shell.

    A ``<command> <chain...> --help`` whose command is named in ``in_process_commands`` is
    answered from the argparse registry of this process (see the module docstring); every
    other argv is spawned, bounded by ``timeout_s``. The timeout knob never applies to the
    in-process walk: it has no I/O to time out.
    """
    if in_process_commands is None:
        known = IN_PROCESS_COMMANDS
    else:
        # A single string names one command (a str IS an Iterable[str], but of its chars).
        known = (in_process_commands,) if isinstance(in_process_commands, str) else tuple(in_process_commands)
    spawn = lambda argv: P.capture(argv, timeout=timeout_s, cwd=Path(root))  # noqa: E731

    def run(argv: list[str]) -> P.Captured:
        if known and len(argv) > 1 and argv[0] in known and argv[-1] == "--help":
            return _HERE.help(argv)
        return spawn(argv)

    #: The set this runner actually treats as in-process — stamped on the runner so
    #: `HelpIndex.walk_all` walks exactly the commands THIS runner answers in-process,
    #: not a re-derived list (the runner's knob is the one source of the fact).
    run.in_process_of = set(known)
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


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser] | None:
    """The subparser choice table of ``parser`` (None: it has none).

    The walk descends argparse's private ``_actions`` (its ``_SubParsersAction`` and that
    action's ``choices``) and relies on ``parse_args`` answering ``--help`` with a
    ``SystemExit`` — stdlib internals on the pinned argparse. A future argparse that
    renamed them would yield a partial walk, which the speed test's chain budget
    (`MIN_CHAINS`), not silent behavior, is what catches.
    """
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return None


class _InProcessHelp:
    """`ddflow <chain...> --help` answered from THIS process's argparse registry.

    ``build_parser()`` IS the help every spawned ``ddflow --help`` prints — the stdout of the
    spawn is exactly ``parser.format_help()`` — so a chain is walked by descending the parser
    choices by word: a word that is no subcommand is where a spawn would exit 2 and the chain
    stops there, exactly like it. The tree build and every chain's replayed help are cached
    per ddflow version, in this process: a render made for version X is never reused for
    version Y (per tool version, per DX-docs-check-speed).
    """

    #: ``(version, root parser, replayed helps)`` of the last build; a module-level memo,
    #: because every runner of one process asks for the same registry.
    _built: tuple[str, argparse.ArgumentParser, dict[tuple[str, ...], P.Captured]] | None = None

    def parser(self) -> argparse.ArgumentParser:
        """The root parser this process declares, built once per ddflow version."""
        from ddflow import __version__
        from ddflow.surfaces.cli import build_parser  # deferred: only the walk needs it

        if self._built is None or self._built[0] != __version__:
            self._built = (__version__, build_parser(), {})
        return self._built[1]

    def help(self, argv: list[str]) -> P.Captured:
        """``Captured`` as the spawned ``argv`` would have been (see the class docstring):
        ``parse_args`` with ``--help`` run against the registry, its stdout/stderr and its
        exit replayed — replayed once per chain then cached (per version, per process).
        `argparse` answers a help request wherever it meets it (`ddflow config set --help`
        prints the config help, exit 0, though ``set`` is no subcommand), and invalid words
        error with exit 2 — no flag-counting of our own."""
        import io
        import sys
        from contextlib import redirect_stderr, redirect_stdout

        try:
            with _RENDER_LOCK:  # build-to-cache is ONE critical section, like any render is:
                #: the version check, the memo the next steps read and the render happen
                #: while no other replay can rebuild beside them (a rebuild between the
                #: check and the read would leak a former version's help into the new
                #: memo -- other replays of one process share this registry).
                parser = self.parser()
                memo = self._built[2]  # the running version's own memo, bound AFTER its check
                key = tuple(argv[1:])
                if key in memo:
                    return memo[key]

                out, err = io.StringIO(), io.StringIO()
                renderer = threading.get_ident()  # the one thread whose stdio writes ARE the render
                try:  # argparse ends help and usage errors by raising SystemExit, like a spawn
                    #: the redirects swap the process-global sys.stdio, which no lock can make
                    #: thread-local; the swaps below route per thread instead, so the render
                    #: captures ONLY the rendering thread's writes, and any OTHER thread's
                    #: print goes to the real stream it meant -- never swallowed into the
                    #: render's text (a mix would plant phantom flags in the finding set).
                    with redirect_stdout(_RoutedTo(sys.stdout, out, renderer)), redirect_stderr(
                        _RoutedTo(sys.stderr, err, renderer)
                    ):
                        parser.parse_args(argv[1:])
                except SystemExit as exc:
                    code = exc.code if isinstance(exc.code, int) else 1
                else:
                    code = 0
        except SystemExit as exc:  # the build itself exited (argparse ends exits): could not run
            return P.Captured(error=RuntimeError(f"registry build exited: {exc.code}"))
        except Exception as exc:  # a registry that cannot build is "could not run"
            return P.Captured(error=exc)
        capped = (
            P.Captured(code, out.getvalue(), err.getvalue())
            if code
            else P.Captured(0, out.getvalue() or err.getvalue(), "")
        )
        memo[key] = capped
        return capped

    def chains(self, cmd: str) -> Iterator[list[str]]:
        """Every verb chain of ``cmd``'s tree, root first, deepest last."""
        tree = self.parser()
        stack: list[list[str]] = [[cmd]]
        while stack:
            chain = stack.pop()
            yield chain
            choices = _subcommands(_require_walk(tree, chain))
            if choices is None:
                continue
            for name in sorted(choices):
                stack.append([*chain, name])


#: The in-process walker every runner here shares: one registry per process, whatever
#: builds the runner (the CLI, the MCP tool, a test).
_HERE = _InProcessHelp()


class _RoutedTo:
    """A stand-in for ``sys.stdout``/``sys.stderr`` while one render holds them: writes of
    the thread rendering go to ``sink`` (the render's own capture); ANY other thread's
    write goes to ``real`` -- the process-wide stream it meant. A foreign print during a
    render is therefore neither swallowed into the capture (a mix would plant phantom
    flags in the finding set) nor dropped from its terminal: the redirect cannot be made
    thread-local, but a capture can be made this-thread-of-it."""

    __slots__ = ("_sink", "_real", "_owner", "isatty", "fileno")

    def __init__(self, real, sink, owner: int) -> None:
        self.isatty = real.isatty  # stream introspection still answers as the REAL stream
        self.fileno = real.fileno
        self._real, self._sink, self._owner = real, sink, owner

    def write(self, text: str) -> int:
        (self._sink if threading.get_ident() == self._owner else self._real).write(text)
        return len(text)

    def flush(self) -> None:
        self._real.flush()


#: Serializes `parse_args` replays: one render's `_RoutedTo` swaps hold the process-global
#: stdio while it runs, so two renders never overlap.
_RENDER_LOCK = threading.Lock()


def _require_walk(root: argparse.ArgumentParser, chain: list[str]) -> argparse.ArgumentParser:
    """The parser ``chain`` names, descended from ``root`` (called with chains from the
    same walk, so every word IS a choice)."""
    parser = root
    for word in chain[1:]:
        choices = _subcommands(parser)
        assert choices is not None and word in choices
        parser = choices[word]
    return parser


class HelpIndex:
    """The flags each verb chain of the configured commands accepts, from their ``--help``."""

    def __init__(self, run: Runner, commands: list[str]) -> None:
        self._run = run
        self.commands = set(commands)
        self._cache: dict[tuple[str, ...], set[str] | None] = {}
        #: Why a command could not be walked, one note each (command or verb chain -> reason).
        self.unavailable: dict[str, str] = {}
        self._broken: set[tuple[str, ...]] = set()  #: chains whose help could not run
        # The in-process commands are walked whole, now: `ddflow docs check` resolves the
        # chains a document mentions against a cache the eager walk already filled, paying
        # one bounded batch per check instead of a render per mention (DX-docs-check-speed).
        if any(c in self._in_process() for c in self.commands):
            self.walk_all()

    def _in_process(self) -> set[str]:
        """The commands THIS runner answers in-process: what the runner itself was told
        (``command_runner`` stamps it), the module default for a custom runner."""
        stamp = getattr(self._run, "in_process_of", None)
        return IN_PROCESS_COMMANDS if stamp is None else set(stamp)

    def walk_all(self) -> int:
        """Eagerly resolve every verb chain of the in-process commands (the ddflow registry)
        and return how many chains were walked; a tool a custom runner walks its own way is
        still resolved lazily, chain by a document's mention. The renders run one at a time —
        inherently serial, because each parse_args replay holds the redirected process stdio
        while it runs and a render has no I/O a second thread would overlap profitably — so
        the whole ddflow walk is one bounded batch instead of one render per mentioned verb.
        The walk `ddflow docs check` pays for a check of ddflow itself (DX-docs-check-speed)."""
        in_tree = [c for c in sorted(self.commands) if c in self._in_process()]
        walked = 0
        for cmd in in_tree:
            #: one command's tree is built (or failed) alone: a broken command notes itself
            #: and stops only ITS OWN walk, never the eager cache of the others.
            try:
                chains = list(_HERE.chains(cmd))
            except Exception as exc:  # a registry that cannot build is "could not run", not a crash
                self.unavailable[cmd] = f"could not walk: {exc}"
                continue
            for chain in chains:
                walked += 1
                key = tuple(chain)
                capped = _HERE.help([*chain, "--help"])
                if capped.ran and capped.returncode == 0:
                    self._cache[key] = help_flags(capped.stdout or capped.stderr)
                elif not capped.ran:
                    self._cache[key] = None
                    self.unavailable[" ".join(key)] = f"could not run: {capped.error}"
                    self._broken.add(key)
                else:  # ran, non-zero: the chain's failure is noted for ITS key, however deep
                    self._cache[key] = None
                    self.unavailable[" ".join(key)] = f"`--help` exited {capped.returncode}"
        return walked

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
