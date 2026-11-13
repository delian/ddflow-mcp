"""Where this code runs from."""

from __future__ import annotations

import re
import sys
from pathlib import Path

#: The checkout this code was imported from: `<tree>/ddflow/config/_tree.py` -> `<tree>`.
#: For an installed ddflow it is site-packages, which is no project's root. The same
#: answer as `infra.paths.package_parent()`, recomputed because `config` is the bottom
#: layer and may import nothing (tests/test_layering.py); the module sits directly under
#: `ddflow/config/`, so `parents[2]` reaches `<tree>` and the level count cannot drift the
#: way the layered modules' did.
_CODE_TREE = Path(__file__).resolve().parents[2]

#: `ddflow/__init__.py` keeps the version as a plain `__version__ = "X.Y.Z"` literal (the
#: whole line); this matches that one declaration. Read from the FILE, never the imported
#: module: a long-lived process (the MCP server) froze `ddflow.__version__` at import time,
#: and the whole point is to notice the tree on disk outran it. `read_text` opens in
#: universal-newline mode, so a CRLF checkout's line endings are normalized to `\n` on read.
_VERSION_LINE = re.compile(r'^__version__ = "([^"]+)"$', re.MULTILINE)


def declared_version(tree: Path) -> str:
    """The version `tree` declares on disk (`tree/ddflow/__init__.py`), or "" when it
    cannot be told (an unreadable file, a non-UTF-8 one, or no literal)."""
    try:
        text = (Path(tree) / "ddflow" / "__init__.py").read_text(encoding="utf-8")
    except (OSError, ValueError):  # ValueError: not valid UTF-8 (UnicodeDecodeError)
        return ""
    found = _VERSION_LINE.search(text)
    return found.group(1) if found else ""


def _running_version() -> str:
    """The version this process imported, "" when there is none. Frozen: a long-lived
    process keeps it while the tree on disk is bumped out from under it. The same answer
    as `ddflow.core.version.running()` (which `config` may not import); keep them in step."""
    dflow = sys.modules.get("ddflow")
    return str(getattr(dflow, "__version__", "") or "")


def _is_code_tree(root: Path) -> bool:
    """Is `root` the very tree this code runs from -- not merely a parent of it?

    Equality, not a prefix: a harness tree NESTED under the primary
    (`.claude/worktrees/x`) has the primary as a prefix, and it is exactly the checkout
    whose older code meets the primary's newer config (bug B9cb7dd1c3b).

    Same tree AND same version: a long-lived process keeps the code it imported while a
    sibling session bumps the tree on disk (an MCP server running 0.2.10 against the
    shell's 0.2.11). The file and the code are then NOT one commit -- an unknown knob is
    the newer release's, not a typo -- so it must be reported, not raised. Only an EQUAL
    declared and running version stands for "one commit"; ANY mismatch (in practice: the
    running code outlived the tree) is not that commit, and greys the knob.

    ``_CODE_TREE`` is read from `ddflow.config` at call time, not this module's copy, so
    a monkeypatch of ``ddflow.config._CODE_TREE`` (which re-exports the value from here)
    takes effect in tests.
    """
    _config = sys.modules[__package__]

    try:
        if root.resolve() != _config._CODE_TREE:
            return False
    except OSError:
        return False
    # A version we cannot compare (neither side) keeps the old behaviour: same path means
    # strict -- an unknown key then can only be a typo, as it always was.
    declared = declared_version(_config._CODE_TREE)
    running = _running_version()
    return not declared or not running or declared == running
