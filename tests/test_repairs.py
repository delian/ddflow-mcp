"""Versioned data repairs (B-upgrade.5-repairs, decision D-upgrade-model (4)).

Failing-first: a crafted log as an old ddflow (0.1.3) left it, carrying each kind of damage.
For every registered repair: detect finds it, apply appends corrective events and one
`repair.applied`, a second run finds nothing, and every line that was there before is
byte-identical (history is never rewritten).
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.core.events import Event
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import repairs as R
from ddflow.services.export import frame as F

OLD = "old-agent"
_clock = {"n": 0}


def _git(repo: Path, *argv: str) -> None:
    subprocess.run(["git", "-C", str(repo), *argv], check=True, capture_output=True)


def _raw(repo: Path, kind: str, subject: str, data: dict, *, agent: str = OLD) -> Event:
    """Append one event line exactly as an old ddflow wrote it: no stamp of this version."""
    _clock["n"] += 1
    n = _clock["n"]
    ev = Event(kind, subject, data, agent=agent, lamport=n, ts=f"2026-09-20T10:{n // 60:02d}:{n % 60:02d}Z")
    shard = repo / ".ddflow" / "events" / f"{agent}.jsonl"
    shard.parent.mkdir(parents=True, exist_ok=True)
    with shard.open("a", encoding="utf-8") as fh:
        fh.write(ev.to_json() + "\n")
    return ev


def _line(repo: Path, text: str, *, agent: str = OLD) -> None:
    with (repo / ".ddflow" / "events" / f"{agent}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(text)


@pytest.fixture
def old(repo: Path) -> Path:
    """An initialised project whose log an old ddflow wrote and committed."""
    _clock["n"] = 0
    assert run_cli(repo, "init")[0] == 0
    _raw(repo, "ddflow.seen", "ddflow", {"version": "0.1.3", "install": "wheel"})
    _raw(repo, "session.started", "s-old", {"model": "m", "tool": "t"})
    _raw(repo, "phase.added", "P1", {"title": "Billing", "globs": ["src/**"], "needs": []})
    _raw(repo, "task.added", "T1", {"title": "one", "parent": "P1", "globs": ["src/**"]})
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "old log")
    return repo


# -- one damage per repair ---------------------------------------------------------------


def _orphan(repo: Path) -> None:
    _raw(repo, "session.prompt", "", {"text": "lost words", "item": ""})
    _raw(repo, "session.note", "", {"text": "lost note", "item": ""})


def _forced(repo: Path) -> None:
    _raw(repo, "item.completed", "T1", {"forced": True, "overridden": ["gate unit_tests not run"]})


def _torn(repo: Path) -> None:
    _line(repo, '{"agent":"old-agent","data":{"te\n')  # a torn append, later terminated
    _line(repo, "123\n")  # JSON, but not an event object


def _mismatched(repo: Path) -> None:
    ev = Event("session.note", "s-old", {"text": "said"}, agent=OLD, lamport=99, ts="2026-09-20T11:00:00Z")
    # Edited after it was written: the data changed, the id did not.
    edited = replace(ev, data={"text": "said something else"}, id=ev.compute_id())
    _line(repo, edited.to_json() + "\n")


def _stranger(repo: Path) -> None:
    _raw(repo, "lesson.recorded", "L-x", {"title": "trust me"}, agent="stranger")


def _old_render(repo: Path) -> None:
    p = repo / ".ddflow" / "config.toml"
    p.write_text(p.read_text() + '\n[export]\ndocuments = ["roadmap"]\n')
    assert run_cli(repo, "export", "roadmap", "--update")[0] == 0
    doc = repo / "ROADMAP.md"
    head, _body = F.split(doc.read_text())
    assert head is not None
    doc.write_text(doc.read_text().replace(f" v={head.version} ", " v=0.1.3 ", 1))
    _raw(repo, "task.added", "T2", {"title": "two", "parent": "P1", "globs": ["x/**"]})


FIXTURES: dict[str, Callable[[Path], None]] = {
    "orphan-prompts": _orphan,
    "forced-completions": _forced,
    "unreadable-lines": _torn,
    "mismatched-ids": _mismatched,
    "unknown-author-shards": _stranger,
    "old-export-renders": _old_render,
}


def _ctx(repo: Path, agent: str = "repairer") -> R.Context:
    return R.context(repo, EventLog(repo, agent), Config.load(repo))


def _shard_bytes(repo: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in (repo / ".ddflow" / "events").glob("*.jsonl")}


def _ids(pend: list[R.Pending]) -> list[str]:
    return [p.repair.id for p in pend]


def test_every_registered_repair_has_a_fixture_and_metadata():
    ids = [r.id for r in R.REGISTRY]
    assert sorted(ids) == sorted(FIXTURES), "a repair ships with its damage fixture here"
    assert len(set(ids)) == len(ids)
    for r in R.REGISTRY:
        assert r.since and r.title and r.action and r.consent in (R.AGENT, R.OPERATOR)


@pytest.mark.parametrize("rid", sorted(FIXTURES))
def test_detect_repair_then_nothing_and_old_lines_untouched(old, rid):
    assert rid not in _ids(R.pending(_ctx(old))), "the clean old log has no such damage"
    FIXTURES[rid](old)
    before = _shard_bytes(old)
    (p,) = R.pending(_ctx(old), [rid])
    assert p.findings and not p.unavailable
    log = EventLog(old, "repairer")
    applied = R.apply(old, log, Config.load(old), [rid])
    assert [a["repair"] for a in applied] == [rid]
    assert applied[0]["findings"] == [f.key for f in p.findings]
    assert R.pending(_ctx(old), [rid]) == []
    assert R.apply(old, log, Config.load(old), [rid]) == [], "a second run applies nothing"
    after = _shard_bytes(old)
    for name, data in before.items():
        assert after[name] == data, f"{name}: a repair rewrote history"
    st = fold(log.read_all(), strict=False)
    assert R.settled(st, rid) == {f.key for f in p.findings}


def test_failing_first_old_log_with_every_damage(old):
    for damage in FIXTURES.values():
        damage(old)
    pend = R.pending(_ctx(old))
    assert sorted(_ids(pend)) == sorted(FIXTURES)
    log = EventLog(old, "repairer")
    applied = R.apply(old, log, Config.load(old))
    # Unnamed, only the repairs an agent may apply run: the author review is the operator's.
    assert "unknown-author-shards" not in [a["repair"] for a in applied]
    assert _ids(R.pending(_ctx(old))) == ["unknown-author-shards"]
    R.apply(old, log, Config.load(old), ["unknown-author-shards"])
    assert R.pending(_ctx(old)) == []
    assert R.doctor_notes(_ctx(old)) == []


def test_orphans_are_adopted_under_the_nearest_session(old):
    _orphan(old)
    log = EventLog(old, "repairer")
    R.apply(old, log, Config.load(old), ["orphan-prompts"])
    copies = [e for e in log.read_all() if e.data.get("adopted_from")]
    assert {e.data["text"] for e in copies} == {"lost words", "lost note"}
    assert {e.subject for e in copies} == {"s-old"}


def test_an_orphan_with_no_session_gets_one_implicit_session(repo):
    _clock["n"] = 0
    _raw(repo, "session.prompt", "", {"text": "alone"})
    log = EventLog(repo, "repairer")
    R.apply(repo, log, Config(), ["orphan-prompts"])
    starts = [e for e in log.read_all() if e.kind == "session.started"]
    (copy,) = [e for e in log.read_all() if e.data.get("adopted_from")]
    assert [s.subject for s in starts] == [copy.subject] and starts[0].data["implicit"]


def test_old_render_is_regenerated_and_a_hand_edit_is_left_alone(old):
    _old_render(old)
    R.apply(old, EventLog(old, "repairer"), Config.load(old), ["old-export-renders"])
    head, _ = F.split((old / "ROADMAP.md").read_text())
    assert head is not None and head.version != "0.1.3"
    assert run_cli(old, "export", "roadmap", "--check")[0] == 0
    doc = old / "ROADMAP.md"
    doc.write_text(doc.read_text().replace(f" v={head.version} ", " v=0.1.3 ", 1) + "hand edit\n")
    assert R.pending(_ctx(old), ["old-export-renders"]) == []


def test_a_detector_that_cannot_run_is_unavailable_not_clean(tmp_path):
    _clock["n"] = 0
    proj = tmp_path / "nogit"
    proj.mkdir()
    _raw(proj, "session.started", "s", {})
    _raw(proj, "session.started", "s2", {}, agent="other")
    (p,) = R.pending(_ctx(proj), ["unknown-author-shards"])
    assert p.unavailable == "not a git repository" and not p.findings
    assert any(n.startswith("unavailable: data repair unknown-author-shards") for n in R.doctor_notes(_ctx(proj)))


def test_repair_applied_folds_and_reads_in_history(old):
    _forced(old)
    R.apply(old, EventLog(old, "repairer"), Config.load(old), ["forced-completions"])
    code, out, _err = run_cli(old, "history", "--kind", "repair")
    assert code == 0 and "data repair applied" in out and "forced" in out


def test_unknown_repair_id_is_refused(old):
    with pytest.raises(KeyError, match="no repair 'nope'"):
        R.apply(old, EventLog(old, "repairer"), Config.load(old), ["nope"])


# -- the registry rule ------------------------------------------------------------------


def _damaged_bug(repo: Path, bug: str) -> None:
    _raw(repo, "bug.found", bug, {"summary": "lost data", "item": "T1", "fix_task": f"fix-{bug}"})
    _raw(repo, "task.added", f"fix-{bug}", {"title": "fix", "parent": "P1", "tags": ["data-damage"], "fixes": [bug]})
    _raw(repo, "bug.fixed", bug, {"regression_test": "tests/x.py::t"})


def test_a_fixed_data_damage_bug_needs_a_repair_or_a_fold_only_note(old, monkeypatch):
    _damaged_bug(old, "Bdead")
    st = fold(EventLog(old, "x").read_all(), strict=False)
    assert st.bugs["Bdead"].fixed_at and st.items["fix-Bdead"].tags == ["data-damage"]
    assert R.uncovered(st) == ["Bdead"]
    monkeypatch.setitem(R.FOLD_ONLY, "Bdead", "the fold reads the old shape")
    assert R.uncovered(st) == []


def test_this_projects_own_data_damage_bugs_are_all_covered():
    root = Path(__file__).resolve().parents[1]
    st = fold(EventLog(root, "reader").read_all(), strict=False)
    assert R.uncovered(st) == []
