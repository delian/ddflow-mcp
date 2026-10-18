"""Sensitivity labels on records and the scan at write (SE-sensitivity.2)."""

from __future__ import annotations

import ddflow.api.records as R

KIND = R.KINDS["risk"]


def _fields(repo, rid):
    return R.record_show(repo, KIND, rid).data["fields"]


def test_a_clean_record_carries_no_label(repo):
    assert R.record_add(repo, KIND, "r1", {"title": "plain"}).exit == 0
    assert "sensitivity" not in _fields(repo, "r1")


def test_a_declared_label_is_kept_and_checked(repo):
    assert R.record_add(repo, KIND, "r1", {"title": "t", "sensitivity": "restricted"}).exit == 0
    assert _fields(repo, "r1")["sensitivity"] == "restricted"
    bad = R.record_add(repo, KIND, "r2", {"title": "t", "sensitivity": "nope"})
    assert bad.exit != 0 and "not a level" in bad.reason


def test_the_scan_raises_the_label_for_an_email_and_a_secret(repo):
    R.record_add(repo, KIND, "r1", {"title": "ask ann@corp.example", "sensitivity": "public"})
    assert _fields(repo, "r1")["sensitivity"] == "restricted"
    R.record_add(repo, KIND, "r2", {"title": "key", "description": "password: hunter2hunter2xx"})
    assert _fields(repo, "r2")["sensitivity"] == "confidential"


def test_an_edit_never_lowers_by_scan_and_lowers_only_when_asked(repo):
    R.record_add(repo, KIND, "r1", {"title": "ask ann@corp.example"})
    assert R.record_edit(repo, KIND, "r1", {"description": "now clean"}).exit == 0
    assert _fields(repo, "r1")["sensitivity"] == "restricted"
    assert R.record_edit(repo, KIND, "r1", {"title": "clean", "sensitivity": "internal"}).exit == 0
    assert _fields(repo, "r1")["sensitivity"] == "internal"


def test_the_scan_is_a_knob(repo):
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "config.toml").write_text("[sensitivity]\nscan_at_write = false\n")
    R.record_add(repo, KIND, "r1", {"title": "ask ann@corp.example"})
    assert "sensitivity" not in _fields(repo, "r1")
