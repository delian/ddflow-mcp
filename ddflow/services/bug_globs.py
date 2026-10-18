"""The files a fix task's bugs declared (`bug found --globs`)."""

from __future__ import annotations

import re
from fnmatch import fnmatch
from posixpath import basename

from ..config import Config
from ..core.model import State
from .completion import fixes_of


def bug_globs_of(state: State, item_id: str, cfg: Config | None = None) -> list[str]:
    """The files the bugs ``item_id`` fixes declared (`bug found --globs`), in bug order:
    what a claim of a fix task with none of its own covers, so the commit hook does not
    refuse the very fix the bug names."""
    out: list[str] = []
    for b in sorted(fixes_of(state, item_id, cfg)):
        out += [g for g in state.bugs[b].globs if g not in out]
    return out


_EXTS = "py|md|toml|json|ya?ml|sh|txt|cfg|conf|ini|lock|js|ts|rs|go|c|h|html|css|sql|xml|rb|java"
_PATHISH = re.compile(
    rf"[\w.*-]+(?:/[\w*-]+)*/[\w*-]*(?:\*|\.[A-Za-z]\w{{0,4}}\b)[\w*-]*(?:\.[A-Za-z]\w*)*"
    rf"|[\w-]+\.(?:{_EXTS})\b"
)


def _names(path: str, glob: str) -> bool:
    return (
        fnmatch(path, glob)
        or glob.endswith("/" + path)
        or path.endswith("/" + glob)
        or ("/" not in path and fnmatch(path, basename(glob)))
    )


def inherit_globs(source_globs: list[str], text: str, *, tool_bug: bool = False) -> list[str]:
    """The source item's globs a fix task for a bug described by ``text`` inherits.

    Text naming no file inherits them all (the bug is in what the item touched). Text that
    names files (a known extension, or a path) keeps only the source globs it names -- none, when it names others -- so a
    bug found beside ``claim.py`` but about ``merge.py`` is not given ``claim.py``.

    ``tool_bug``: the bug is in ddflow itself (`--scope ddflow`), noticed while working on the
    item, not in what the item touched, and the files its text quotes are as often the symptom
    as the cause, so NOTHING is inherited: the globs of an unrelated item made a fix task wait
    for, or write over, files it had no business with (Bc0b26248e3). `--globs` names its files."""
    if tool_bug:
        return []
    named = _PATHISH.findall(text)
    if not named:
        return list(source_globs)
    return [g for g in source_globs if any(_names(p, g) for p in named)]
