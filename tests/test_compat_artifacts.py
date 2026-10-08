"""An older ddflow meeting a region a newer one wrote (D-compat 2, B-uni-compat-artifacts):
fsio.Managed tells newer, older, edited and current apart, keeps what it does not
understand, and refuses only a write that would downgrade."""

from __future__ import annotations

import pytest

from ddflow.infra.fsio import Managed, NewerContent, RegionError

M = Managed("doc/main")
HASH = Managed("hook", open="#", close="")


def _text(body="one\n", **kw):
    return "intro\n" + M.render(body, **kw) + "outro\n"


def test_render_then_stamp_round_trips():
    t = _text(version="0.1.5", fmt=1)
    s = M.stamp(t)
    assert (s.version, s.fmt) == ("0.1.5", 1)
    assert M.owns(t) and M.version(t) == "0.1.5"
    assert M.state(t, version="0.1.5", fmt=1) == "current"


def test_absent_and_unstamped():
    assert M.state("nothing\n") == "absent" and not M.owns("nothing\n")
    legacy = "a\n<!-- ddflow:begin doc/main -->\nx\n<!-- ddflow:end doc/main -->\n"
    assert M.owns(legacy) and M.stamp(legacy) is None and M.state(legacy) == "older"


def test_older_and_newer_by_version_and_format():
    t = _text(version="0.1.5", fmt=1)
    assert M.state(t, version="0.2.0", fmt=1) == "older"
    assert M.state(t, version="0.1.4", fmt=1) == "current"  # a version alone never refuses
    assert M.state(t, version="9.9.9", fmt=2) == "older"
    assert M.state(t, version="9.9.9", fmt=0) == "newer"


def test_hand_edit_is_told_from_a_version_difference():
    t = _text().replace("one", "two")
    assert M.state(t) == "edited"


def test_splice_replaces_only_the_region_and_restamps():
    t = _text(version="0.1.5", fmt=1)
    out = M.splice(t, "two\n", version="0.2.0", fmt=1)
    assert out.startswith("intro\n") and out.endswith("outro\n")
    assert "two\n" in out and "one" not in out
    assert M.state(out, version="0.2.0", fmt=1) == "current"


def test_older_writer_never_downgrades_a_newer_region():
    t = _text(version="0.3.0", fmt=2)
    with pytest.raises(NewerContent) as e:
        M.splice(t, "old\n", version="0.1.5", fmt=1)
    assert "upgrade ddflow to >= 0.3.0" in str(e.value) and e.value.needs == "0.3.0"


def test_unknown_attributes_survive_a_same_level_rewrite():
    t = _text(version="0.1.5", fmt=1, extra=(("future", "yes"),))
    assert M.stamp(t).extra == (("future", "yes"),)
    out = M.splice(t, "two\n", version="0.1.6", fmt=1)
    assert "future=yes" in out


def test_absent_region_is_appended():
    out = M.splice("keep\n", "body\n", version="0.1.5", fmt=1)
    assert out.startswith("keep\n") and M.owns(out)


def test_hash_comment_grammar_and_broken_markers():
    t = "#!/bin/sh\n" + HASH.render("run\n", version="0.1.5", fmt=1)
    assert HASH.owns(t) and HASH.state(t, version="0.1.5", fmt=1) == "current"
    assert not M.owns(t)
    with pytest.raises(RegionError):
        M.owns(_text() + M.render("again\n"))


def test_a_marker_quoted_in_prose_is_not_a_region():
    quoted = "see `<!-- ddflow:begin doc/main -->x<!-- ddflow:end doc/main -->` in prose\n"
    assert not M.owns(quoted)


def test_a_non_ascii_digit_in_fmt_is_an_unstamped_header_not_a_crash():
    t = "<!-- ddflow:begin doc/main ddflow=0.1.5 fmt=\u00b2 sha=abc -->\nx\n<!-- ddflow:end doc/main -->\n"
    assert M.stamp(t) is None and M.state(t) == "older"


def test_unknown_attributes_are_not_carried_across_a_format_level():
    t = _text(version="0.1.5", fmt=1, extra=(("future", "yes"),))
    assert "future" not in M.splice(t, "two\n", version="0.2.0", fmt=2)


def test_a_newer_version_at_the_same_level_is_rewritable():
    t = _text(version="0.9.0", fmt=1)
    assert "two" in M.splice(t, "two\n", version="0.1.5", fmt=1)


# -- exports: the whole-file header, the region marker, the append log -----------------

from ddflow.services.export import frame as F  # noqa: E402
from ddflow.services.export import write as W  # noqa: E402


def test_a_header_at_the_implicit_level_is_byte_for_byte_what_it_was():
    text = F.frame("body\n", "roadmap", "0.1.9")
    assert text.splitlines()[0] == (
        f"<!-- ddflow:generated doc=roadmap v=0.1.9 body-sha256={F.body_digest('body' + chr(10))} -->"
    )
    assert F.split(text)[0].fmt == 1


def test_a_higher_level_is_spelled_and_read_back():
    text = F.frame("body\n", "roadmap", "9.0.0", {"last": "E1"}, fmt=2)
    head, body = F.split(text)
    assert (head.fmt, head.extra, body) == (2, {"last": "E1"}, "body\n")
    assert " fmt=2" in text.splitlines()[0]


