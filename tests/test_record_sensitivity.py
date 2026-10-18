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


def test_an_unknown_held_label_is_no_floor_and_does_not_crash(repo):
    R.record_add(repo, KIND, "r1", {"title": "plain"})
    from ddflow.api import defs as DEFS

    DEFS.def_record(repo, KIND.def_kind, "r1", {"title": "plain", "sensitivity": "gone"})
    assert R.record_edit(repo, KIND, "r1", {"description": "again"}).exit == 0
    assert "sensitivity" not in _fields(repo, "r1") or _fields(repo, "r1")["sensitivity"] != "gone"


def test_the_machine_name_raises_the_label_to_restricted(repo):
    import socket

    host = socket.gethostname()
    R.record_add(repo, KIND, "r1", {"title": f"runs on {host}"})
    assert _fields(repo, "r1").get("sensitivity") == "restricted"


def test_revise_with_the_scan_off_keeps_the_held_label(repo):
    R.record_add(repo, KIND, "r1", {"title": "t", "sensitivity": "restricted"})
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "config.toml").write_text("[sensitivity]\nscan_at_write = false\n")
    assert R.record_revise(repo, KIND, "r1", {"title": "t2"}, reason="x").exit == 0
    assert _fields(repo, "r1")["sensitivity"] == "restricted"
