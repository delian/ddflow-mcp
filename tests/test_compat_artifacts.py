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
    assert M.state(t, version="0.1.4", fmt=1) == "newer"
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
    # nothing was written: the caller still has the original text
    assert "one" in t


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
    assert not M.owns("see `<!-- ddflow:begin doc/main -->` in prose\n")
