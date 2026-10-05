"""Which sentences of an instruction file does the test suite pin? (B22)

Compressing a rulebook without knowing deletes rules silently. On the source project one
by-eye pass broke nine prose pins, because "every sentence picked read like rationale and
was a rule". ddflow ships a driver and a rulebook block, and projects adopting it keep
their own, so the question comes up wherever an instruction file is edited.

**The pins are read off the suite, not declared.** Every string literal in a Python test
file that occurs in the document marks that span as pinned. The source project matched
calls to its own `pin()` helper by name instead, and paid for it five times: an import
alias, a parametrize list, a module-level list, a stacked decorator and a module alias
each dropped real pins, and the text they held was reported free. A literal is a literal
however the test spells the call, so none of those shapes exist here.

**Every error goes in one direction.** A literal that only coincides with the document
is counted as a pin, and so is text a test lowercases before matching. Over-counting
means a compression pass re-runs one suite too many; under-counting means it deletes a
rule. So the report says what is pinned, which suites to re-run, and what is FREE — and
free is a lower bound: text nobody pinned yet may still be a rule. Read what you delete.

Docstrings are excluded. A docstring that quotes a rule asserts nothing, and counting it
would hold text no test checks.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

from ..infra.worktree import repo_relative

#: Needles shorter than this are ordinary words and would mark most of any document as
#: pinned. Twelve is the source project's measured floor.
MIN_NEEDLE_CHARS = 12

#: Where a project's suite lives, when the caller does not say.
DEFAULT_TEST_DIRS: tuple[str, ...] = ("tests", "test")

_WS = re.compile(r"\s+")


def flatten(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace runs to one space, and map each kept char to its source offset.

    A pin written across a line break (`"No\\nreviewer sees"`) must match the document
    whatever the wrapping, and a report must still name the document's own line numbers.
    """
    out: list[str] = []
    where: list[int] = []
    pos = 0
    for m in _WS.finditer(text):
        for i in range(pos, m.start()):
            out.append(text[i])
            where.append(i)
        out.append(" ")
        where.append(m.start())
        pos = m.end()
    for i in range(pos, len(text)):
        out.append(text[i])
        where.append(i)
    return "".join(out), where


def lower_aligned(text: str) -> str:
    """Lowercase WITHOUT changing the length, so offsets stay aligned.

    `"İ".lower()` is two characters; a character whose lowercase expands is kept as is
    and so only matches case-sensitively, the conservative direction.
    """
    return "".join(c.lower() if len(c.lower()) == 1 else c for c in text)


def _docstrings(tree: ast.Module) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
    return out


def literals(source: str, min_chars: int = MIN_NEEDLE_CHARS) -> set[str]:
    """Every non-docstring string literal in one module, flattened, at least `min_chars`.

    Raises `SyntaxError` for a module that does not parse — the caller must count it,
    because its pins are unknown rather than absent.
    """
    tree = ast.parse(source)
    skip = _docstrings(tree)
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            needle = flatten(node.value)[0].strip()
            if len(needle) >= min_chars:
                out.add(needle)
    return out


#: A quoted string, for a file even the tokenizer gives up on. Over-matches (a quote
#: inside a comment starts one), which only holds more text: the safe direction.
_QUOTED = re.compile(
    r'"""[\s\S]*?"""' + r"|'''[\s\S]*?'''" + r'|"(?:\\.|[^"\\\n])*"' + r"|'(?:\\.|[^'\\\n])*'"
)


def quoted(source: str, min_chars: int = MIN_NEEDLE_CHARS) -> set[str]:
    """String literals of a module that does NOT parse, docstrings included.

    A file with newer syntax than this interpreter is still a live suite under the
    project's own, and its pins were reported FREE behind a warning (B22-unparsed). The
    tokenizer needs no grammar, so it reads such a file; where it too stops, a quote
    regex covers the rest. Both over-count, never under-count.
    """
    raw: list[str] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.STRING or tok.type == getattr(tokenize, "FSTRING_MIDDLE", -1):
                raw.append(tok.string)
    except (tokenize.TokenError, SyntaxError):
        raw += _QUOTED.findall(source)
    out: set[str] = set()
    for text in raw:
        try:
            value = ast.literal_eval(text)
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            value = text.strip("rRbBuUfF").strip("\"'")
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        if isinstance(value, str):
            needle = flatten(value)[0].strip()
            if len(needle) >= min_chars:
                out.add(needle)
    return out


