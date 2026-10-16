"""The names a change adds, removes or renames, and the tests that spell them out (B4067086b6b).

The import graph cannot see every route to a change. A knob added to `config_sections/x.py`
is read by a test through ``Config()``; a function added to a module behind a facade is
called through the facade. Cutting the walk at a hub (`testreach`) removes the false routes
and these true ones together, so the true ones come back by what a test SAYS: a test that
spells an identifier the diff defines or deletes (``test_file_globs``, ``triage_counts``) is
selected. Only a distinctive identifier counts -- long, and with an underscore or a mixed
case -- and one that more than `[gates].unit_tests_name_max_tests` tests spell is noise (a word, not a name).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path, PurePosixPath

from ..infra import worktree as W
from .testreach import module_name

_MIN_LEN = 8
_DEFINES = re.compile(
    r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_]\w*)|^\s*([A-Za-z_]\w*)\s*(?::|=[^=])"
)


def distinctive(name: str) -> bool:
    """A name worth searching for: not a dunder, long enough, and not a plain word."""
    if len(name) < _MIN_LEN or name.startswith("__"):
        return False
    return "_" in name.strip("_") or (name != name.lower() and name != name.upper())


def _removed(lines: list[str]) -> set[str]:
    """Names a removed line defines: the diff shows them, the new file does not."""
    out: set[str] = set()
    for line in lines:
        m = _DEFINES.match(line)
        if m and distinctive(name := m.group(1) or m.group(2)):
            out.add(name)
    return out


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.M)


def _touched_lines(diff: str) -> set[int]:
    """New-side line numbers a unified diff (``-U0``) adds or edits; a pure removal marks the
    line before it and the line it left behind (git numbers the point after line N)."""
    out: set[int] = set()
    for m in _HUNK.finditer(diff):
        start, count = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
        out.update(range(start, start + count) if count else (start, start + 1))
    return out


def _enclosing(source: str, lines: set[int]) -> set[str]:
    """Distinctive names of the definitions and assignments that contain a touched line: a
    knob whose default changes is touched on a line that never spells its name."""
    try:
        module = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    out: set[str] = set()
    for node in ast.walk(module):
        end = getattr(node, "end_lineno", None)
        if end is None or not any(node.lineno <= n <= end for n in lines):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        else:
            continue
        out.update(n for n in names if distinctive(n))
    return out


def _diff(tree: Path, *args: str) -> str:
    r = W.git(tree, "diff", "-U0", "--no-color", "--no-renames", *args)
    return r.out if r.code == 0 else ""


def changed_identifiers(
    tree: Path, base: str, files: list[str], tip: str = "HEAD", source: Path | None = None
) -> dict[str, str]:
    """Identifier -> a changed .py file that defines, edits or removes it: from the committed
    change (``base...tip``) and the working tree against HEAD (when ``tip`` is HEAD); every
    definition of an untracked file. ``source`` is where the new text is read (default
    ``tree``). {} when git cannot say."""
    root = source or tree
    out: dict[str, str] = {}
    for f in (f for f in files if f.endswith(".py")):
        diffs = [_diff(tree, f"{base}...{tip}", "--", f)] if base else []
        if tip == "HEAD":
            diffs.append(_diff(tree, "HEAD", "--", f))
        try:
            text = (root / f).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        tracked = W.git(tree, "ls-files", "--error-unmatch", "--", f).code == 0
        touched = set(range(1, text.count("\n") + 2)) if not tracked else set()
        removed: list[str] = []
        for d in diffs:
            touched |= _touched_lines(d)
            removed += [ln[1:] for ln in d.splitlines() if ln[:1] == "-" and ln[:3] != "---"]
        for name in _enclosing(text, touched) | _removed(removed):
            out.setdefault(name, f)
    return out


def spelling_tests(
    idents: dict[str, str], sources: dict[str, str], max_tests: int = 30
) -> dict[str, str]:
    """Test path -> why, for each test whose text spells a changed identifier as a whole word."""
    found: dict[str, list[str]] = {}
    for name in idents:
        pat = re.compile(rf"(?<![\w]){re.escape(name)}(?![\w])")
        hit = [t for t, text in sources.items() if name in text and pat.search(text)]
        if 0 < len(hit) <= max_tests:
            for t in hit:
                found.setdefault(t, []).append(name)
    return {t: f"names {names[0]}, changed in {idents[names[0]]}" for t, names in found.items()}


def listed_packages(files: list[str]) -> dict[str, str]:
    """Package -> an added or removed module of it, for each .py file of ``files`` (the
    dotted name and the path of its directory, both keys). A module added to or removed from a
    package changes what the package LISTS, which no import of the new name can reveal."""
    out: dict[str, str] = {}
    for f in files:
        parent = str(PurePosixPath(f).parent)
        if not f.endswith(".py") or parent == ".":
            continue
        out.setdefault(module_name(f"{parent}/__init__.py"), f)
        out.setdefault(parent, f)
        grand = str(PurePosixPath(parent).parent)
        if PurePosixPath(f).name == "__init__.py" and grand != ".":  # a new subpackage
            out.setdefault(module_name(f"{grand}/__init__.py"), f)
            out.setdefault(grand, f)
    return out


def listing_tests(
    packages: dict[str, str], sources: dict[str, str], max_tests: int = 30
) -> dict[str, str]:
    """Test path -> why, for each test that spells a changed package as a QUOTED string
    (``"ddflow.services.gates"``, ``'ddflow/api'``): a layout test, a guard that walks the
    package. An import statement is not quoted, so the tests that merely use the package are
    not taken; a package spelt that way by more than ``max_tests`` tests selects none."""
    found: dict[str, list[str]] = {}
    for pkg in packages:
        pat = re.compile(rf"""["']{re.escape(pkg)}/?["']""")
        hit = [t for t, text in sources.items() if pkg in text and pat.search(text)]
        if 0 < len(hit) <= max_tests:
            for t in hit:
                found.setdefault(t, []).append(pkg)
    return {
        t: f"lists package {p[0]} (module {packages[p[0]]} added or removed)"
        for t, p in found.items()
    }
