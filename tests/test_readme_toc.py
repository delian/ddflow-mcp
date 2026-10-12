"""The README's table of contents is what its headings render, and no link dangles
(P-readme-structure.toc)."""

from __future__ import annotations

from pathlib import Path

from ddflow.views import readme_toc as T

README = Path(__file__).resolve().parents[1] / "README.md"


def test_the_readme_toc_is_what_the_headings_render():
    """When this fails a section was added, renamed or moved: run
    `uv run python -m ddflow.views.readme_toc README.md` and commit."""
    readme = README.read_text("utf-8")
    assert readme.count(f"<!-- ddflow:begin {T.REGION} sha=") == 1
    assert not T.hand_edited(readme)
    assert T.replace(readme, T.render(readme)) == readme, (
        "README.md's table of contents is stale: `uv run python -m ddflow.views.readme_toc README.md`"
    )


def test_no_link_in_the_readme_points_at_a_missing_heading():
    assert T.dead_anchors(README.read_text("utf-8")) == []


def test_slugs_follow_github():
    assert T.slug("How do I…?") == "how-do-i"
    assert T.slug('Recall — "have we been here before?"') == "recall--have-we-been-here-before"
    assert T.slug("The `[export]` knobs (x)") == "the-export-knobs-x"
    assert T.anchors(["Same", "Same", "Other"]) == ["same", "same-1", "other"]


def test_headings_skip_code_fences_and_generated_regions():
    text = (
        "## Real\n```\n## in a fence\n```\n### Sub\n"
        "<!-- ddflow:begin X/y sha=aaaaaaaaaaaa -->\n## generated\n<!-- ddflow:end X/y -->\n"
    )
    assert T.headings(text) == [(2, "Real"), (3, "Sub")]


def test_a_hand_edit_inside_the_region_is_noticed():
    block = T.render("## A\n\n## Table of contents\n")
    edited = block.replace("- [A](#a)", "- [A](#b)")
    assert T.hand_edited(edited) and not T.hand_edited(block)


def test_a_longer_fence_is_closed_only_by_an_equal_or_longer_one():
    text = "## A\n````\n```\n# x\n## inside\n```\n````\n## B\n"
    assert T.headings(text) == [(2, "A"), (2, "B")]
