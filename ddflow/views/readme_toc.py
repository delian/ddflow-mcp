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
LISTED_LEVELS = (2, 3)
_HEADING = re.compile(r"^ {0,3}(#{2,3}) +(.+?) *#*$")
_ANY_HEADING = re.compile(r"^ {0,3}(#{1,6}) +(.+?) *#*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def slug(title: str) -> str:
    """GitHub's anchor for a heading: lower case, markup and punctuation dropped, spaces
    hyphenated. A repeated title gets a numeric suffix from `anchors`."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", title)  # a link keeps its text
    text = text.replace("`", "").lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def headings(readme: str, pattern: re.Pattern[str] = _HEADING) -> list[tuple[int, str]]:
    """``(level, title)`` of each `##`/`###` heading outside a code fence, in order (any
    level with ``pattern=_ANY_HEADING``). Text between a region's markers is generated, not
    written, so it is skipped."""
    out: list[tuple[int, str]] = []
    for line in _prose_lines(readme):
        h = pattern.match(line)
        if h:
            out.append((len(h.group(1)), h.group(2)))
    return out


def _prose_lines(readme: str) -> list[str]:
    """The README's lines outside code fences and generated regions. A fence closes on a
    line of its own character, at least as long, followed only by blanks (CommonMark)."""
    out: list[str] = []
    fence = ""
    for line in _without_regions(readme).split("\n"):
        m = _FENCE.match(line)
        if m:
            run = m.group(1)
            if not fence:
                fence = run
            elif (
                run[0] == fence[0]
                and len(run) >= len(fence)
                and re.fullmatch(f" {{0,3}}{re.escape(run[0])}+[ \t]*", line)
            ):
                fence = ""
            continue
        if not fence:
            out.append(line)
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
    used: set[str] = set()
    out = []
    for title in titles:
        base = slug(title)
        n = seen.get(base, 0)
        anchor = base if n == 0 else f"{base}-{n}"
        while anchor in used:  # `Foo`, `Foo-1`, `Foo`: the third is `foo-2`, not a second `foo-1`
            n += 1
            anchor = f"{base}-{n}"
        seen[base] = n + 1
        used.add(anchor)
        out.append(anchor)
    return out


def render(readme: str) -> str:
    """The region, markers included, for ``readme``'s headings."""
    # GitHub numbers repeats over every heading, so number over all of them, then list ours
    every = headings(readme, _ANY_HEADING)
    links = anchors([t for _lv, t in every])
    lines = [
        f"{'  ' * (lv - 2)}- [{t}](#{a})"
        for (lv, t), a in zip(every, links, strict=True)
        if lv in LISTED_LEVELS and t != TOC_TITLE
    ]
    return _REGION.block("\n".join(lines))


def dead_anchors(readme: str) -> list[str]:
    """In-page links (`](#anchor)`) that match no heading of any level."""
    known = set(anchors([t for _lv, t in headings(readme, _ANY_HEADING)]))
    prose = "\n".join(_prose_lines(readme))
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
