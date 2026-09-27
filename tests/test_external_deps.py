"""Dependencies on items in a SIBLING repository: `needs = ["trainer:132.D"]`.

Two projects ddflow was built for depend on each other -- a data generator whose plan
waits on trainer-side items driven by another agent service in another repository. The
dependency was prose, and nothing could say when it was met.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog

OK, FAIL, NOTHING = 0, 1, 2


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(path), "config", k, v], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "--allow-empty", "-m", "i"], check=True)
    return path


def _pair(repo: Path) -> Path:
    trainer = _git_repo(repo.parent / "trainer")
    run_cli(trainer, "init")
    run_cli(trainer, "task", "add", "132.D", "--title", "train-record type", "--globs", "t.py")
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text('[schedule]\nrepos = ["trainer=../trainer"]\n')
    run_cli(repo, "task", "add", "GEN.1", "--needs", "trainer:132.D", "--globs", "g.py")
    return trainer


def _ready(repo: Path) -> set[str]:
    _c, out, _e = run_cli(repo, "--json", "next")
    return {r["id"] for r in json.loads(out)["ready"]}


def test_work_waits_until_the_other_repository_finishes_the_item(repo):
    trainer = _pair(repo)
    assert "GEN.1" not in _ready(repo), "an unobserved external dependency was treated as met"

    code, out, err = run_cli(repo, "--json", "external", "sync")
    assert code == OK, err
    assert json.loads(out) == [
        {
            "dep": "trainer:132.D",
            "state": "open",
            "title": "train-record type",
            "changed": True,
            "error": "",
        }
    ]
    assert "GEN.1" not in _ready(repo)

    # How the trainer's own workflow finished it is its business; the fact is the event.
    EventLog(trainer, "t").append("item.completed", "132.D", {"kind": "task"})
    assert fold(EventLog(trainer).read_all(), strict=False).items["132.D"].state == "done"
    run_cli(repo, "external", "sync")
    assert "GEN.1" in _ready(repo)


def test_only_a_CHANGE_is_recorded(repo):
    _pair(repo)
    run_cli(repo, "external", "sync")
    run_cli(repo, "external", "sync")
    seen = [e for e in EventLog(repo).read_all() if e.kind == "external.observed"]
    assert len(seen) == 1, "every sync wrote an event whether or not anything changed"


def test_nothing_is_ever_written_to_the_other_repository(repo):
    trainer = _pair(repo)
    before = sorted(p.read_bytes() for p in (trainer / ".ddflow" / "events").glob("*.jsonl"))
    run_cli(repo, "external", "sync")
    after = sorted(p.read_bytes() for p in (trainer / ".ddflow" / "events").glob("*.jsonl"))
    assert before == after


def test_an_unconfigured_repository_is_a_doctor_PROBLEM_and_a_failed_sync(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "GEN.1", "--needs", "nowhere:1.A", "--globs", "g.py")
    code, out, _err = run_cli(repo, "external", "sync")
    assert code == FAIL
    # The reason, not just the exit: a crash is exit 1 too, and says nothing useful.
    assert "NOT OBSERVED" in out and "'nowhere' is not in [schedule] repos" in out
    code, out, _e = run_cli(repo, "doctor")
    assert code == FAIL
    assert "'nowhere' is not in [schedule] repos" in out


def test_nothing_to_sync_is_exit_2(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    assert run_cli(repo, "external", "sync")[0] == NOTHING


def test_the_session_start_hook_observes_before_the_brief(repo):
    trainer = _pair(repo)
    EventLog(trainer, "t").append("item.completed", "132.D", {"kind": "task"})
    code, out, _e = run_cli(repo, "hooks", "session-start")
    assert code == OK
    assert "trainer:132.D is done" in out
    assert "GEN.1" in out.split("## Ready now", 1)[1]
