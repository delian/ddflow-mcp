"""One explicit agent name on two clones is a double claim, and it is said so (B36aa4c8ee5).

`--agent X`, `DDFLOW_AGENT`, `ddflow_identify` names and a committed `[agent].id` are
used verbatim, so two users who both run an agent called `U1` write ONE shard that
`merge=union` concatenates. Both claim T1, both exit 0, and the fold saw ONE holder: no
CONTESTED, `doctor` exit 0 with only a NOTE that the clock decreases.

A claim now records the clone that made it (`.ddflow/local/clone-id`). The fold reads a
claim by the SAME name from a DIFFERENT clone as another holder's, so overlapping ones are
a contest, and `doctor` calls a shard with two writers a PROBLEM that says how to fix it.
A claim without a clone (every claim written before this) is never a rival of its own name.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from helpers import git_raw as _git
from helpers import make_event as _ev

from ddflow.api import reporting
from ddflow.core.model import fold
from ddflow.infra import log as L
from tests.conftest import run_cli


@pytest.fixture(autouse=True)
def _no_version_stamp(monkeypatch):
    monkeypatch.setattr(L.EventLog, "stamp", False)


def _acq(lamport: int, at: float, clone: str | None, holder: str = "U1"):
    extra = {} if clone is None else {"clone": clone}
    return _ev("lease.acquired", "T", lamport, holder, holder=holder, at=at, ttl_s=1000, **extra)


def _item(*claims):
    return fold([_ev("task.added", "T", 1, title="t"), *claims]).items["T"]


def test_the_same_name_from_two_clones_is_a_contest():
    it = _item(_acq(2, 1000.0, "aaaaaa"), _acq(3, 1100.0, "bbbbbb"))
    assert len(it.lease_contest) == 2
    assert it.contest_summary()


def test_the_same_name_from_one_clone_is_not_a_contest():
    it = _item(_acq(2, 1000.0, "aaaaaa"), _acq(3, 1100.0, "aaaaaa"))
    assert it.lease_contest == []


def test_a_claim_without_a_clone_never_rivals_its_own_name():
    """Claims written before the clone id existed, or on an unadopted project."""
    assert _item(_acq(2, 1000.0, None), _acq(3, 1100.0, None)).lease_contest == []
    assert _item(_acq(2, 1000.0, None), _acq(3, 1100.0, "bbbbbb")).lease_contest == []


def test_non_overlapping_claims_from_two_clones_are_a_handover_not_a_contest():
    it = _item(_acq(2, 1000.0, "aaaaaa"), _acq(3, 9000.0, "bbbbbb"))
    assert it.lease_contest == []


# -- end to end: two real clones, one explicit name ---------------------------------------


def _clone(remote: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git(dest, "config", k, v)
    return dest


def _commit_log(repo: Path, msg: str) -> None:
    _git(repo, "add", ".ddflow/events")
    _git(repo, "commit", "-qm", msg, "--no-verify")


@pytest.fixture
def merged(tmp_path: Path) -> Path:
    """Both clones claim T1 as `U1` offline, then A pulls B's shard with the union driver."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    a = _clone(remote, tmp_path / "a")
    (a / ".gitattributes").write_text(".ddflow/events/*.jsonl merge=union\n")
    _git(a, "add", ".gitattributes")
    _git(a, "commit", "-qm", "attrs", "--no-verify")
    assert (
        run_cli(a, "task", "add", "T1", "--title", "t", "--globs", "src/t.py", agent="U1")[0] == 0
    )
    _commit_log(a, "T1")
    _git(a, "push", "-q", "origin", "main")
    b = _clone(remote, tmp_path / "b")
    # Each clone is adopted on first use; give them distinct clone ids the way init does.
    for repo, cid in ((a, "aaaaaa"), (b, "bbbbbb")):
        (repo / ".ddflow" / "local").mkdir(parents=True, exist_ok=True)
        (repo / ".ddflow" / "local" / "clone-id").write_text(cid + "\n")
    assert run_cli(b, "claim", "T1", agent="U1")[0] == 0
    _commit_log(b, "b claim")
    _git(b, "push", "-q", "origin", "main")
    assert run_cli(a, "claim", "T1", agent="U1")[0] == 0
    _commit_log(a, "a claim")
    _git(a, "pull", "-q", "--no-rebase", "--no-edit", "origin", "main")
    return a


def test_two_clones_claiming_as_one_name_leave_the_item_contested(merged):
    out = reporting.doctor(merged, agent="U1")
    assert any("T1 is CONTESTED" in p for p in out.data["problems"]), out.data
    code, text, err = run_cli(merged, "show", "T1", agent="U1")
    assert code == 0 and "CONTESTED" in text, text + err


def test_doctor_calls_a_shard_with_two_writers_a_problem(merged):
    out = reporting.doctor(merged, agent="U1")
    problems = " ".join(out.data["problems"])
    assert "U1.jsonl" in problems and "decrease" in problems, out.data
    assert "DDFLOW_AGENT" in problems  # says how to give each clone its own id
    assert not any("decrease" in n for n in out.data["notes"])
    code = run_cli(merged, "doctor", agent="U1")[0]
    assert code == 1
