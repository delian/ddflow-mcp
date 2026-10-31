"""Where this code runs from."""

from __future__ import annotations

from pathlib import Path

#: The checkout this code was imported from: `<tree>/ddflow/config/_tree.py` -> `<tree>`.
#: For an installed ddflow it is site-packages, which is no project's root. The same
#: answer as `infra.paths.package_parent()`, recomputed because `config` is the bottom
#: layer and may import nothing (tests/test_layering.py); the module sits directly under
#: `ddflow/config/`, so `parents[2]` reaches `<tree>` and the level count cannot drift the
#: way the layered modules' did.
_CODE_TREE = Path(__file__).resolve().parents[2]


def _is_code_tree(root: Path) -> bool:
    """Is `root` the very tree this code runs from -- not merely a parent of it?

    Equality, not a prefix: a harness tree NESTED under the primary
    (`.claude/worktrees/x`) has the primary as a prefix, and it is exactly the checkout
    whose older code meets the primary's newer config (bug B9cb7dd1c3b).

    ``_CODE_TREE`` is read from `ddflow.config` at call time, not this module's copy, so
    a monkeypatch of ``ddflow.config._CODE_TREE`` (which re-exports the value from here)
    takes effect in tests.
    """
    from ddflow import config as _config

    try:
        return root.resolve() == _config._CODE_TREE
    except OSError:
        return False
