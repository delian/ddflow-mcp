"""Time-out shaping for one ``gate run``: the ``--timeout`` override and the
``ddflow ci run`` command line it re-writes, shared by the query and run paths.
"""

from __future__ import annotations

import re
from dataclasses import replace

from ...core import outcome as O

#: Quoted words stay whole; the first bare `--` ends the ci options; a bare `--timeout N` is the
#: one to replace. A CONSERVATIVE scanner for a one-line gate command, not a shell parser: an
#: unclosed quote leaves the command unchanged. Accepted limits: no here-docs, no `$(...)`/backtick
#: nesting, no option-level parsing (`ddflow ci run` itself refuses an option value that is
#: `--timeout=N`), no `#` comments, no words built by quote concatenation (`+"3"0`).
#: The one predicate for "a `ddflow ci run` line", used to decide and to insert.
_CI_VERB = re.compile(r"^(\s*ddflow\s+ci\s+run)(?=\s|$)")
_CI_WORDS = re.compile(
    r"""(?P<quoted>(?:"(?:[^"\\]|\\.)*"|'[^']*'|\\[^\n])+)"""
    r"""|(?P<sep>(?:\s|\\\n)--(?=\s|$))|(?P<timeout>(?P<lead>\s|\\\n)--timeout(?:=|\s+)["']?[+-]?\d+["']?)""",
    re.S,
)


def _quotes_balanced(command: str) -> bool:
    """False when ``command`` ends inside a quote (a backslash escapes outside single quotes)."""
    quote, i = "", 0
    while i < len(command):
        c = command[i]
        if quote == "'":
            quote = "" if c == "'" else quote
        elif c == "\\":
            i += 1
        elif quote == '"':
            quote = "" if c == '"' else quote
        elif c in "'\"":
            quote = c
        i += 1
    return not quote


def _ci_command_with_timeout(command: str, timeout_s: int) -> str:
    """``command`` (a `ddflow ci run ...` line) with its `--timeout` replaced by ``timeout_s``,
    placed right after the verb. Only the ci options are touched: what follows a bare `--` belongs
    to the test runner, and a quoted option value is left as written."""
    if not _quotes_balanced(command):
        return command  # cannot tell what is quoted: leave the line as written
    out, pos = [], 0
    for m in _CI_WORDS.finditer(command):
        if m.lastgroup == "sep":
            break
        if m.lastgroup == "timeout":
            # Drop only the option; a continuation (backslash-newline) before it stays as written.
            keep = m.group("lead") if m.group("lead").startswith("\\") else ""
            out.append(command[pos : m.start()] + keep)
            pos = m.end()
    else:
        m = None
    end = m.start() if m is not None else len(command)
    out.append(command[pos:end])
    head = _CI_VERB.sub(rf"\1 --timeout {timeout_s}", "".join(out), count=1)
    return head + command[end:]


def _with_run_timeout(cfg, gdef, timeout_s: int | None, item: str, gate: str):
    """``gdef`` for one run that may take ``timeout_s`` (None: as declared; else 1..max): its own timeout, and
    the ci budget a ``ddflow ci run`` command carries (``--timeout``), both replaced. A request
    outside ``[gates].max_run_timeout_s`` is the refusal instead."""
    if timeout_s is None or gdef is None:
        return gdef
    if not 0 < timeout_s <= cfg.gates.max_run_timeout_s:
        return O.refused(
            "gate.run",
            f"--timeout {timeout_s} is outside 1..{cfg.gates.max_run_timeout_s} seconds "
            f"([gates].max_run_timeout_s bounds a per-run timeout)",
            id=item,
            gate=gate,
            outcome="",
        )
    command = gdef.command or ""
    if _CI_VERB.match(command):
        command = _ci_command_with_timeout(command, timeout_s)
    return replace(gdef, timeout_s=timeout_s, command=command)
