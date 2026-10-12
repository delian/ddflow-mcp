"""The README's table of contents, generated from its own headings.

The `README/toc` region (D-doc-regions) lists every `##` and `###` heading outside a code
fence, in order, as a link to its GitHub anchor, so a section added or renamed cannot be left
out of (or dangle from) a hand-kept list. tests/test_readme_toc.py fails when the README's
block is not what this renders, or when a link in the README points at no heading. Rewrite
the block with

    uv run python -m ddflow.views.readme_toc README.md
"""

from __future__ import annotations

import re
import sys

from .readme_region import Region

REGION = "README/toc"
#: The heading that carries the region: it is not listed in itself.
TOC_TITLE = "Table of contents"
_HEADING = re.compile(r"^(#{2,3}) +(.+?) *#*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def slug(title: str) -> str:
    """GitHub's anchor for a heading: lower case, markup and punctuation dropped, spaces
    hyphenated. A repeated title gets a numeric suffix from `anchors`."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", title)  # a link keeps its text
    text = text.replace("`", "").lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def headings(readme: str) -> list[tuple[int, str]]:
    """``(level, title)`` of each `##`/`###` heading outside a code fence, in order. Text
    between a region's markers is generated, not written, so it is skipped."""
    out: list[tuple[int, str]] = []
    fence = ""
    for line in _without_regions(readme).split("\n"):
        m = _FENCE.match(line)
        if m:
            run = m.group(1)
            if not fence:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence):
                fence = ""
            continue
        h = None if fence else _HEADING.match(line)
        if h:
            out.append((len(h.group(1)), h.group(2)))
    return out


def _without_regions(readme: str) -> str:
    return re.sub(
        r"^<!-- ddflow:begin [^\n]*-->\n.*?^<!-- ddflow:end [^\n]*-->$",
        "",
        readme,
        flags=re.M | re.S,
    )


def anchors(titles: list[str]) -> list[str]:
    """The anchor of each title, a repeat numbered `-1`, `-2` ... as GitHub does."""
    seen: dict[str, int] = {}
    out = []
    for title in titles:
        base = slug(title)
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.append(base if n == 0 else f"{base}-{n}")
    return out


def render(readme: str) -> str:
    """The region, markers included, for ``readme``'s headings."""
    found = [(lv, t) for lv, t in headings(readme) if t != TOC_TITLE]
    links = anchors([t for _lv, t in found])
    lines = [f"{'  ' * (lv - 2)}- [{t}](#{a})" for (lv, t), a in zip(found, links, strict=True)]
    return _REGION.block("\n".join(lines))


def dead_anchors(readme: str) -> list[str]:
    """In-page links (`](#anchor)`) that match no heading."""
    known = set(anchors([t for _lv, t in headings(readme)] + [TOC_TITLE]))
    prose = _without_regions(readme)
    prose = re.sub(r"^ {0,3}(`{3,}|~{3,}).*?^ {0,3}\1[`~]*$", "", prose, flags=re.M | re.S)
    return sorted(set(re.findall(r"\]\(#([^)\s]+)\)", prose)) - known)


_REGION = Region(REGION, "table of contents", render)
END = _REGION.end
replace = _REGION.replace
hand_edited = _REGION.hand_edited


def main(argv: list[str]) -> int:
    """Rewrite the table of contents of the README named in `argv` (default README.md)."""
    return _REGION.main(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
