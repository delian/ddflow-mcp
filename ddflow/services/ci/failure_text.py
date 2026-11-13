"""Failure text with the ci tree's path blanked, so a failure has one digest (GM-ci-path-normalise).

A test failure that prints a path prints the checkout it ran in: a ci scratch tree
(``/tmp/ddflow-ci.k3j2h1x9``), the base's scratch tree (``ddflow-base.*``), a lane worktree
(``.../.ddflow/worktrees/<item>``) or a pytest temp directory. The same failure on the base
and on the candidate then reads as two failures, and an old red looks new
(B-baseline-red-hides-new-cause compares digests). `normalise` rewrites those tree roots to
``<tree>`` and keeps everything after them: a different repo-relative path or a different
message still differs. `digest` is the comparison key.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ...core.digest import content_digest

TREE = "<tree>"
PYTEST_TMP = "<pytest-tmp>"
#: Where a path starts and ends inside failure text: not in the middle of another path or word.
_START = r"(?<![\w.\-/])"
_END = r"(?=\.?(?:[/\s:'\")\],]|$))"
#: A scratch tree ddflow makes: the directory name is the marker, whatever the temp root is.
_SCRATCH = re.compile(
    _START + r"(?:/[^\s:'\",)\]]*?)?/ddflow-(?:ci|base|mut)\.[A-Za-z0-9_]+" + _END
)
#: A lane worktree: ``<anything>/.ddflow/worktrees/<item>``.
_WORKTREE = re.compile(_START + r"(?:/[^\s:'\",)\]]*?)?/\.ddflow/worktrees/[^/\s:'\"]+" + _END)
#: pytest's own temp tree, under a directory named tmp, T (macOS) or temp (a custom root is
#: named in ``roots``): the user and the run counter differ between runs.
_PYTEST = re.compile(
    _START
    + r"(?:/(?:[^\s:'\",)\]]*?/)?(?:tmp|T|temp)|"
    + re.escape(TREE)
    + r")/pytest-of-[^/\s]+/pytest-\d+"
    + _END
)


def normalise(text: str, roots: Iterable[str] = ()) -> str:
    """``text`` with each tree root blanked to ``<tree>``: the named ``roots`` (longest first,
    then by name, so the result never depends on set order and a nested root is not
    half-replaced) and the roots ddflow's own scratch trees and worktrees have. A root matches
    whole path components only. A path outside any tree is left alone."""
    named = sorted(
        {str(r).rstrip("/") for r in roots if str(r).strip("/")}, key=lambda r: (-len(r), r)
    )
    if named:  # one pass over every root: a replacement never opens a boundary for the next
        pattern = "|".join(re.escape(r) for r in named)
        text = re.sub(_START + "(?:" + pattern + ")" + _END, TREE, text)
    text = _WORKTREE.sub(TREE, _SCRATCH.sub(TREE, text))
    text = re.sub(
        "(?:" + re.escape(TREE) + "){2,}", TREE, text
    )  # a scratch tree holding a worktree: one root
    return _PYTEST.sub(PYTEST_TMP, text)  # last: a pytest temp may sit inside a tree


def digest(text: str, roots: Iterable[str] = ()) -> str:
    """The comparison key of ``text``: equal for the same failure from two trees."""
    return content_digest(normalise(text, roots))[:16]
