"""Two decisions recorded without seeing each other are said to be unreconciled (B0dcccd8594).

alice records D-sql (storage MUST be sqlite) and bob D-pg (MUST be postgres) on the same
globs, offline. After the merge `decision applicable` and `brief` listed BOTH as binding and
`doctor` was clean. The fold is pure and cannot tell concurrent from sequential from lamports
alone, so `decision add` records what its author SAW: the ids of the live decisions whose
globs overlapped the new one at write time (`saw`, additive; no new kind). Two live decisions
on overlapping globs where neither saw the other, and neither supersedes the other, are
UNRECONCILED. A decision with no `saw` key (every log written before) never forms a pair.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from helpers import git_raw as _git
from helpers import make_event as _ev

from ddflow.api import decisions as DEC
from ddflow.api import reporting
from ddflow.core.model import fold
from ddflow.infra import log as L
from tests.conftest import run_cli


@pytest.fixture(autouse=True)
def _no_version_stamp(monkeypatch):
    monkeypatch.setattr(L.EventLog, "stamp", False)


def _dec(did: str, lamport: int, agent: str, saw: list[str] | None, globs=("src/a.py",), **extra):
    data = {"title": did, "decision": f"{did} text", "globs": list(globs), **extra}
    if saw is not None:
        data["saw"] = saw
    return _ev("decision.recorded", did, lamport, agent, **data)


def _pairs(*events) -> list[tuple[str, str]]:
    from ddflow.services.guidance.kinds import unreconciled

    st = fold([_ev("task.added", "T1", 1, title="t", globs=["src/a.py"]), *events])
    return [(a.id, b.id) for a, b in unreconciled(st)]


# -- the fold ------------------------------------------------------------------------------


def test_two_decisions_that_did_not_see_each_other_are_unreconciled():
    assert _pairs(_dec("D-sql", 2, "alice", []), _dec("D-pg", 2, "bob", [])) == [("D-pg", "D-sql")]


def test_a_decision_that_saw_the_other_is_not_flagged():
    assert _pairs(_dec("D-sql", 2, "alice", []), _dec("D-pg", 3, "alice", ["D-sql"])) == []
    assert _pairs(_dec("D-sql", 3, "alice", ["D-pg"]), _dec("D-pg", 2, "bob", [])) == []


def test_events_without_saw_never_form_a_pair():
    """Every log written before this field folds exactly as it did."""
    assert _pairs(_dec("D-sql", 2, "alice", None), _dec("D-pg", 2, "bob", None)) == []
    assert _pairs(_dec("D-sql", 2, "alice", None), _dec("D-pg", 2, "bob", [])) == []


def test_disjoint_globs_are_not_flagged():
    a = _dec("D-sql", 2, "alice", [], globs=("src/a.py",))
    b = _dec("D-pg", 2, "bob", [], globs=("docs/**",))
    assert _pairs(a, b) == []


def test_a_decision_that_supersedes_the_other_is_not_flagged():
    a = _dec("D-sql", 2, "alice", [])
    b = _dec("D-pg", 2, "bob", [], supersedes=["D-sql"])
    assert _pairs(a, b) == []


def test_a_decision_superseding_both_clears_the_flag():
    a, b = _dec("D-sql", 2, "alice", []), _dec("D-pg", 2, "bob", [])
    assert _pairs(a, b)
    c = _dec("D-both", 3, "alice", ["D-sql", "D-pg"], supersedes=["D-sql", "D-pg"])
    assert _pairs(a, b, c) == []


def test_superseding_one_leaves_no_pair_with_the_retired_one():
    a, b = _dec("D-sql", 2, "alice", []), _dec("D-pg", 2, "bob", [])
    s = _ev("decision.superseded", "D-sql", 3, "alice", by="D-pg")
    assert _pairs(a, b, s) == []


# -- two real clones -------------------------------------------------------------------------


def _clone(remote: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git(dest, "config", k, v)
    return dest


def _commit_log(repo: Path, msg: str) -> None:
    _git(repo, "add", ".ddflow/events")
    _git(repo, "commit", "-qm", msg, "--no-verify", "--allow-empty")


def _add(repo: Path, did: str, agent: str, *extra: str) -> None:
    args = (
        "decision",
        "add",
        "--id",
        did,
        "--title",
        f"title of {did}",
        "--decision",
        f"{did} MUST hold",
        "--globs",
        "src/a.py",
        "--by",
        "operator",
        *extra,
    )
    code, out, err = run_cli(repo, *args, agent=agent)
    assert code == 0, out + err


@pytest.fixture
def clones(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    a = _clone(remote, tmp_path / "a")
    (a / ".gitattributes").write_text(".ddflow/events/*.jsonl merge=union\n")
    _git(a, "add", ".gitattributes")
    _git(a, "commit", "-qm", "attrs", "--no-verify")
    code = run_cli(a, "task", "add", "T1", "--title", "t", "--globs", "src/a.py", agent="alice")[0]
    assert code == 0
    _commit_log(a, "T1")
    _git(a, "push", "-q", "origin", "main")
    return a, _clone(remote, tmp_path / "b")


def _pull(repo: Path) -> None:
    _git(repo, "pull", "-q", "--no-rebase", "--no-edit", "origin", "main")


def _exchange(a: Path, b: Path) -> None:
    _commit_log(b, "b")
    _git(b, "push", "-q", "origin", "main")
    _commit_log(a, "a")
    _pull(a)


@pytest.fixture
def concurrent(clones) -> Path:
    a, b = clones
    _add(a, "D-sql", "alice")
    _add(b, "D-pg", "bob")
    _exchange(a, b)
    return a


def test_doctor_notes_two_unseen_decisions_and_the_fix(concurrent):
    out = reporting.doctor(concurrent, agent="alice")
    notes = [n for n in out.data["notes"] if "unreconciled" in n.lower()]
    assert notes, out.data["notes"]
    text = notes[0]
    assert "D-sql" in text and "D-pg" in text, text
    assert "alice" in text and "bob" in text, text
    assert "decision supersede" in text, text
    assert not any("unreconciled" in p.lower() for p in out.data["problems"])


def test_applicable_and_brief_mark_both_unreconciled(concurrent):
    code, text, err = run_cli(concurrent, "decision", "applicable", "T1", agent="alice")
    assert code == 0, err
    assert text.lower().count("unreconciled") >= 2, text
    code, text, err = run_cli(concurrent, "brief", "--item", "T1", agent="alice")
    assert code == 0, err
    assert "unreconciled" in text.lower(), text
    out = DEC.decision_applicable(concurrent, "T1")
    got = {d["id"]: d.get("unreconciled") for d in out.data["applicable"]}
    assert got == {"D-sql": ["D-pg"], "D-pg": ["D-sql"]}, out.data


def test_the_same_two_decisions_added_in_sequence_are_not_flagged(clones):
    a, b = clones
    _add(a, "D-sql", "alice")
    _add(a, "D-pg", "alice")  # its `saw` names D-sql
    _exchange(a, b)
    out = reporting.doctor(a, agent="alice")
    assert not [n for n in out.data["notes"] if "unreconciled" in n.lower()], out.data["notes"]
    code, text, _ = run_cli(a, "decision", "applicable", "T1", agent="alice")
    assert code == 0 and "unreconciled" not in text.lower(), text


def test_a_decision_superseding_both_clears_the_flag_end_to_end(concurrent):
    _add(concurrent, "D-both", "alice", "--supersedes", "D-sql,D-pg")
    out = reporting.doctor(concurrent, agent="alice")
    assert not [n for n in out.data["notes"] if "unreconciled" in n.lower()], out.data["notes"]
