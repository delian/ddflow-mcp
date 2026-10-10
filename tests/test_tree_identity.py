"""One tree identity in gate evidence (B-uni-tree-identity).

A gate used to record TWO identities of the tree it ran on: ``tree_sha`` (HEAD plus a
digest of the porcelain, the diff text and the untracked files) and ``source_tree`` (the exact content manifest). Now it records ``tree_sha`` alone,
derived from HEAD plus the manifest: ``<head12>+clean`` for a clean tree and
``<head12>+st:<id>`` otherwise. Evidence already in the log keeps its meaning: the legacy
fingerprint spellings, and a ``source_tree`` field beside them, are read as before.

The first half pins how recorded evidence is READ (it passes before and after); the
second half is the new identity.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from conftest import run_cli
from helpers import git as _git

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import gates as G
from ddflow.services.gates import outcomes


@pytest.fixture
def adopted(repo: Path) -> Path:
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    return repo


def _passed(repo: Path, evidence: dict) -> None:
    EventLog(repo, "agent-x").append(
        "gate.passed",
        "T1",
        {"gate": "unit_tests", "by": "agent-x", "reason": "", "evidence": evidence},
    )


def _stale(repo: Path, *, landed: str = ""):
    st = fold(EventLog(repo).read_all(), strict=False)
    return outcomes.stale_evidence_detail(st, Config.load(repo), "T1", repo, landed=landed)


def _legacy_both(repo: Path) -> dict:
    """The two fields a gate recorded before this change, taken on the tree as it is."""
    return {
        "command": "true",
        "exit": 0,
        "tree_sha": G.tree_fingerprint(repo),
        "source_tree": G.source_tree(repo),
    }


# -- reading evidence that is already in the log (pins) ------------------------------------


def test_legacy_evidence_with_both_fields_is_fresh_on_the_same_dirty_tree(adopted):
    (adopted / "a.py").write_text("a = 1\n")
    _passed(adopted, _legacy_both(adopted))
    assert _stale(adopted) == []


def test_legacy_evidence_with_both_fields_is_stale_after_an_edit(adopted):
    (adopted / "a.py").write_text("a = 1\n")
    _passed(adopted, _legacy_both(adopted))
    (adopted / "a.py").write_text("a = 2\n")
    notes = _stale(adopted)
    assert [n.gate for n in notes] == ["unit_tests"] and not notes[0].unverified


def test_legacy_evidence_with_both_fields_is_fresh_once_the_edits_are_committed(adopted):
    (adopted / "a.py").write_text("a = 1\n")
    _passed(adopted, _legacy_both(adopted))
    _git(adopted, "add", "a.py")
    _git(adopted, "commit", "-qm", "a")
    assert _stale(adopted) == []


def test_legacy_fingerprint_only_evidence_on_a_dirty_tree_compares_the_fingerprint(adopted):
    (adopted / "a.py").write_text("a = 1\n")
    _passed(adopted, {"command": "true", "exit": 0, "tree_sha": G.tree_fingerprint(adopted)})
    assert _stale(adopted) == []
    (adopted / "a.py").write_text("a = 2\n")
    assert [n.gate for n in _stale(adopted)] == ["unit_tests"]


# -- the new identity ----------------------------------------------------------------------


def test_a_clean_tree_is_spelled_as_before(adopted):
    assert G.tree_identity(adopted) == G.tree_fingerprint(adopted)
    assert G.tree_identity(adopted).endswith("+clean")


def test_a_dirty_tree_is_head_plus_its_content_id(adopted):
    (adopted / "a.py").write_text("a = 1\n")
    head = _git(adopted, "rev-parse", "HEAD")[:12]
    assert G.tree_identity(adopted) == f"{head}+{G.source_tree(adopted)}"
    assert G.tree_identity(adopted).split("+", 1)[1].startswith("st:")


def test_outside_a_repository_there_is_no_identity(tmp_path):
    assert G.tree_identity(tmp_path) == ""


def test_the_identity_moves_when_an_already_modified_tracked_binary_is_re_edited(adopted):
    (adopted / "blob.bin").write_bytes(b"\x00\x01first")
    _git(adopted, "add", "blob.bin")
    _git(adopted, "commit", "-qm", "blob")
    (adopted / "blob.bin").write_bytes(b"\x00\x01second")
    first = G.tree_identity(adopted)
    (adopted / "blob.bin").write_bytes(b"\x00\x01third!")
    assert G.tree_identity(adopted) != first


def test_the_identity_ignores_ddflow_bookkeeping(adopted):
    (adopted / "a.py").write_text("a = 1\n")
    before = G.tree_identity(adopted)
    (adopted / ".ddflow" / "noise.jsonl").write_text("{}\n")
    assert G.tree_identity(adopted) == before


def test_recorded_content_reads_the_new_and_the_legacy_spellings(adopted):
    (adopted / "a.py").write_text("a = 1\n")
    new = G.tree_identity(adopted)
    assert G.recorded_content(new) == G.source_tree(adopted)
    assert G.recorded_content(new, "st:other") == "st:other", "a recorded source_tree wins"
    assert G.recorded_content(G.tree_fingerprint(adopted)) == ""
    assert G.recorded_content("abc+clean") == ""
    assert G.recorded_content("") == ""


def test_new_evidence_is_fresh_on_the_same_tree_and_stale_after_an_edit(adopted):
    (adopted / "a.py").write_text("a = 1\n")
    _passed(adopted, {"command": "true", "exit": 0, "tree_sha": G.tree_identity(adopted)})
    assert _stale(adopted) == []
    (adopted / "a.py").write_text("a = 2\n")
    notes = _stale(adopted)
    assert [n.gate for n in notes] == ["unit_tests"] and not notes[0].unverified


def test_new_evidence_on_uncommitted_edits_is_fresh_once_they_are_committed(adopted):
    (adopted / "a.py").write_text("a = 1\n")
    _passed(adopted, {"command": "true", "exit": 0, "tree_sha": G.tree_identity(adopted)})
    _git(adopted, "add", "a.py")
    _git(adopted, "commit", "-qm", "a")
    assert _stale(adopted) == []


def test_a_gate_run_records_the_one_identity(adopted):
    (adopted / ".ddflow" / "gates.toml").write_text('[gate.unit_tests]\ncommand = "true"\n')
    gdef = G.load_gates(adopted, Config.load(adopted))["unit_tests"]
    (adopted / "a.py").write_text("a = 1\n")
    ev = G.run_command_gate(gdef, adopted)[1]
    assert ev["tree_sha"] == G.tree_identity(adopted)
    assert "source_tree" not in ev


def _unborn(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    return path


def test_before_the_first_commit_the_content_is_still_named(tmp_path):
    """`git init`, scaffold, run a gate: there is no HEAD, but the manifest exists. It used
    to be recorded as `source_tree` beside an empty fingerprint; the one identity keeps it
    (`+st:...`, no commit part) rather than recording nothing."""
    work = _unborn(tmp_path / "fresh")
    (work / "a.py").write_text("a = 1\n")
    ident = G.tree_identity(work)
    assert ident.startswith("+st:")
    assert G.recorded_content(ident) == G.source_tree(work)
    (work / "a.py").write_text("a = 2\n")
    assert G.tree_identity(work) != ident


def test_evidence_taken_before_the_first_commit_goes_stale_on_an_edit(adopted, tmp_path):
    work = _unborn(tmp_path / "fresh")
    (work / "a.py").write_text("a = 1\n")
    _passed(adopted, {"command": "true", "exit": 0, "tree_sha": G.tree_identity(work)})
    st = fold(EventLog(adopted).read_all(), strict=False)
    cfg = Config.load(adopted)
    assert outcomes.stale_evidence_detail(st, cfg, "T1", work) == []
    (work / "a.py").write_text("a = 2\n")
    notes = outcomes.stale_evidence_detail(st, cfg, "T1", work)
    assert [n.gate for n in notes] == ["unit_tests"] and not notes[0].unverified


def test_a_fingerprint_spelled_dirty_value_is_compared_as_a_fingerprint(adopted):
    """The fallback `tree_identity` emits when no manifest can be taken, and what an older
    gate recorded: not an `st:` id, and not HEAD's own tree -- an edit makes it stale."""
    (adopted / "a.py").write_text("a = 1\n")
    legacy = G.tree_fingerprint(adopted)
    assert not legacy.endswith("+clean") and G.recorded_content(legacy) == ""
    _passed(adopted, {"command": "true", "exit": 0, "tree_sha": legacy})
    (adopted / "a.py").write_text("a = 2\n")
    assert [n.gate for n in _stale(adopted)] == ["unit_tests"]


def test_is_tree_reads_both_spellings_and_nothing(adopted):
    (adopted / "a.py").write_text("a = 1\n")
    assert G.is_tree(G.tree_identity(adopted), adopted)
    assert G.is_tree(G.tree_fingerprint(adopted), adopted)
    assert not G.is_tree("", adopted)
    (adopted / "a.py").write_text("a = 2\n")
    assert not G.is_tree("0" * 12 + "+st:other", adopted)
    _git(adopted, "add", "a.py")
    _git(adopted, "commit", "-qm", "a")
    legacy_clean = _git(adopted, "rev-parse", "HEAD")[:12] + "+" + G.LEGACY_CLEAN
    assert G.is_tree(legacy_clean, adopted), "the pre-fix spelling of a clean tree"
