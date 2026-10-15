"""Markdown structure for the documentation check: fenced code, headings and anchors.

Split from ``docscheck`` (rule r-small-files). Stdlib only; reads text, never the tree.
"""

from __future__ import annotations

import re
from collections import Counter

from ..core.slug import github_anchor

_FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
#: A line that cannot continue a paragraph: a list item, a quote or a table row.
_NOT_PARAGRAPH = re.compile(
    r"^\s*(?:[-*+]\s|\d+[.)]\s|>|\||(?:\*\s*){3,}$|(?:-\s*){3,}$|(?:_\s*){3,}$)"
)
_SETEXT = re.compile(r"^\s{0,3}(=+|-+)\s*$")
SPAN = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)")
_ATX = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_HTML_ANCHOR = re.compile(r"""<a\s[^>]*?\b(?:name|id)\s*=\s*["']([^"']+)["']""", re.I)


# ---------------------------------------------------------------------------------------
# headings


slugify = github_anchor  # kept under its old name for callers and tests


class Headings:
    def __init__(self, root, texts: dict[str, str]) -> None:
        self.root = root
        self.texts = texts
        self._cache: dict[str, set[str] | None] = {}

    def of(self, path: str) -> set[str] | None:
        """The anchors ``path`` offers; None when it cannot be read (could not tell)."""
        if path not in self._cache:
            text = self.texts.get(path)
            if text is None:
                try:
                    text = (self.root / path).read_text("utf-8", errors="replace")
                except OSError:
                    self._cache[path] = None
                    return None
            self._cache[path] = anchors_of(text)
        return self._cache[path]


def lines(text: str):
    """(number, line, in_code) for each line; fence lines themselves are in_code. A fence
    closes only on a run of the SAME character at least as long as the one that opened it
    and carrying no info string (CommonMark), so a short fence inside a long one is code."""
    opened: tuple[str, int] | None = None
    for n, line in enumerate(text.splitlines(), 1):
        m = _FENCE.match(line)
        if opened is None:
            if m and not ("`" in m.group(2) and m.group(1)[0] == "`"):
                opened = (m.group(1)[0], len(m.group(1)))
            yield n, line, opened is not None
        else:
            yield n, line, True
            if (
                m
                and m.group(1)[0] == opened[0]
                and len(m.group(1)) >= opened[1]
                and not m.group(2).strip()
            ):
                opened = None


def anchors_of(text: str) -> set[str]:
    """Every anchor a markdown document offers, lower-cased: heading slugs (ATX and
    setext; GitHub numbers repeats `-1`, `-2`, ...) and explicit `<a name=...>` / `id=...`
    targets."""
    out: set[str] = set()
    seen: Counter = Counter()
    taken: set[str] = set()  #: heading slugs only: an explicit anchor never bumps a heading

    def add(heading: str) -> None:
        base = slug = slugify(heading)
        while slug in taken:  # github-slugger: bump the suffix until the candidate is free
            seen[base] += 1
            slug = f"{base}-{seen[base]}"
        taken.add(slug)
        out.add(slug)

    paragraph: list[str] = []  #: prose lines so far; a setext underline makes them a heading
    for _, line, code in lines(text):
        if code:
            paragraph = []
            continue
        h = _ATX.match(line)
        if h:
            add(h.group(2))
            paragraph = []
        elif paragraph and _SETEXT.match(line):
            add(" ".join(paragraph))
            paragraph = []
        elif line.strip() and not (_SETEXT.match(line) or _NOT_PARAGRAPH.match(line)):
            paragraph.append(line.strip())
        else:
            paragraph = []
        out.update(a.lower() for a in _HTML_ANCHOR.findall(line))
    return out


def prose_only(text: str) -> str:
    """``text`` with fenced code lines and inline code spans blanked, same length and same
    line numbers: what is left is what a reader takes as the document's own claims."""
    out = []
    for _, line, code in lines(text):
        out.append("" if code else SPAN.sub(lambda s: " " * len(s.group(0)), line))
    return "\n".join(out)
