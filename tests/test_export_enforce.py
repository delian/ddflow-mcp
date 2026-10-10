"""B-export-enforce (decision D-export): a staged exported document must equal a fresh
export; a hand-written file that only holds a marker region is not a generated file;
doctor notes a stale or hand-edited target; docscheck and stale_docs ignore targets; a
registered export target draws no 'no merge strategy' note.

Through a real `git commit` and the installed hook, like test_generated_views.py."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git_proc as _git

from ddflow.config import Config
from ddflow.core.schedule import shared_globs
from ddflow.services import docscheck
from ddflow.services import shared_files as SF

DOC = "ROADMAP.md"


def _commit(repo: Path, *paths: str) -> subprocess.CompletedProcess:
    _git(repo, "add", *paths)
    return subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "docs"],
        capture_output=True,
        text=True,
        env={**os.environ},
        timeout=180,
    )


def _config(repo: Path, mode: str | None, extra: str = "") -> None:
    body = '[enforce]\ncommit_without_lease = "off"\n'
    if mode:
        body += f'generated_views = "{mode}"\n'
    body += '\n[export]\ndocuments = ["roadmap"]\n' + extra
    (repo / ".ddflow" / "config.toml").write_text(body)


@pytest.fixture
def proj(repo):
    run_cli(repo, "adopt", "--agents", "claude")
    _config(repo, None)
    run_cli(repo, "phase", "add", "P1", "--title", "Core")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "src/a.py")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "scaffold", "--no-verify")
    return repo


def _export(repo: Path) -> None:
    assert run_cli(repo, "export", "roadmap", "--update", "--yes")[0] == 0
    assert (repo / DOC).read_text().startswith("<!-- ddflow:generated doc=roadmap")


def _move_the_log(repo: Path) -> None:
    assert run_cli(repo, "task", "add", "P1.T2", "--phase", "P1", "--globs", "src/b.py")[0] == 0


def test_a_fresh_exported_target_commits(proj):
    _export(proj)
    r = _commit(proj, DOC, ".ddflow")
    assert r.returncode == 0, r.stderr


def test_a_stale_staged_target_is_refused_under_block(proj):
    _export(proj)
    _move_the_log(proj)
    r = _commit(proj, DOC, ".ddflow")
    assert r.returncode != 0, "a stale exported document was committed"
    assert DOC in r.stderr and "stale" in r.stderr
    assert "ddflow export roadmap --update" in r.stderr


def test_a_stale_staged_target_only_warns_under_warn(proj):
    _config(proj, "warn")
    _export(proj)
    _move_the_log(proj)
    r = _commit(proj, DOC, ".ddflow")
    assert r.returncode == 0, r.stderr
    assert DOC in r.stderr and "warning only" in r.stderr


def test_off_checks_nothing(proj):
    _config(proj, "off")
    _export(proj)
    _move_the_log(proj)
    assert _commit(proj, DOC, ".ddflow").returncode == 0


def test_a_hand_edited_staged_target_is_refused(proj):
    _export(proj)
    p = proj / DOC
    p.write_text(p.read_text() + "\n- a line nobody generated\n")
    r = _commit(proj, DOC, ".ddflow")
    assert r.returncode != 0
    assert DOC in r.stderr and "hand-edited" in r.stderr


def test_the_event_log_must_be_fully_staged(proj):
    _export(proj)
    _move_the_log(proj)
    run_cli(proj, "export", "roadmap", "--update", "--yes")
    r = _commit(proj, DOC)  # the log shard that moved is not staged
    assert r.returncode != 0
    assert "event log" in r.stderr


def test_a_hand_written_file_with_a_region_is_not_a_generated_file(proj):
    _config(proj, None, '\n[export.roadmap]\npath = "NOTES.md"\nmode = "region"\n')
    assert Config.load(proj).export.targets() == [("roadmap", "NOTES.md", "region")]
    notes = proj / "NOTES.md"
    notes.write_text(
        "# Notes\n\nmy words\n\n<!-- ddflow:begin doc=roadmap body-sha256=000000000000 -->\n"
        "stale region\n<!-- ddflow:end doc=roadmap -->\n"
    )
    _move_the_log(proj)
    r = _commit(proj, "NOTES.md", ".ddflow")
    assert r.returncode == 0, r.stderr
    # its hand-written text is still scanned by stale_docs / docscheck: only whole files are excluded
    assert "NOTES.md" not in SF.doc_exclude(Config.load(proj))
    assert "NOTES.md" in shared_globs(Config.load(proj))


def test_a_marker_quoted_below_the_first_line_is_not_generated(proj):
    (proj / "GUIDE.md").write_text(
        "# Guide\n\n<!-- ddflow:generated doc=roadmap v=1 body-sha256=000000000000 -->\n"
    )
    assert _commit(proj, "GUIDE.md").returncode == 0


def test_doctor_notes_a_stale_target(proj):
    _export(proj)
    assert "ROADMAP.md (roadmap)" not in run_cli(proj, "doctor")[1]
    _move_the_log(proj)
    out = run_cli(proj, "doctor")[1]
    line = next(ln for ln in out.splitlines() if "ROADMAP.md (roadmap) is stale" in ln)
    assert line.strip().startswith("note:"), line  # a NOTE, not a problem


def test_doctor_notes_a_hand_edited_target(proj):
    _export(proj)
    p = proj / DOC
    p.write_text(p.read_text() + "\nedited\n")
    assert "ROADMAP.md (roadmap) is hand-edited" in run_cli(proj, "doctor")[1]


def test_export_targets_are_excluded_from_stale_docs_and_docscheck(proj):
    cfg = Config.load(proj)
    assert DOC in SF.doc_exclude(cfg)
    assert set(cfg.enforce.doc_exclude) <= set(SF.doc_exclude(cfg))
    _export(proj)
    (proj / "README.md").write_text(
        "# proj\n\nSee [nowhere](docs/missing.md) and `no_such_fn()`.\n"
    )
    (proj / DOC).write_text((proj / DOC).read_text() + "\nSee [gone](docs/also-missing.md).\n")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-qm", "docs", "--no-verify")
    rep = docscheck.check_docs(proj, docs=["README.md", DOC], generated=SF.export_targets(cfg))
    assert DOC not in rep.checked["docs"]
    assert "README.md" in rep.checked["docs"]
    # even with no config at all, a tracked file with an export header is skipped
    rep = docscheck.check_docs(proj, doc_globs=["*.md"])
    assert DOC not in rep.checked["docs"]


def test_export_targets_are_shared_and_draw_no_merge_note(proj):
    from ddflow.core.schedule import conflicts

    cfg = Config.load(proj)
    assert DOC in shared_globs(cfg)
    assert DOC not in cfg.lease.shared_globs
    # two leases on the target do not overlap: no claim on it is needed
    assert conflicts([DOC], [DOC], shared_globs(cfg)) == []
    _problems, notes = SF.findings(proj, cfg)
    assert not [n for n in notes if DOC in n], notes
    assert "no merge strategy" not in run_cli(proj, "doctor")[1]


def test_the_no_merge_note_still_fires_for_an_ordinary_shared_glob(proj):
    # control: the doctor line the test above forbids does exist for a declared shared glob
    _config(proj, None, '\n[lease]\nshared_globs = ["generated/out.toml"]\n')
    assert "no merge strategy" in run_cli(proj, "doctor")[1]


def test_a_configured_target_is_checked_whatever_its_suffix(proj):
    _config(proj, None, '\n[export.roadmap]\npath = "ROADMAP.rst"\n')
    assert run_cli(proj, "export", "roadmap", "--update", "--yes")[0] == 0
    _move_the_log(proj)
    r = _commit(proj, "ROADMAP.rst", ".ddflow")
    assert r.returncode != 0 and "ROADMAP.rst" in r.stderr
