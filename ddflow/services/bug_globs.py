"""The files a fix task's bugs declared (`bug found --globs`)."""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path
from posixpath import basename

from ..config import Config
from ..core import globspec as GS
from ..core.model import State
from ..infra import git as GIT
from .completion import fixes_of
from .testselect import RANK_DECLARED, _by_name, _data_readers, _Picks, governed, is_test_file


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


def _named_files(named: list[str], paths: list[str]) -> list[str]:
    """The listed paths ``named`` tokens denote: a pattern kept whole, a path named whole,
    a pattern a path matches, or a bare name a path's basename matches (the text says
    `claim.py` for `ddflow/api/lifecycle/claim.py`). One tell per token, in order."""
    out: list[str] = []
    for t in named:
        stripped = t.removeprefix("./")
        if any(c in stripped for c in "*?["):
            out.append(stripped)  # a glob the text names as such: kept, existing_globs judges it
            continue
        for p in paths:
            if "/" not in stripped:
                if fnmatch(basename(p), stripped):
                    out.append(p)
                    break
            elif p == stripped or fnmatch(p, stripped) or (p.endswith("/" + stripped)):
                out.append(p)
                break
    return out


def found_scope(root: Path, text: str) -> tuple[list[str], list[str]] | None:
    """``(kept, dropped)``: the bug's OWN found scope -- the files its text names that exist
    in the tree, plus the tests pinning them (by-name, GOVERNS and data readers, the cheap
    half of `testselect`: the import graph is too slow for every `bug found`) -- the default
    globs of a fix task for a bug naming few files (B45b80286d2). None when the tree cannot
    be listed: the caller falls back to the inherited globs."""
    listed = GIT.files(root, "all")
    if listed is None:
        return None
    files = _named_files(_PATHISH.findall(text), listed)  # a named test file is the scope too
    kept, dropped = existing_globs(root, files)
    tests = [p for p in listed if is_test_file(p) and (root / p).is_file()]
    pick = _Picks()
    _by_name(kept, tests, pick)
    pick.many(_data_readers(root, kept, tests), RANK_DECLARED)
    pick.many(governed(root, kept, tests), RANK_DECLARED)
    return kept + [t for t in sorted(pick.by) if t not in kept], dropped


def existing_globs(root: Path, globs: list[str]) -> tuple[list[str], list[str]]:
    """``(kept, dropped)``: the globs that name something in the tree at ``root`` (a path
    that exists, or a pattern that matches one), and those that name nothing.

    A fix task's globs protect files; one guessed from a bug's text that matches no file
    protects nothing and sends the implementer off to widen it (Be305b7d6ea)."""
    kept: list[str] = []
    dropped: list[str] = []
    for g in globs:
        rel = g.lstrip("/")  # `/a.md` is anchored to the repo root, not the host's
        try:
            hit = (root / rel).exists() if not any(c in rel for c in "*?[") else any(root.glob(rel))
        except (ValueError, OSError, NotImplementedError):
            hit = True  # could not check: keep it, never call an unchecked glob empty
        (kept if hit else dropped).append(g)
    return kept, dropped


def fix_task_globs(
    root: Path,
    globs: str,
    source_globs: list[str],
    text: str,
    *,
    tool_bug: bool = False,
    named_scope_at: int = 0,
) -> tuple[list[str], list[str]]:
    """``(kept, dropped)`` for a fix task: the reporter's ``globs`` verbatim (an explicit
    scope may name a file the fix creates), else -- a bug naming at most ``named_scope_at``
    files -- the bug's own found scope (`found_scope`, B45b80286d2: two narrow sibling bugs
    no longer inherit one wide item glob and exclude each other), else the ones inherited
    from the source item, cut to those that exist in the tree (only a guess can be wrong
    about the tree)."""
    if globs:
        return GS.parse(globs), []
    named = _PATHISH.findall(text)
    if not tool_bug and named and 0 < len(named) <= named_scope_at:
        found = found_scope(root, text)
        if found is not None and found[0]:
            return found  # the bug's own files (an empty find: fall back and inherit)
    return existing_globs(root, inherit_globs(source_globs, text, tool_bug=tool_bug))
