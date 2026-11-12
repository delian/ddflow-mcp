"""PS-evidence-keys-audit (D-evidence-blobs-on): each reader of the evidence keys that may move
out of line -- reviewed_paths, ci_scope, note, baseline -- must see the VALUE when the shard
holds a blob reference, not the stub. One test per reader."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ddflow.config import Config, LogConfig
from ddflow.core import blobref as B
from ddflow.core.model import fold
from ddflow.infra import blobstore as BS
from ddflow.infra.log import EventLog
from ddflow.services import gate_accept as GA
from ddflow.services import testscope as TS
from ddflow.services.completion import rules as R

KEYS = ["reviewed_paths", "ci_scope", "note", "baseline"]
ON = LogConfig(evidence_blobs=True, evidence_blob_keys=KEYS, evidence_blob_min_bytes=1)
BASELINE = {"red": True, "bug": "B1", "auto": {"accepted": True, "ids": ["t::a"], "why": "x"}}


@pytest.fixture(autouse=True)
def _only_this_tests_projects():
    BS._KNOWN_ROOTS.clear()
    yield
    BS._KNOWN_ROOTS.clear()


def _item(repo, gate, outcome, **evidence):
    log = EventLog(repo, "a", log_cfg=ON)
    log.append("task.added", "T1", {"title": "t", "globs": ["a.py"]})
    ev = log.append(f"gate.{outcome}", "T1", {"gate": gate, "reason": "r", "evidence": evidence})
    stubs = {k for k, _ in B.stubs(ev.data["evidence"])}
    assert stubs == set(evidence) & set(KEYS), "the fixture must really move the keys out"
    return fold(log.read_all(), strict=False).items["T1"]


def test_baseline_reader_gate_accept_refusal(repo):
    it = _item(repo, "ci", "partial", baseline=BASELINE)
    assert GA.refusal(it, "ci") == ""


def test_baseline_reader_gate_accept_auto_ids(repo):
    it = _item(repo, "ci", "partial", baseline=BASELINE)
    cfg = Config.load(repo)
    assert GA.auto_ids(it, "ci", cfg) == ["t::a"]


def test_baseline_reader_gate_accept_next_step(repo):
    it = _item(repo, "ci", "partial", baseline={"red": True, "auto": {"why": "WHYNOT"}})
    assert "WHYNOT" in GA.next_step(it, "T1", "ci")


def test_baseline_reader_testscope(repo):
    it = _item(repo, "ci", "partial", baseline=BASELINE)
    assert TS._ci_baseline_red(it) is True


def test_baseline_reader_notice(repo):
    from ddflow.api import gate_baseline_notice as N

    _item(repo, "ci", "partial", baseline=BASELINE)
    assert N.notify(repo, "a", "T1", "ci") is True


def test_ci_scope_reader_testscope(repo):
    it = _item(repo, "ci", "failed", ci_scope={"scope": "selected", "why": "w"})
    assert TS._ci_failed_selected(it) is True


def test_ci_scope_reader_unit_tests_scope_accessor(repo, monkeypatch):
    """`unit_tests_scope` reads the ci gate's `ci_scope` inline (the `_ci_scope` accessor it
    once had is gone): what it hands `_after_ci` must be the value, not the stub."""
    it = _item(repo, "ci", "passed", ci_scope={"scope": "selected", "why": "w"})
    seen = []
    monkeypatch.setattr(TS, "changed_lines", lambda tree, base: 1)
    monkeypatch.setattr(TS, "_ci_excused", lambda *a: ("", False))  # past the tree check
    monkeypatch.setattr(TS, "_after_ci", lambda *a: seen.append(a[-1]) or TS.Scope("full", "x"))
    TS.unit_tests_scope(Config.load(repo), SimpleNamespace(bugs={}), it, "pytest", repo)
    assert seen == [{"scope": "selected", "why": "w"}]


def test_reviewed_paths_reader_refine_stale_notes(repo, monkeypatch):
    it = _item(repo, "critic", "passed", reviewed_paths={"a.py": "h1"})
    monkeypatch.setattr(R, "worktree_entries", lambda cwd: {"a.py": ["h1"]})
    note = SimpleNamespace(gate="critic", unverified=False, why="differ")
    out = R.refine_stale_notes([note], it, repo, "", ["a.py"])
    assert out == [], "an unchanged reviewed path must drop the note (a stub would keep it)"


def test_note_reader_readme_report(repo):
    from ddflow.core.model import State  # noqa: F401

    log = EventLog(repo, "a", log_cfg=ON)
    log.append("task.added", "T1", {"title": "t", "globs": ["a.py"]})
    log.append("gate.passed", "T1", {"gate": "docs", "evidence": {"note": "README section X"}})
    st = fold(log.read_all(), strict=False)
    cfg = Config.load(repo)
    assert R.readme_report(st, cfg, "T1", repo=repo) == ""
