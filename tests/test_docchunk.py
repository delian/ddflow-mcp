"""The document chunker: headings, fences, size cap, regions, line endings, stability."""

from __future__ import annotations

from ddflow.core import docchunk as C

MD = """\
---
title: Guide
owner: ops
---
Intro text.

# Install

Setext Sub
----------

Run it.

```sh
# not a heading
pip install x
```

## From source

More.
"""


def ids(res):
    return [c.chunk_id for c in res.chunks]


def test_markdown_sections_paths_and_front_matter():
    res = C.chunk("g.md", MD)
    assert res.meta == {"title": "Guide", "owner": "ops"}
    assert ids(res) == [
        "g.md#~0",
        "g.md#install~0",
        "g.md#setext-sub~0",
        "g.md#setext-sub~1",
        "g.md#from-source~0",
    ]
    sub = res.chunks[2]
    assert sub.heading_path == ("Install", "Setext Sub") and sub.kind == "prose"
    assert res.chunks[3].kind == "code" and "# not a heading" in res.chunks[3].text
    assert res.chunks[4].heading_path == ("Install", "From source")
    assert (sub.line_start, sub.line_end) == (9, 12)


def test_nested_fences_hide_headings():
    text = "# A\n\n````md\n```\n# fake\n```\n````\n\n# B\n"
    res = C.chunk("a.md", text)
    assert [c.heading_path for c in res.chunks] == [("A",), ("A",), ("B",)]
    assert res.chunks[1].kind == "code" and "# fake" in res.chunks[1].text


def test_long_section_is_split_with_overlap_and_never_in_a_fence():
    para = "\n".join(f"line {i:03d} " + "x" * 30 for i in range(40))
    code = "```\n" + "\n".join("c" * 50 for _ in range(60)) + "\n```"
    res = C.chunk("l.md", f"# T\n\n{para}\n\n{code}\n", max_chars=400, overlap=100)
    prose = [c for c in res.chunks if c.kind == "prose"]
    assert len(prose) > 3 and all(len(c.text) <= 400 for c in prose)
    assert prose[1].line_start < prose[0].line_end + 1  # overlap carries lines
    fences = [c for c in res.chunks if c.kind == "code"]
    assert len(fences) == 1 and fences[0].text.startswith("```") and fences[0].text.endswith("```")
    assert len(fences[0].text) > 400  # oversize fence stays whole


def test_one_huge_line_is_cut_by_characters():
    res = C.chunk("h.txt", "y" * 1000, max_chars=300)
    assert [len(c.text) for c in res.chunks] == [300, 300, 300, 100]


def test_crlf_equals_lf_and_ids_are_stable():
    a = C.chunk("g.md", MD)
    b = C.chunk("g.md", MD.replace("\n", "\r\n"))
    assert [(c.chunk_id, c.sha256, c.line_start) for c in a.chunks] == [
        (c.chunk_id, c.sha256, c.line_start) for c in b.chunks
    ]
    assert C.chunk("g.md", MD) == a


def test_an_edit_changes_only_the_touched_chunks():
    a = C.chunk("g.md", MD)
    b = C.chunk("g.md", MD.replace("More.", "More, edited."))
    assert ids(a) == ids(b)
    changed = [x.chunk_id for x, y in zip(a.chunks, b.chunks, strict=True) if x.sha256 != y.sha256]
    assert changed == ["g.md#from-source~0"]


def test_non_utf8_is_skipped_and_reported():
    res = C.chunk("b.md", b"# ok\n\xff\xfe\n")
    assert res.chunks == () and "not UTF-8" in res.skipped and "b.md" in res.skipped
    assert C.chunk("b.md", b"# ok\n").chunks[0].heading_path == ("ok",)


def test_duplicate_headings_get_distinct_anchors():
    res = C.chunk("d.md", "# Same\n\na\n\n# Same\n\nb\n")
    assert ids(res) == ["d.md#same~0", "d.md#same-1~0"]


def test_regions_split_chunks_and_tag_ownership():
    text = (
        "# T\n\nhuman\n<!-- ddflow:begin d/s sha=x -->\ngen\n<!-- ddflow:end d/s -->\nmore human\n"
    )
    span = C.RegionSpan("d", "s", 4, 6)
    res = C.chunk("t.md", text, regions=[span], doctype="adr")
    got = [(c.region, c.ownership, c.doctype) for c in res.chunks]
    assert got == [("", "human", "adr"), ("d/s", "generated", "adr"), ("", "human", "adr")]
    assert "gen" in res.chunks[1].text and "more human" in res.chunks[2].text


def test_rst_headings_and_levels():
    text = "Title\n=====\n\nbody\n\nSub\n---\n\nmore\n\n=====\nOver\n=====\n\nx\n"
    res = C.chunk("r.rst", text)
    assert [c.heading_path for c in res.chunks] == [
        ("Title",),
        ("Title", "Sub"),
        ("Title", "Sub", "Over"),  # a new adornment style is a deeper level
    ]


def test_asciidoc_headings_listing_and_table():
    text = "= Doc\n\n== Part\n\ntext\n\n----\n== not a heading\n----\n\n|===\n|a|b\n|===\n"
    res = C.chunk("a.adoc", text)
    assert [c.kind for c in res.chunks] == ["prose", "prose", "code", "table"]
    assert res.chunks[2].heading_path == ("Doc", "Part")


def test_markdown_table_is_its_own_chunk():
    res = C.chunk("t.md", "# T\n\nintro\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\nafter\n")
    assert [c.kind for c in res.chunks] == ["prose", "table", "prose"]


def test_plain_text_and_empty():
    assert [c.chunk_id for c in C.chunk("n.txt", "one\n\ntwo\n").chunks] == ["n.txt#~0"]
    assert C.chunk("n.txt", "").chunks == ()
    assert C.detect("x.ADOC") == "adoc" and C.detect("Makefile") == "text"


def test_anchor_that_collides_with_a_suffixed_slug_is_bumped():
    res = C.chunk("d.md", "# Install\n\na\n\n# Install 1\n\nb\n\n# Install\n\nc\n")
    assert ids(res) == ["d.md#install~0", "d.md#install-1~0", "d.md#install-2~0"]
