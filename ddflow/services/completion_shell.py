"""The shell activation text `ddflow completion <bash|zsh|fish>` prints.

Shells run it through three spellings -- ``eval "$(ddflow completion zsh)"``, the unquoted
``eval $(ddflow completion zsh)`` and ``source <(ddflow completion zsh)`` -- and the
unquoted one joins every output line with a space, so a leading ``#`` comment or a
newline-separated statement list silently becomes one comment and NOTHING runs. The text
therefore is exactly ONE line, with no comment: for bash and zsh a single ``eval`` of the
argcomplete registration code written as a ``$'...'`` string (newlines, tabs and space
runs escaped, so joining or word-splitting cannot change it); for fish ``;``-separated
statements.

The registration code is argcomplete's own (`argcomplete.shellcode`), so no command,
option or choice name is spelled here, and it does not depend on a
``register-python-argcomplete`` script being on PATH. zsh needs the completion system
(``compdef``): it is loaded with ``compinit -C`` (cached dump, no security scan) only when
it is not yet loaded. The code is not run inside a function: argcomplete's zsh code reads
``$zsh_eval_context`` to tell a top-level eval from an autoload.
"""

from __future__ import annotations

import re
from importlib import import_module

SHELLS = ("bash", "zsh", "fish")
#: The executable the registration is for.
PROGRAM = "ddflow"
_NO_ENGINE = "ddflow: shell completion needs the 'argcomplete' package (pip install argcomplete)"
_ZSH_BOOT = "(( $+functions[compdef] )) || { autoload -Uz compinit; compinit -C; }; "
_SPACES = re.compile(r" {2,}")


def for_shell(shell: str) -> str:
    """The one-line activation text for ``shell`` (one of `SHELLS`), newline-terminated."""
    if shell not in SHELLS:
        raise ValueError(f"completion shell {shell!r} is not one of {SHELLS}")
    try:
        argcomplete = import_module("argcomplete")
    except ImportError:
        return _message(shell)
    code = argcomplete.shellcode([PROGRAM], shell="bash" if shell == "bash" else shell)
    lines = [ln.strip() for ln in code.splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("#")]
    if shell == "fish":
        return "; ".join(lines) + "\n"
    quoted = _quote("\n".join(lines))
    return f"{_ZSH_BOOT if shell == 'zsh' else ''}eval $'{quoted}'\n"


def _message(shell: str) -> str:
    """No engine: say so, on stderr, as one harmless line."""
    if shell == "fish":
        return f"echo '{_NO_ENGINE}' >&2\n"
    return f"printf '%s\\n' '{_NO_ENGINE}' >&2\n"


def _quote(text: str) -> str:
    """``text`` as the inside of a ``$'...'`` word that survives being split on whitespace
    and re-joined with single spaces: every newline, tab and space run beyond one is an
    escape, and the quote and backslash are escaped."""
    out = text.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\t", "\\t")
    return _SPACES.sub(lambda m: " " + "\\x20" * (len(m.group()) - 1), out)
