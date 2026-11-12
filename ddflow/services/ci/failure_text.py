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
#: A scratch tree ddflow makes: the directory name is the marker, whatever the temp root is.
_SCRATCH = re.compile(r"(?:/[^\s:'\"]*?)?/ddflow-(?:ci|base|mut)\.[A-Za-z0-9_]+")
#: A lane worktree: ``<anything>/.ddflow/worktrees/<item>``.
_WORKTREE = re.compile(r"(?:/[^\s:'\"]*?)?/\.ddflow/worktrees/[^/\s:'\"]+")
#: pytest's own temp tree: the user and the run counter differ between two runs.
_PYTEST = re.compile(r"(?:/[^\s:'\"]*?)?/pytest-of-[^/\s]+/pytest-\d+")


def normalise(text: str, roots: Iterable[str] = ()) -> str:
    """``text`` with each tree root blanked to ``<tree>``: the named ``roots`` (longest first,
    so a nested root is not half-replaced) and the roots ddflow's own scratch trees and
    worktrees have. A path outside any tree is left alone."""
    for root in sorted(
        {str(r).rstrip("/") for r in roots if str(r).strip("/")}, key=len, reverse=True
    ):
        text = text.replace(root, TREE)
    text = _PYTEST.sub(PYTEST_TMP, text)
    return _WORKTREE.sub(TREE, _SCRATCH.sub(TREE, text))


def digest(text: str, roots: Iterable[str] = ()) -> str:
    """The comparison key of ``text``: equal for the same failure from two trees."""
    return content_digest(normalise(text, roots))[:16]
