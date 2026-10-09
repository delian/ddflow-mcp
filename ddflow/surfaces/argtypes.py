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
