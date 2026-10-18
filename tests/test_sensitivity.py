"""The sensitivity vocabulary as data (SE-sensitivity.1)."""

from __future__ import annotations

import pytest

import ddflow.core.sensitivity as S
import ddflow.services.sensitivity as SL
from ddflow.core.redact import redact_text


def test_the_packaged_vocabulary_has_the_four_levels_in_order():
    v = SL.load()
    assert [lv.name for lv in v.ordered()] == ["public", "internal", "restricted", "confidential"]
    assert v.highest(["internal", "restricted", "public"]) == "restricted"


def test_restricted_and_confidential_are_not_sent_to_a_model_unless_allowed():
    v = SL.load()
    assert v.may_send("public") and v.may_send("internal")
    assert not v.may_send("restricted") and not v.may_send("confidential")
    assert v.may_send("restricted", allowed=["restricted"]) and not v.may_send(
        "confidential", ["restricted"]
    )
    with pytest.raises(KeyError):
        v.may_send("nope")


def test_the_scan_level_is_the_highest_kind_found_and_never_below_the_floor():
    v = SL.load()
    assert v.scan_level({}) == v.unlabelled == "internal"
    assert v.scan_level({}, floor="public") == "public"
    assert v.scan_level({"email": 1, "path": 2}) == "restricted"
    assert v.scan_level({"secret": 1, "email": 3}) == "confidential"
    assert v.scan_level({"secret": 0}, floor="public") == "public"
    # a kind found that the vocabulary has no entry for is NOT dropped: it is the highest level
    assert v.scan_level({"newkind": 4}, floor="public") == "confidential"
    assert v.scan_level({}, floor="confidential") == "confidential"


def test_every_scan_kind_is_one_the_redactor_counts():
    found = redact_text("TOKEN-demo 10.0.0.1 a@b.example", secret_patterns=[r"TOKEN-\w+"]).counts
    assert set(found) <= set(SL.load().scan) and "secret" in found and "email" in found


def test_a_project_file_adds_and_replaces_levels(tmp_path):
    (tmp_path / ".ddflow").mkdir()
    (tmp_path / SL.PROJECT_FILE).write_text(
        '[[level]]\nname = "secret-lab"\nrank = 4\nto_model = false\n'
        '[[level]]\nname = "internal"\nrank = 1\nto_model = false\n'
        '[scan]\nemail = "internal"\n'
    )
    v = SL.load(tmp_path)
    assert v.ordered()[-1].name == "secret-lab" and not v.may_send("internal")
    assert v.scan_level({"email": 1}) == "internal"


@pytest.mark.parametrize(
    "text",
    [
        "not toml [",
        "level = 3",
        '[[level]]\nname = "a"\nrank = 0\nto_model = true\n[[level]]\nname = "b"\nrank = 0\nto_model = true\n',
        '[[level]]\nname = "a"\nrank = -1\nto_model = true\n',
        '[[level]]\nname = "a"\nrank = 0\n',
        '[[level]]\nname = "a"\nrank = 0\nto_model = true\nextra = 1\n',
        '[[level]]\nname = "a"\nrank = 0\nto_model = true\n[scan]\nsecret = "ghost"\n',
        "other = 1",
        "",
    ],
)
def test_a_bad_vocabulary_is_refused(text):
    with pytest.raises(S.SensitivityError):
        S.parse(text)


def test_the_unlabelled_level_is_data_and_must_exist():
    text = '[[level]]\nname = "a"\nrank = 0\nto_model = true\n[[level]]\nname = "b"\nrank = 1\nto_model = true\n'
    assert S.parse('unlabelled = "b"\n' + text).scan_level({}) == "b"
    with pytest.raises(S.SensitivityError, match="unlabelled"):
        S.parse('unlabelled = "zzz"\n' + text)
