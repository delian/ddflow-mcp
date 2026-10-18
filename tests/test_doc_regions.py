"""Named multi-region engine (B-tpl-regions, D-doc-regions): grammar, splice, legacy, macro."""

from __future__ import annotations

from pathlib import Path

import pytest

from ddflow.services.export import ExportError
from ddflow.services.export import regions as RG
from ddflow.services.export import registry as R


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".ddflow").mkdir()
    return tmp_path


def rr(name: str, body: str, doc: str = "d") -> str:
    return RG.render_region(doc, name, body)


SKELETON = (
    "# Title\n\nintro\n\n" + rr("a", "A1\n") + "\nuser text\n\n" + rr("b", "B1\n") + "\nbye\n"
)


def test_parse_reads_names_and_hashes():
    regs = RG.parse(SKELETON)
    assert [(r.doc, r.name, r.dialect) for r in regs] == [("d", "a", "new"), ("d", "b", "new")]
    assert regs[0].content == "A1\n" and not regs[0].edited


@pytest.mark.parametrize(
    "text",
    [
        rr("a", "x\n") + rr("a", "y\n"),  # duplicate
        "<!-- ddflow:begin d/a sha=000000000000 -->\nx\n",  # never closed
        "<!-- ddflow:end d/a -->\n",  # end without begin
        "<!-- ddflow:begin d/a sha=000000000000 -->\n<!-- ddflow:end d/b -->\n",  # mismatch
    ],
)
def test_bad_markers_are_refused(text):
    with pytest.raises(RG.RegionError):
        RG.parse(text)


def test_splice_replaces_by_name_and_keeps_the_rest_byte_identical():
    sp = RG.splice(SKELETON, "d", {"a": "A2\n", "b": "B1\n"})
    assert sp.updated == ("a",) and sp.unchanged == ("b",)
    assert sp.text == SKELETON.replace("A1\n", "A2\n").replace(
        RG.parse(SKELETON)[0].sha, RG.parse(sp.text)[0].sha
    )
    again = RG.splice(sp.text, "d", {"a": "A2\n", "b": "B1\n"})
    assert again.text == sp.text and again.updated == ()


def test_user_text_survives_crlf_and_missing_final_newline():
    old = SKELETON.replace("\n", "\r\n").rstrip("\r\n")
    sp = RG.splice(old, "d", {"a": "A1\n", "b": "B1\n"})
    assert sp.text == old and sp.updated == ()
    sp = RG.splice(old, "d", {"a": "A9\n", "b": "B1\n"})
    assert sp.text.count("\r\n") == old.count("\r\n") + 0 and "\n" not in sp.text.replace(
        "\r\n", ""
    )
    assert sp.text.startswith("# Title\r\n\r\nintro\r\n") and sp.text.endswith("bye")


def test_template_region_missing_from_file_is_reported_not_inserted():
    old = "top\n" + rr("a", "A1\n") + "bottom\n"
    sp = RG.splice(old, "d", {"a": "A1\n", "c": "C\n"})
    assert sp.missing == ("c",) and "d/c" not in sp.text
    assert RG.splice(old, "d", {}).orphans == ("a",)


def test_other_documents_regions_are_not_touched():
    old = rr("a", "mine\n", "d") + rr("a", "theirs\n", "other")
    sp = RG.splice(old, "d", {"a": "new\n"})
    assert rr("a", "theirs\n", "other") in sp.text


def test_legacy_dialects_are_read_and_rewritten():
    from ddflow.services.export import frame as F

    body = "old\n"
    old = (
        f"pre\n<!-- ddflow:begin doc=d body-sha256={F.body_digest(body)} fmt=2 -->\n{body}"
        "<!-- ddflow:end doc=d -->\npost\n"
    )
    sp = RG.splice(old, "d", {RG.DEFAULT_REGION: "new\n"})
    assert sp.migrated == (RG.DEFAULT_REGION,)
    assert sp.text == "pre\n" + rr(RG.DEFAULT_REGION, "new\n") + "post\n"
    bare = "x\n<!-- DDFLOW:BEGIN (managed — edits inside this block are overwritten) -->\nz\n<!-- DDFLOW:END -->\ny\n"
    sp = RG.splice(bare, "d", {"body": "n\n"}, bare_doc="d")
    assert sp.text == "x\n" + rr("body", "n\n") + "y\n" and sp.migrated == ("body",)
    assert RG.parse(bare) == []  # the bare block is only ours when the caller says so


def test_write_named_create_update_and_hand_edit(repo):
    out = RG.write_named(repo, "DOC.md", "d", SKELETON)
    assert out.action == "created" and (repo / "DOC.md").read_text() == SKELETON
    p = repo / "DOC.md"
    p.write_text(p.read_text().replace("intro", "my own intro"))  # outside a region
    r = RG.write_named(repo, "DOC.md", "d", SKELETON.replace("A1", "A2"))
    assert r.action == "updated" and "my own intro" in p.read_text() and "A2" in p.read_text()
    p.write_text(p.read_text().replace("B1", "B-hand"))
    with pytest.raises(ExportError) as e:
        RG.write_named(repo, "DOC.md", "d", SKELETON)
    assert e.value.code == 3 and "B-hand" in p.read_text()
    assert RG.write_named(repo, "DOC.md", "d", SKELETON, force=True).action == "updated"
    chk = RG.write_named(repo, "DOC.md", "d", SKELETON, check=True)
    assert chk.code == 0


def test_write_named_reports_missing_and_needs_regions(repo):
    p = repo / "DOC.md"
    p.write_text("keep\n" + rr("a", "A1\n"))
    r = RG.write_named(repo, "DOC.md", "d", SKELETON)
    assert any("d/b" in n and "not inserted" in n for n in r.notes)
    with pytest.raises(ExportError):
        RG.write_named(repo, "DOC.md", "d", "no regions here\n")


def test_region_macro_emits_markers_with_the_body_hash():
    tmpl = 'head\n{% call region("sum") %}\n{{ x }}\n{% endcall %}\n'
    out = R.render(tmpl, {"x": "hi"}, doc="d")
    assert RG.extract(out, "d") == {"sum": "\nhi\n"} or RG.extract(out, "d")["sum"].strip() == "hi"
    assert not RG.parse(out)[0].edited
    with pytest.raises(ExportError):
        R.render(tmpl, {"x": "hi"})  # no document kind: nothing to name the region by
