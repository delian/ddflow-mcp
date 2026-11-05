"""Shell-text helpers for the CI command: the ``SKIP=`` word and diff scoping.

Split out of ``ddflow/services/ci.py``: the scan of a leading ``SKIP=`` assignment
and ``scope_to_diff``, which replaces the default command's ``--all-files`` with the
``since..HEAD`` range the check is about.
"""

from __future__ import annotations

from pathlib import Path

from ...infra import worktree as W
from ..cmdrunner import executable_missing
from .precommit import DEFAULT_COMMAND, PRE_PUSH_RUN


def _escaped(quote: str, nxt: str) -> str | None:
    """What an escape pair contributes: outside quotes a backslash escapes ANY next char,
    inside double quotes only `$`, backtick, `"` and backslash (a backslash-newline is a
    line continuation that contributes NOTHING, the same outside); ``None`` is
    not-an-escape here (single quotes, a bare backslash before an ordinary double-quoted
    char) and both chars stay literal."""
    if not quote:
        return "" if nxt == "\n" else nxt
    if quote == '"' and nxt in '$`"\\\n':
        return "" if nxt == "\n" else nxt
    return None


def _shell_active(c: str, value: list[str]) -> bool:
    """Does the shell expand ``c`` in an unquoted value here? `$`, backtick, or a tilde
    at the word's head / after a `:` (a PATH-style assignment value)."""
    return c in "$`" or (c == "~" and (not value or value[-1] == ":"))


def _sub_end(quote: str, c: str, command: str, i: int, n: int) -> int:
    """The index just past a rounded `$( ... )` opened at ``i`` -- one shell word-part the
    scan must cross whole -- or -1 when ``c`` is not one (a `$` inside single quotes is
    literal, not a substitution) or the parens never balance."""
    if quote == "'" or c != "$" or i + 1 >= n or command[i + 1] != "(":
        return -1
    depth, i = 1, i + 2
    inner = ""  # quotes inside the substitution are its own: parens inside them are text
    while i < n and depth:
        c = command[i]
        if inner:
            if c == inner:
                inner = ""
            elif inner == '"' and c == "\\" and i + 1 < n and command[i + 1] in '"\\$`':
                i += 1  # `\"` etc. inside the inner double quotes is a literal quote char
        elif c in "\"'":
            inner = c
        elif c == "\\" and i + 1 < n:
            i += 1  # the escaped char inside the substitution is text, not a paren
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        i += 1
    return i if depth == 0 else -1


def _leading_skip(command: str) -> tuple[str | None, str, str]:
    """Split a leading shell ``SKIP=<word>`` off ``command``: ``(own, verbatim, rest)``
    -- the value the shell would resolve it to (`'a b'` -> `a b`), the value's source text
    (non-empty only when the value must stay verbatim), and the untouched remainder, so
    the rest of the command (shell text) is never re-tokenized. ``own`` is ``None`` when
    the value cannot be reproduced as a literal (`$`/backtick/**tilde** expansions the
    rebuild would have to guess at, or unbalanced quotes); its ``verbatim`` text then
    keeps the operator's own expansion semantics. A word whose END is uncertain is
    returned whole (``verbatim=command``, nothing after)."""
    if not command.startswith("SKIP="):
        return None, "", command
    value: list[str] = []
    activity = False  # the shell would expand part of this value: keep it verbatim
    quote = ""  # "'" or '"' inside it, "" outside
    i, n = len("SKIP="), len(command)
    while i < n:
        c = command[i]
        if c == "\\" and i + 1 < n and (got := _escaped(quote, command[i + 1])) is not None:
            if got:
                value.append(got)
            i += 1
        elif (end := _sub_end(quote, c, command, i, n)) >= 0:
            activity = True  # the whole substitution is one verbatim word-span
            i = end
            continue
        elif quote and c == quote:
            quote = ""
        elif quote:
            if quote == '"' and c in "$`":
                activity = True
            value.append(c)  # inside the quotes: everything else is literal text
        elif c in " \t\n" or c in ";\n|&<>()":
            break  # a word ends at POSIX whitespace or a shell operator (kept in the rest)
        elif c in "\"'":
            quote = c
        elif _shell_active(c, value):
            activity = True  # an expansion the shell would perform: keep the value verbatim
            value.append(c)
        else:
            value.append(c)
        i += 1
    if quote:  # unbalanced: the word's end is unknown
        return None, command, ""
    if activity:
        return None, command[:i], command[i:]  # verbatim value text, run from its end
    return "".join(value), "", command[i:]


def _sh_single(word: str) -> str:
    """``word`` single-quoted for a POSIX shell: the value stays a literal whatever it
    holds (`$(...)`, quotes), so re-emission cannot make it shell-active."""
    return "'" + word.replace("'", "'\\''") + "'"


def _scope_the_run(run: str, resolve: str) -> str:
    """``run`` with its own ` --all-files` replaced by the since..HEAD range -- when the
    run IS the default run phrase, give or take the shell operator the `SKIP=` word ended
    at (`;`, `&&`) and POSIX whitespace around them. A run carrying anything beyond that
    phrase is shell text, not the default one: it stays as written, uncorrupted."""
    at = run.find(DEFAULT_COMMAND)
    if at < 0:
        return run
    lead, tail = run[:at], run[at + len(DEFAULT_COMMAND) :]
    if lead.strip(" \t\n") in ("", ";", "&&", "&") and not tail.strip(" \t\n"):
        return lead + f"{PRE_PUSH_RUN} --from-ref {resolve} --to-ref HEAD" + tail
    return run


def scope_to_diff(command: str, tree: Path, since: str) -> str:
    """``command`` (the default pre-push command, maybe after `SKIP=...`) run over the diff
    ``since``..HEAD instead of over every file (`--all-files`): the checks a push is about run
    on what the push changes, so an error the base itself carries does not fail a branch that
    did not make it (Bf333cb2f5d). The ref name itself is never interpolated: the command runs
    through a shell, and a ref name is not shell text (the resolved commit is).
    ``command`` as written when ``since`` is not a commit there."""
    resolve = W.rev(tree, since) if since else ""
    if not resolve or " --all-files" not in command:
        return command
    own, verbatim, run = _leading_skip(command)
    scoped = _scope_the_run(run, resolve)
    if scoped == run:
        return command  # nothing was actually scoped: leave the command exactly as written
    # A leading `SKIP=` assignment shadows the INHERITED `SKIP` the run's environment set (the
    # selected-tests plan), so merge the two like the earlier tripwire run did; the merged
    # value is the env expansion plus the command's own list as a shell literal. `SKIP=''` is
    # an explicit EMPTY override -- it keeps clearing the inherited SKIP. When the value was
    # not parseable its verbatim text stays (unmerged, its own expansion semantics); with no
    # assignment the environment's own SKIP reaches pre-commit as it is.
    if own is not None:
        merged = f'SKIP="${{SKIP:+$SKIP,}}"{_sh_single(own)}' if own else 'SKIP=""'
    else:
        merged = verbatim
    return merged + scoped


def tool_missing(command: str) -> str:
    """The executable the command starts with, when it is not installed ("" when it is)."""
    return executable_missing(command)
