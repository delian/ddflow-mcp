"""`[export].refresh`: selected documents regenerate at merge, phase close and the docs gate.

Decisions D-export, D-export-selection. Off writes nothing; a hand-edited target is skipped
with a note; a refresh error never fails the merge; a per-document setting wins.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api._base import _load
from ddflow.infra import worktree as W
from ddflow.services.export import frame as F
from ddflow.services.export import refresh as RF

OK = 0


def _git(tree: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", "-C", str(tree), *argv], check=True, capture_output=True, text=True
    ).stdout


def _config(repo: Path, toml: str) -> None:
    p = repo / ".ddflow" / "config.toml"
    p.write_text(p.read_text() + "\n" + toml)


@pytest.fixture
def proj(repo: Path) -> Path:
    """T1 is open and claimed with its own worktree; ROADMAP.md is written and committed,
    then made stale by a later task."""
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "Billing", "--globs", "src/**")
    run_cli(repo, "task", "add", "T1", "--phase", "P1", "--title", "first", "--globs", "src/**")
    _config(repo, '[export]\ndocuments = ["roadmap"]\n')
    assert run_cli(repo, "export", "roadmap", "--update")[0] == OK
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    assert run_cli(repo, "claim", "T1")[0] == OK
    run_cli(repo, "task", "add", "T2", "--phase", "P1", "--title", "second one", "--globs", "x/**")
    return repo


def _tree(repo: Path) -> Path:
    return W.load_path(repo, _load(repo)[2].items["T1"].worktree)


def _work(tree: Path) -> None:
    (tree / "src").mkdir(exist_ok=True)
    (tree / "src" / "a.py").write_text("x = 1\n")
    _git(tree, "add", "src/a.py")
    _git(tree, "commit", "-qm", "work")


def _merge(repo: Path) -> tuple[int, dict]:
    _work(_tree(repo))
    code, out, err = run_cli(repo, "--json", "merge", "T1", "--allow-dirty")
    return code, (json.loads(out) if out.strip().startswith("{") else {"raw": out + err})


def _stale(repo: Path) -> bool:
    return run_cli(repo, "export", "roadmap", "--check")[0] != OK


def test_refresh_merge_updates_a_stale_roadmap_in_the_merge(proj):
    p = proj / ".ddflow" / "config.toml"
    p.write_text(
        p.read_text().replace(
            'documents = ["roadmap"]', 'documents = ["roadmap"]\nrefresh = "merge"'
        )
    )
    assert _stale(proj)
    code, out = _merge(proj)
    assert code == OK, out
    assert "second one" in _git(proj, "show", "main:ROADMAP.md")
    assert "ROADMAP.md" in _git(proj, "log", "-m", "--name-only", "--format=", "-n", "3", "main")
    er = out["export_refresh"]
    assert er["changed"] == ["ROADMAP.md"] and er["digests"]["roadmap"]


def test_refresh_off_leaves_it(proj):
    code, out = _merge(proj)
    assert code == OK, out
    assert "second one" not in _git(proj, "show", "main:ROADMAP.md")
    assert "export_refresh" not in out


def test_a_hand_edited_target_is_skipped_with_a_note(proj):
    p = proj / ".ddflow" / "config.toml"
    p.write_text(
        p.read_text().replace(
            'documents = ["roadmap"]', 'documents = ["roadmap"]\nrefresh = "merge"'
        )
    )
    tree = _tree(proj)
    road = tree / "ROADMAP.md"
    road.write_text(road.read_text() + "\nmy own words\n")
    _git(tree, "add", "ROADMAP.md")
    _git(tree, "commit", "-qm", "hand edit")
    code, out = _merge(proj)
    assert code == OK, out
    er = out["export_refresh"]
    assert er["changed"] == [] and er["documents"][0]["action"] == "skipped"
    assert "hand-edited" in er["documents"][0]["message"]
    assert "my own words" in (proj / "ROADMAP.md").read_text()


def test_a_refresh_error_does_not_fail_the_merge(proj, monkeypatch):
    p = proj / ".ddflow" / "config.toml"
    p.write_text(
        p.read_text().replace(
            'documents = ["roadmap"]', 'documents = ["roadmap"]\nrefresh = "merge"'
        )
    )
    from ddflow.services.export import ops

    def boom(*a, **k):
        raise RuntimeError("template exploded")

    monkeypatch.setattr(ops, "write_doc", boom)
    _work(_tree(proj))
    from ddflow import api

    out = api.merge_item(proj, "T1", allow_dirty=True)
    assert out.exit == OK, out.reason
    doc = out.data["export_refresh"]["documents"][0]
    assert doc["action"] == "failed" and "template exploded" in doc["message"]


def test_a_per_document_setting_wins(proj):
    p = proj / ".ddflow" / "config.toml"
    p.write_text(
        p.read_text().replace(
            'documents = ["roadmap"]', 'documents = ["roadmap"]\nrefresh = "merge"'
        )
        + '\n[export.roadmap]\nrefresh = "off"\n'
    )
    code, out = _merge(proj)
    assert code == OK, out
    assert "second one" not in _git(proj, "show", "main:ROADMAP.md")
    # and the other way: global off, this document merge
    cfg = (
        (proj / ".ddflow" / "config.toml")
        .read_text()
        .replace('refresh = "off"', 'refresh = "docs_gate"')
    )
    (proj / ".ddflow" / "config.toml").write_text(cfg)
    r = RF.refresh_selected(proj, "docs_gate")
    assert [o.doc for o in r.outcomes] == ["roadmap"]


def test_docs_gate_regenerates_verifies_and_records_digests(proj):
    p = proj / ".ddflow" / "config.toml"
    p.write_text(p.read_text() + '\n[export.roadmap]\nrefresh = "docs_gate"\n')
    r = RF.refresh_selected(proj, "docs_gate")
    assert [(o.doc, o.action, o.verified) for o in r.outcomes] == [("roadmap", "updated", True)]
    head, _ = F.split((proj / "ROADMAP.md").read_text())
    ev = r.evidence()
    assert ev["digests"] == {"roadmap": head.digest} and ev["documents"][0]["path"] == "ROADMAP.md"
    assert RF.refresh_selected(proj, "docs_gate").outcomes[0].action == "unchanged"
    # another trigger selects nothing for this document
    r2 = RF.refresh_selected(proj, "phase_close")
    assert r2.outcomes == [] and "nothing to do" in r2.note


def test_nothing_selected_is_a_noop_that_says_so(repo):
    run_cli(repo, "init")
    r = RF.refresh_selected(repo, "docs_gate")
    assert r.outcomes == [] and "no documents are selected" in r.note and r.changed == []
    assert RF.refresh_selected(repo, "off").outcomes == []


def test_phase_close_regenerates_only_documents_set_to_it(proj):
    p = proj / ".ddflow" / "config.toml"
    p.write_text(p.read_text() + '\n[export.roadmap]\nrefresh = "phase_close"\n')
    from ddflow import api

    assert _stale(proj)
    out = api.complete(proj, "P1", force=True)
    assert out.exit == OK, out.reason
    assert out.data["export_refresh"]["changed"] == ["ROADMAP.md"]
    assert not _stale(proj)


def test_the_cadence_lists_a_stale_opted_in_document(proj):
    from ddflow.services.cadence import export_cadence

    cfg = _load(proj)[1]
    assert export_cadence(proj, cfg) == []  # refresh off: not asked about
    p = proj / ".ddflow" / "config.toml"
    p.write_text(p.read_text() + '\n[export.roadmap]\nrefresh = "merge"\n')
    cfg = _load(proj)[1]
    due = export_cadence(proj, cfg)
    assert due and due[0]["cadence"] == "export_refresh" and "roadmap" in due[0]["since"]


def test_a_failed_refresh_commit_removes_a_document_it_created(proj, monkeypatch):
    p = proj / ".ddflow" / "config.toml"
    p.write_text(
        p.read_text().replace(
            'documents = ["roadmap"]', 'documents = ["roadmap", "status"]\nrefresh = "merge"'
        )
    )
    tree = _tree(proj)
    _work(tree)
    real = W.git

    def git(repo, *args, **kw):
        if "commit" in args and "refresh generated documents" in args:
            return W.GitResult(1, "", "gpg failed")
        return real(repo, *args, **kw)

    monkeypatch.setattr(W, "git", git)
    from ddflow import api

    out = api.merge_item(proj, "T1", allow_dirty=True, keep=True)
    assert out.exit == OK, out.reason
    assert "could not commit" in out.data["export_refresh"]["summary"]
    assert not (tree / "STATUS.md").exists()
    assert _git(tree, "status", "--porcelain").strip() == ""


def test_one_documents_bad_settings_do_not_block_the_others(proj):
    p = proj / ".ddflow" / "config.toml"
    text = p.read_text().replace(
        'documents = ["roadmap"]', 'documents = ["roadmap", "status", "rules"]\nrefresh = "merge"'
    )
    p.write_text(
        text + '\n[export.status]\npath = "../outside.md"\n\n[export.rules]\nrefresh = "off"\n'
    )
    r = RF.refresh_selected(proj, "merge")
    by = {o.doc: o for o in r.outcomes}
    assert set(by) == {"roadmap", "status"}  # rules is off: never resolved
    assert by["roadmap"].action == "updated" and by["status"].action == "failed"


def test_recording_the_docs_gate_runs_the_export_step_and_keeps_digests(proj):
    p = proj / ".ddflow" / "config.toml"
    p.write_text(p.read_text() + '\n[export.roadmap]\nrefresh = "docs_gate"\n')
    assert _stale(proj)
    code, out, err = run_cli(
        proj, "--json", "gate", "record", "P1", "docs", "--outcome", "passed", "--evidence", "x"
    )
    assert code == OK, out + err
    assert not _stale(proj)
    ev = _load(proj)[2].items["P1"].gates["docs"].evidence["export"]
    head, _ = F.split((proj / "ROADMAP.md").read_text())
    assert ev["digests"] == {"roadmap": head.digest}
    assert ev["documents"][0]["verified"] is True


def test_the_cadence_says_when_it_could_not_check(proj, monkeypatch):
    from ddflow.services import cadence
    from ddflow.services.export import ops

    p = proj / ".ddflow" / "config.toml"
    p.write_text(p.read_text() + '\n[export.roadmap]\nrefresh = "merge"\n')
    cfg = _load(proj)[1]

    def boom(*a, **k):
        raise OSError("log unreadable")

    monkeypatch.setattr(ops, "load", boom)
    due = cadence.export_cadence(proj, cfg)
    assert due and "could not check" in due[0]["since"]


def test_a_failed_refresh_commit_keeps_a_document_that_already_existed_untracked(proj, monkeypatch):
    p = proj / ".ddflow" / "config.toml"
    p.write_text(
        p.read_text().replace(
            'documents = ["roadmap"]', 'documents = ["roadmap", "status"]\nrefresh = "merge"'
        )
    )
    tree = _tree(proj)
    _work(tree)
    assert run_cli(proj, "export", "status", "--out", "STATUS.md")[0] == OK  # in the primary
    (tree / "STATUS.md").write_text((proj / "STATUS.md").read_text())  # untracked, generated
    real = W.git
    monkeypatch.setattr(
        W,
        "git",
        lambda repo, *a, **k: (
            W.GitResult(1, "", "gpg failed")
            if "refresh generated documents" in a
            else real(repo, *a, **k)
        ),
    )
    from ddflow import api

    out = api.merge_item(proj, "T1", allow_dirty=True, keep=True)
    assert out.exit == OK, out.reason
    assert (tree / "STATUS.md").exists()
