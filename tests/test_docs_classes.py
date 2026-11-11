"""README.md and its guides are registered as evergreen document types (readme-manage).

The glob-to-contract map of D-readme-split-r2 is carried by the project's doctype files:
`.ddflow/doctypes/readme.toml` (README.md) and `guide.toml` (docs/guide/*.md), each with
contract evergreen, so `ddflow docs check` validates them like any typed document.
"""

from __future__ import annotations

from pathlib import Path

from readme_corpus import GUIDES

from ddflow.services.docsync import glob_regex
from ddflow.services.doctypes import spec
from ddflow.services.doctypes.find import type_glob, types_of

ROOT = Path(__file__).resolve().parents[1]


def _types() -> dict[str, spec.DocType]:
    return {t.name: t for t in types_of(ROOT)}


def test_the_readme_is_an_evergreen_document_type():
    dt = _types()["readme"]
    assert dt.contract == "evergreen" and type_glob(dt) == "README.md"


def test_every_guide_is_named_by_the_guide_type():
    dt = _types()["guide"]
    assert dt.contract == "evergreen"
    rx = glob_regex(type_glob(dt))
    for g in GUIDES:
        assert rx.fullmatch(f"docs/guide/{g}.md"), g


def test_the_real_readme_and_guides_pass_their_types():
    from ddflow.services.doctypes.validate import validate

    types = _types()
    found = validate(types["readme"], (ROOT / "README.md").read_text("utf-8"), "README.md")
    assert not found, found
    for g in GUIDES:
        p = ROOT / "docs" / "guide" / f"{g}.md"
        bad = validate(types["guide"], p.read_text("utf-8"), f"docs/guide/{g}.md")
        assert not bad, (g, bad)


def test_the_configuration_guide_says_the_docs_are_managed():
    text = (ROOT / "docs" / "guide" / "configuration.md").read_text("utf-8")
    assert "managed documents" in text and ".ddflow/doctypes/readme.toml" in text


def test_the_enforcement_the_guide_names_exists():
    text = (ROOT / "docs" / "guide" / "configuration.md").read_text("utf-8")
    for name in (
        "test_readme_toc.py",
        "test_readme_command_table.py",
        "test_readme_agents_table.py",
        "test_readme_tool_table.py",
        "test_readme_knob_table.py",
    ):
        assert name in text and (ROOT / "tests" / name).is_file(), name
