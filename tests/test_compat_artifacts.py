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
    assert text.count("body-sha256=") == 1


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


def test_a_huge_or_odd_fmt_value_is_read_without_crashing():
    assert F.parse_level("2") == 2 and F.parse_level("\u00b2") is None and F.parse_level("") is None
    assert F.parse_level("9" * 5000) > 10**6
    header = f"<!-- ddflow:generated doc=d v=1 body-sha256=000000000000 fmt={'9' * 5000} -->"
    head, _ = F.split(f"{header}\n{F.NOTICE}\n\nb\n")
    assert head is not None and F.newer(head)
    region = f"<!-- ddflow:begin doc=d body-sha256=000000000000 fmt={'9' * 5000} -->\nx\n"
    assert W._begin_attrs(region, "d")[0] > 10**6
    assert W._is_newer(region, "d")


def test_the_changelog_cut_dry_run_refuses_what_the_real_cut_refuses(tmp_path, monkeypatch):
    from ddflow.services import changelog_cut as C

    monkeypatch.setattr(F, "FORMAT_LEVEL", 2)
    region = W.region_text(C.DOC, "future\n")
    monkeypatch.setattr(F, "FORMAT_LEVEL", 1)
    (tmp_path / "CHANGELOG.md").write_text("# Notes\n\n" + region)
    (tmp_path / ".ddflow" / "local").mkdir(parents=True)
    apply = C._region("CHANGELOG.md", "unreleased\n", "## [1.0.0]\n- x\n", "1.0.0", {})
    with pytest.raises(W.Refused, match="upgrade ddflow before"):
        apply(tmp_path, force=False, dry=True)


def test_managed_reads_a_huge_fmt_without_crashing():
    t = f"<!-- ddflow:begin doc/main ddflow=0.1.5 fmt={'9' * 5000} sha=abc -->\nx\n<!-- ddflow:end doc/main -->\n"
    assert M.stamp(t).fmt > 10**6 and M.state(t) == "newer"
    with pytest.raises(NewerContent):
        M.splice(t, "y\n")


# --- B-uni-compat-artifacts.5-views: generated views, the gitignore, drift wording ------


def test_a_view_is_stamped_with_a_body_digest_and_no_version():
    from ddflow.views import markdown as MD

    text = MD.stamp_view(MD.GENERATED + "\n\n# Work queue\n")
    first = text.splitlines()[0]
    assert first.startswith(MD.GENERATED_PREFIX) and "body-sha256=" in first
    assert "ddflow=" not in first and "fmt=" not in first  # level 1 adds nothing
    head, body = MD.split_view(text)
    assert head is not None and head.fmt == 1 and body == "\n# Work queue\n"
    assert MD.view_difference(text, text) == ""


def test_a_higher_format_level_is_written_as_fmt():
    from ddflow.views import markdown as MD

    text = MD.stamp_view(MD.GENERATED + "\nbody\n", fmt=2)
    assert " fmt=2 -->" in text.splitlines()[0]
    assert MD.split_view(text)[0].fmt == 2


def test_view_freshness_compares_body_and_state_not_the_first_line():
    from ddflow.views import markdown as MD

    want = MD.stamp_view(MD.GENERATED + "\nsame\n")
    assert MD.view_difference(MD.GENERATED + "\nsame\n", want) == ""  # unstamped, same body
    assert MD.view_difference(MD.stamp_view(MD.GENERATED + "\nsame\n", fmt=1), want) == ""
    assert MD.view_difference(MD.stamp_view(MD.GENERATED + "\nother\n"), want) == "stale"
    assert MD.view_difference(want.replace("same", "hand"), want) == "edited"
    newer = MD.stamp_view(MD.GENERATED + "\nother\n", fmt=2)
    assert MD.view_difference(newer, want) == "newer"
    # the same body at a higher level needs no regeneration
    assert MD.view_difference(MD.stamp_view(MD.GENERATED + "\nsame\n", fmt=2), want) == ""


def test_rendering_does_not_downgrade_a_view_a_newer_format_wrote(tmp_path):
    from ddflow.config import Config
    from ddflow.core.model import State
    from ddflow.infra.fsio import NewerContent
    from ddflow.views import markdown as MD

    d = tmp_path / "docs" / "ddflow"
    d.mkdir(parents=True)
    (d / "QUEUE.md").write_text(MD.stamp_view(MD.GENERATED + "\nfrom the future\n", fmt=2))
    with pytest.raises(NewerContent) as e:
        MD.write_views(tmp_path, State(), Config())
    assert "format level 2" in str(e.value)
    assert not (d / "LESSONS.md").exists()  # nothing was written
    assert "from the future" in (d / "QUEUE.md").read_text()


def test_the_ddflow_gitignore_is_a_region_that_keeps_a_persons_lines(tmp_path):
    from ddflow.services import adopt as AD

    gi = tmp_path / ".gitignore"
    assert AD.write_ddflow_gitignore(gi) is True
    text = gi.read_text()
    assert AD.GITIGNORE_REGION.state(text) == "current"
    assert AD.GITIGNORE_REGION._region().body(text) == AD.DDFLOW_GITIGNORE
    assert AD.write_ddflow_gitignore(gi) is False  # nothing differs: no write
    # a release alone changes no byte: an older stamp over the same body is left alone
    older = text.replace("ddflow=" + AD.GITIGNORE_REGION.stamp(text).version, "ddflow=0.0.1")
    gi.write_text(older + "mine/\n")
    assert AD.write_ddflow_gitignore(gi) is False
    assert gi.read_text() == older + "mine/\n"
    # a changed body is rewritten, the person's line stays
    gi.write_text(older.replace("local/", "locl/") + "mine/\n")
    assert AD.write_ddflow_gitignore(gi) is True
    assert "mine/\n" in gi.read_text() and "\nlocal/\n" in gi.read_text()


def test_the_whole_file_gitignore_of_an_earlier_ddflow_becomes_the_region(tmp_path):
    from ddflow.services import adopt as AD

    gi = tmp_path / ".gitignore"
    gi.write_text(AD.DDFLOW_GITIGNORE)
    assert AD.write_ddflow_gitignore(gi) is True
    assert AD.GITIGNORE_REGION.owns(gi.read_text())


def test_a_rules_difference_says_what_it_is():
    from ddflow.services import adopt as AD

    def line(**kw):
        return AD.RulesState("AGENTS.md", AD.STALE, **kw).render()

    assert "newer format level" in line(newer=True, stamped=True)
    assert "edited by hand" in line(edited=True, stamped=True)
    assert "older version" in line(stamped=True)
    unstamped = line()
    assert "older version" not in unstamped and "no version stamp" in unstamped


def test_a_staged_newer_view_gets_no_regenerate_remedy(tmp_path):
    from ddflow.config import Config
    from ddflow.infra.log import EventLog
    from ddflow.services import enforce as E
    from ddflow.views import markdown as MD

    log = EventLog(tmp_path, "t")
    staged = {"docs/ddflow/QUEUE.md": MD.stamp_view(MD.GENERATED + "\nx\n", fmt=2).encode()}
    lines = E._wrong_views(log, Config(), staged)
    text = "\n".join(lines)
    assert "newer format level" in text and "upgrade ddflow first" in text
    assert "git add" not in text and "ddflow render" not in text