def test_state_tells_the_cases_apart():
    cur = F.frame("b\n", "roadmap", "0.1.9", fmt=1)
    assert F.state(None, "roadmap", "0.1.9") == "absent"
    assert F.state("plain\n", "roadmap", "0.1.9") == "not-ours"
    assert F.state(cur, "roadmap", "0.1.9", 1) == "current"
    assert F.state(cur, "roadmap", "0.2.0", 1) == "older"
    assert F.state(cur, "roadmap", "0.1.5", 1) == "current"  # a version alone is never newer
    assert F.state(cur, "bugs", "0.1.9", 1) == "other-doc"
    assert F.state(cur.replace("b\n", "edit\n"), "roadmap", "0.1.9", 1) == "edited"
    assert F.state(F.frame("b\n", "roadmap", "9.0.0", fmt=2), "roadmap", "0.1.9", 1) == "newer"


def _newer_file(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "FORMAT_LEVEL", 2)
    text = F.frame("future body\n", "roadmap", "9.0.0")
    monkeypatch.setattr(F, "FORMAT_LEVEL", 1)
    (tmp_path / "ROADMAP.md").write_text(text)
    return text


@pytest.mark.parametrize("force", [False, True])
def test_an_older_writer_refuses_to_rewrite_a_newer_whole_file(tmp_path, monkeypatch, force):
    text = _newer_file(tmp_path, monkeypatch)
    doc = F.frame("mine\n", "roadmap", "0.1.9")
    with pytest.raises(W.Refused) as e:
        W.write_whole(tmp_path, "ROADMAP.md", doc, force=force)
    assert "upgrade ddflow to >= 9.0.0" in str(e.value) and e.value.code == 3
    assert (tmp_path / "ROADMAP.md").read_text() == text
    # --check and --diff still report, and write nothing
    assert W.write_whole(tmp_path, "ROADMAP.md", doc, check=True).code == 1
    assert W.write_whole(tmp_path, "ROADMAP.md", doc, diff=True).action == "diff"


def test_an_older_writer_refuses_to_append_to_a_newer_log(tmp_path, monkeypatch):
    text = _newer_file(tmp_path, monkeypatch)
    with pytest.raises(W.Refused, match=r"upgrade ddflow to >= 9\.0\.0"):
        W.append_entries(
            tmp_path, "ROADMAP.md", "roadmap", lambda last: ("x\n", "E2"), register=False
        )
    assert (tmp_path / "ROADMAP.md").read_text() == text


def test_an_older_writer_refuses_a_newer_region_but_reads_extra_attributes(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "FORMAT_LEVEL", 2)
    region = W.region_text("changelog", "future\n")
    monkeypatch.setattr(F, "FORMAT_LEVEL", 1)
    (tmp_path / "CHANGELOG.md").write_text("# Notes\n\n" + region)
    assert W._begin_attrs(region, "changelog")[0] == 2
    with pytest.raises(W.Refused, match="upgrade ddflow before"):
        W.write_region(tmp_path, "CHANGELOG.md", "changelog", "mine\n", force=True)
    assert (tmp_path / "CHANGELOG.md").read_text() == "# Notes\n\n" + region


def test_a_current_level_region_round_trips_unchanged(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text("# Notes\n\n" + W.region_text("changelog", "a\n"))
    assert W.write_region(tmp_path, "CHANGELOG.md", "changelog", "b\n").action == "updated"
    assert W.write_region(tmp_path, "CHANGELOG.md", "changelog", "b\n").action == "unchanged"


def test_an_unknown_region_attribute_survives_a_same_level_rewrite(tmp_path):
    region = W.region_text("changelog", "a\n", {"future": "yes"})
    assert "future=yes" in region
    (tmp_path / "CHANGELOG.md").write_text("# Notes\n\n" + region)
    W.write_region(tmp_path, "CHANGELOG.md", "changelog", "b\n")
    text = (tmp_path / "CHANGELOG.md").read_text()
    assert "future=yes" in text and "\nb\n" in text


def test_check_reports_a_newer_log_stale_even_with_nothing_to_append(tmp_path, monkeypatch):
    _newer_file(tmp_path, monkeypatch)
    res = W.append_entries(
        tmp_path, "ROADMAP.md", "roadmap", lambda last: ("", last), register=False, check=True
    )
    assert (res.action, res.code) == ("stale", 1)


def test_the_changelog_cut_region_writer_refuses_a_newer_region(tmp_path, monkeypatch):
    from ddflow.services import changelog_cut as C

    monkeypatch.setattr(F, "FORMAT_LEVEL", 2)
    region = W.region_text(C.DOC, "future\n")
    monkeypatch.setattr(F, "FORMAT_LEVEL", 1)
    (tmp_path / "CHANGELOG.md").write_text("# Notes\n\n" + region)
    (tmp_path / ".ddflow" / "local").mkdir(parents=True)
    apply = C._region("CHANGELOG.md", "unreleased\n", "## [1.0.0]\n- x\n", "1.0.0", {})
    with pytest.raises(W.Refused, match="upgrade ddflow before"):
        apply(tmp_path, force=True, dry=False)
    assert (tmp_path / "CHANGELOG.md").read_text() == "# Notes\n\n" + region