@dataclass
class Pin:
    """One needle found in the document, and the suites that hold it."""

    needle: str
    tests: list[str]
    lines: list[int]


@dataclass
class Report:
    document: str
    chars: int
    pinned_chars: int
    pins: list[Pin] = field(default_factory=list)
    #: Suites that hold at least one pin: the ones to re-run after editing. Those that
    #: name the document come first.
    tests: list[str] = field(default_factory=list)
    #: Contiguous unpinned stretches, longest first: (chars, first line, last line, text).
    free: list[tuple[int, int, int, str]] = field(default_factory=list)
    scanned: int = 0
    #: Test files that did not parse. Any pin in them is unknown, so `free` is a lower
    #: bound while this is non-empty.
    unparsed: list[str] = field(default_factory=list)


def test_files(repo: Path, dirs: list[str] | tuple[str, ...] = DEFAULT_TEST_DIRS) -> list[Path]:
    out: list[Path] = []
    for d in dirs:
        root = (repo / d).resolve()
        if root.is_file() and root.suffix == ".py":
            out.append(root)
        elif root.is_dir():
            out.extend(p for p in sorted(root.rglob("*.py")) if "__pycache__" not in p.parts)
    return sorted(set(out))


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def coverage(
    text: str,
    files: list[Path],
    *,
    repo: Path,
    document: str = "",
    min_chars: int = MIN_NEEDLE_CHARS,
) -> Report:
    """Mark every character of `text` that a literal in `files` occurs over.

    `document` is the path reported back; a suite whose source names its file (the last
    path component) is listed first among the suites to re-run.
    """
    name = Path(document).name if document else ""
    flat, where = flatten(text)
    hay = lower_aligned(flat)
    mask = [False] * len(flat)
    held: dict[str, set[str]] = {}
    unparsed: list[str] = []
    named: set[str] = set()

    for f in files:
        rel = repo_relative(repo, f, as_given=True) or str(f)
        try:
            source = f.read_text(encoding="utf-8")
        # OSError: a dangling symlink or an unreadable file. One bad file must not hide
        # every pin the readable ones hold (B22-symlink).
        except (UnicodeDecodeError, OSError):
            unparsed.append(rel)
            continue
        try:
            needles = literals(source, min_chars)
        except (SyntaxError, ValueError):
            # Still listed, so the operator knows its pins were LEXED, not parsed.
            unparsed.append(rel)
            needles = quoted(source, min_chars)
        if name and name in source:
            named.add(rel)
        for needle in needles:
            key = lower_aligned(needle)
            at = hay.find(key)
            while at >= 0:
                held.setdefault(needle, set()).add(rel)
                for i in range(at, at + len(key)):
                    mask[i] = True
                at = hay.find(key, at + 1)

    pins = []
    for needle, tests in sorted(held.items()):
        key = lower_aligned(needle)
        lines, at = [], hay.find(key)
        while at >= 0:
            lines.append(_line_of(text, where[at]))
            at = hay.find(key, at + 1)
        pins.append(Pin(needle, sorted(tests), sorted(set(lines))))

    suites = sorted({t for p in pins for t in p.tests}, key=lambda t: (t not in named, t))
    free = []
    start = None
    for i, pinned in enumerate([*mask, True]):
        if not pinned and start is None:
            start = i
        elif pinned and start is not None:
            run = flat[start:i]
            if run.strip():
                # Lines of the first and last NON-space characters: a run that begins on
                # the newline after a pin would otherwise start a line early (B22-freeline).
                first = start + len(run) - len(run.lstrip())
                last = i - 1 - (len(run) - len(run.rstrip()))
                free.append(
                    (
                        len(run.strip()),
                        _line_of(text, where[first]),
                        _line_of(text, where[last]),
                        run.strip(),
                    )
                )
            start = None
    free.sort(key=lambda r: (-r[0], r[1]))
    return Report(
        document=document,
        chars=len(flat),
        pinned_chars=sum(mask),
        pins=pins,
        tests=suites,
        free=free,
        scanned=len(files) - len(unparsed),
        unparsed=unparsed,
    )
