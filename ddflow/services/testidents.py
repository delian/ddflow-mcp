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
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from ..core import unidiff
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
    # `quotepath=false`: a non-ASCII file name stays that name in the headers; the prefixes
    # are named, so `diff.mnemonicPrefix` and `diff.noprefix` of the user's config do not
    # change what `_added_removed` reads.
    flags = ("-U0", "--no-color", "--no-renames", "--src-prefix=a/", "--dst-prefix=b/")
    r = W.git(tree, "-c", "core.quotepath=false", "diff", *flags, *args)
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


def _spellers(
    keys: Iterable[str], pattern: str, sources: dict[str, str], max_tests: int
) -> dict[str, str]:
    """Test path -> the first key it spells, for every key spelt (``pattern`` with ``{}`` for
    the escaped key) by at least one and at most ``max_tests`` tests: more is a word, not a name."""
    found: dict[str, str] = {}
    for key in keys:
        pat = re.compile(pattern.format(re.escape(key)))
        hit = [t for t, text in sources.items() if key in text and pat.search(text)]
        if 0 < len(hit) <= max_tests:
            for t in hit:
                found.setdefault(t, key)
    return found


def spelling_tests(
    idents: dict[str, str], sources: dict[str, str], max_tests: int = 30
) -> dict[str, str]:
    """Test path -> why, for each test whose text spells a changed identifier as a whole word."""
    found = _spellers(idents, r"(?<![\w]){}(?![\w])", sources, max_tests)
    return {t: f"names {name}, changed in {idents[name]}" for t, name in found.items()}


_LITERAL = re.compile(r"\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)'")
_PLACEHOLDER = re.compile(r"\{[^{}]*\}|\\[ntr]")
_FLAG = re.compile(
    r"(?<![\w-])--[a-z0-9]+(?:-[a-z0-9]+)+(?![\w-])"
)  #: `--add-needs`, not `--force`
_PHRASE_WORDS = 4  #: consecutive words that make a phrase
_PHRASE_MIN = 16  #: characters a phrase has, so four short words are not one
_SHORT_WORDS = 3  #: a test often asserts on a fragment of the message, not the whole of it
_SHORT_MIN = 15  #: characters a fragment has: three short words are common English
_EDGE = " ,.;:()[]`'\""  #: punctuation a quoted fragment drops at its ends
_PHRASE_CAP = 1500  #: phrases kept per change; a rewrite of every message is not a signal


def _phrases(lines: Iterable[str]) -> set[str]:
    """Runs of ``_PHRASE_WORDS`` words inside the string literals of ``lines``, split at an
    interpolation (``{x}``) or a newline escape: the text a message is made of; and each
    command-line flag with a hyphen in its name (``--add-needs``) a literal spells."""
    out: set[str] = set()
    for line in lines:
        for m in _LITERAL.finditer(line):
            out.update(_FLAG.findall(m.group(1) or m.group(2) or ""))
            for part in _PLACEHOLDER.split(m.group(1) or m.group(2) or ""):
                words = part.split()
                for n, floor in ((_PHRASE_WORDS, _PHRASE_MIN), (_SHORT_WORDS, _SHORT_MIN)):
                    for i in range(len(words) - n + 1):
                        phrase = " ".join(words[i : i + n])
                        for form in (
                            phrase,
                            phrase.strip(_EDGE),
                        ):  # a test quotes it without the comma
                            if len(form) >= floor:
                                out.add(form)
    return out


_BATCH = 200  #: files per git call, so the command line stays short


def _added_removed(diff: str) -> dict[str, list[str]]:
    """File -> the lines a ``-U0`` diff of several files adds or removes. The file is read by
    `core.unidiff` (the one reader of a diff's headers: a quoted name, a name with a space);
    only lines after its first hunk header count, so a content line that begins ``--`` is
    never taken for a header."""
    out: dict[str, list[str]] = {}
    for section in unidiff.sections(diff):
        path = unidiff.parse_section(section).path
        rows = out.setdefault(path, [])
        _head, _, hunks = section.partition("\n@@")
        for line in hunks.splitlines()[1:] if hunks else ():
            if line[:1] in "+-":
                rows.append(line[1:])
    return out


def changed_phrases(
    tree: Path, base: str, files: list[str], tip: str = "HEAD", source: Path | None = None
) -> dict[str, str]:
    """Phrase -> a changed .py file whose string literals gained or lost it: the message a
    test asserts on, which the import graph cannot see (Bb5b8b17161: a CLI message reworded
    in ``claim_text`` broke ``test_asagent_tree.py``, reached only through a hub). From the
    committed change (``base...tip``) and the working tree against HEAD (when ``tip`` is
    HEAD); every phrase of an untracked file. {} when git cannot say. A few git calls for
    all the files, not three per file."""
    root = source or tree
    py = [f for f in files if f.endswith(".py")]
    lines: dict[str, list[str]] = {f: [] for f in py}
    untracked = set(py)
    for i in range(0, len(py), _BATCH):
        part = py[i : i + _BATCH]
        specs = ([f"{base}...{tip}"] if base else []) + (["HEAD"] if tip == "HEAD" else [])
        for spec in specs:
            for f, got in _added_removed(_diff(tree, spec, "--", *part)).items():
                if f in lines:
                    lines[f] += got
        tracked = W.git(tree, "ls-files", "-z", "--", *part)
        if tracked.code == 0:
            untracked -= set(tracked.out.split("\0"))
    out: dict[str, str] = {}
    for f in py:
        if f in untracked:
            try:
                lines[f] += (root / f).read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                pass
        for phrase in sorted(_phrases(lines[f])):
            if len(out) < _PHRASE_CAP:
                out.setdefault(phrase, f)
    return out


def phrase_tests(
    phrases: dict[str, str], sources: dict[str, str], max_tests: int = 30
) -> dict[str, str]:
    """Test path -> why, for each test whose text holds a changed phrase."""
    found = _spellers(phrases, "{}", sources, max_tests)
    return {t: f"says '{p}', changed in {phrases[p]}" for t, p in found.items()}


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
    found = _spellers(packages, r"""["']{}/?["']""", sources, max_tests)
    return {
        t: f"lists package {p} (module {packages[p]} added or removed)" for t, p in found.items()
    }
