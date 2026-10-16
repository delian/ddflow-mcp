"""What several command declarations and parser modules share: the repeatable `--globs`
action and its help, and the positive-integer argument type.

Standard library only, so a declaration (`surfaces/declared/`, read by the MCP tool table
without loading `ddflow.api`) can use them. `surfaces/parsers/_common.py` re-exports them.
"""

from __future__ import annotations

import argparse

#: Help for every `--globs` that takes paths to claim, add or update.
GLOBS_HELP = (
    "path globs, comma-separated (a.py,src/**) or a JSON array; repeat the flag to add "
    "more -- every value is kept"
)


class _Globs(argparse.Action):
    """A repeatable `--globs`: every value is KEPT, as a list, for `globspec.parse`.

    A plain store kept only the last value, so an agent that passed the flag ten times
    held one path -- or none -- and the conflict check never saw the rest (Bdc85898c40).
    """

    def __call__(self, parser, namespace, values, option_string=None):
        cur = getattr(namespace, self.dest, None)
        setattr(namespace, self.dest, [*(cur if isinstance(cur, list) else []), values])


def _positive_int(v: str) -> int:
    n = int(v)
    if n < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return n


#: The flags that take a glob list.
_GLOB_FLAGS = ("--globs", "--add-globs", "--remove-globs")


def glob_list_hint(message: str, argv: list[str]) -> str:
    """``message`` (an argparse error) with the way to write a glob list, when it is the
    "unrecognized arguments" of `--globs a b c`: the words after the flag's one value were
    read as strays. A list is ONE comma-separated value (`--globs a,b,c`), or the flag
    repeated. Only a flag whose value is IMMEDIATELY followed by the first stray word is
    named; a stray after some other flag gets the plain error."""
    if not message.startswith("unrecognized arguments:"):
        return message
    stray = message.split(":", 1)[1].split()
    for i, tok in enumerate(argv):
        flag = tok.split("=", 1)[0]
        if flag not in _GLOB_FLAGS or not stray:
            continue
        if "=" in tok:
            value, first = tok.split("=", 1)[1], i + 1
        else:
            value, first = (argv[i + 1] if i + 1 < len(argv) else ""), i + 2
        run: list[str] = []
        for k, word in enumerate(stray):
            if argv[first + k : first + k + 1] != [word] or word.startswith("-"):
                break
            run.append(word)
        if run and value:
            return (
                f"{message}\n{flag} takes ONE value, a comma-separated list "
                f"({flag} {','.join([value, *run])}), or the flag repeated; "
                f"not space-separated words"
            )
    return message
