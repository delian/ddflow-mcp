"""A merge queue is reported as one: enqueued, position, ejected (B172).

`gh pr merge` on a queue-protected branch ENQUEUES instead of merging. ddflow saw "not
merged yet" and kept the item in review -- correct, but it could not say the request was
queued, where it stood, or that the queue had ejected it.
"""

# ruff: noqa: F811  (fixtures imported from test_flow)
from __future__ import annotations

import json

from conftest import run_cli
from test_flow import AUTHOR, _state, _work, pr_repo  # noqa: F401

GREEN = [{"status": "COMPLETED", "conclusion": "SUCCESS"}]


def _approved_and_green(pr_repo):
    repo, forge, _remote = pr_repo
    forge.set(merge_queue=True)
    _work(repo, "T1", "a.py", "a\n")
    run_cli(repo, "merge", "T1", "--model", AUTHOR)
    forge.approve(1)
    forge.edit(1, checks=GREEN)
    return repo, forge


def _sync(repo) -> dict:
    code, out, err = run_cli(repo, "--json", "pr", "sync")
    assert code == 0, err
    return json.loads(out)


def _row(repo) -> dict:
    _, out, _ = run_cli(repo, "--json", "pr", "status")
    return next(r for r in json.loads(out)["rows"] if r["id"] == "T1")


def test_an_enqueued_request_is_reported_as_queued_with_its_position(pr_repo):
    repo, _forge = _approved_and_green(pr_repo)
    body = _sync(repo)
    queued = [c for c in body["changes"] if c["what"] == "queued"]
    assert queued and "position 1" in queued[0]["detail"], body["changes"]
    assert body["waiting"][0]["queue_position"] == 1 and body["waiting"][0]["queued"] is True
    row = _row(repo)
    assert (row["queue"], row["queue_position"]) == ("queued", 1), row
    assert _state(repo).items["T1"].state == "review", "still the queue's to finish"


def test_a_queued_request_is_not_merged_again_or_re_reported(pr_repo):
    repo, forge = _approved_and_green(pr_repo)
    _sync(repo)
    again = _sync(repo)
    assert len(forge.calls("pr", "merge")) == 1, "asked the forge to merge a queued request twice"
    assert not [c for c in again["changes"] if c["what"] == "queued"], again["changes"]


def test_an_ejection_is_reported_as_one(pr_repo):
    repo, forge = _approved_and_green(pr_repo)
    _sync(repo)
    forge.queue_eject(1)
    body = _sync(repo)
    ejected = [c for c in body["changes"] if c["what"] == "queue_ejected"]
    assert ejected and "ejected" in ejected[0]["detail"], body["changes"]
    assert _row(repo)["queue"] == ""
    assert _state(repo).items["T1"].state == "review", "the request is still open"


def test_a_request_the_queue_landed_completes_the_item(pr_repo):
    repo, forge = _approved_and_green(pr_repo)
    _sync(repo)
    forge.queue_land(1)
    body = _sync(repo)
    whats = [c["what"] for c in body["changes"]]
    assert "merged" in whats and "completed" in whats, whats
    assert _state(repo).items["T1"].state == "done"


def test_a_queue_that_could_not_be_asked_is_not_an_ejection(pr_repo):
    """A rate limit on the GraphQL call is "do not know": the request is still queued, and
    must neither be reported ejected nor be asked to merge again."""
    repo, forge = _approved_and_green(pr_repo)
    _sync(repo)
    forge.set(graphql_down=True)
    body = _sync(repo)
    assert not [c for c in body["changes"] if c["what"] == "queue_ejected"], body["changes"]
    assert len(forge.calls("pr", "merge")) == 1, "re-merged a request that is still queued"
    assert _row(repo)["queue"] == "queued", "the last known queue state must be kept"


def test_positions_move_up_as_the_queue_drains(pr_repo):
    repo, forge, _ = pr_repo
    forge.set(merge_queue=True)
    for n, name in enumerate(("a.py", "b.py"), start=1):
        _work(repo, f"T{n}", name, f"{n}\n")
        run_cli(repo, "merge", f"T{n}", "--model", AUTHOR)
        forge.approve(n)
        forge.edit(n, checks=GREEN)
    _sync(repo)
    rows = {r["id"]: r for r in json.loads(run_cli(repo, "--json", "pr", "status")[1])["rows"]}
    assert (rows["T1"]["queue_position"], rows["T2"]["queue_position"]) == (1, 2)
    forge.queue_land(1)
    _sync(repo)
    rows = {r["id"]: r for r in json.loads(run_cli(repo, "--json", "pr", "status")[1])["rows"]}
    assert rows["T2"]["queue_position"] == 1, rows["T2"]


def test_pr_status_text_shows_the_queue(pr_repo):
    repo, _forge = _approved_and_green(pr_repo)
    _sync(repo)
    _, out, _ = run_cli(repo, "pr", "status")
    assert "[merge queue #1]" in out, out


def test_a_queue_that_could_not_be_asked_keeps_its_state_when_something_else_changes(pr_repo):
    """The restored queue state must be what the `pr.synced` event records too, or the
    projection reads the next fold as an ejection (the event is built from the same info)."""
    repo, forge = _approved_and_green(pr_repo)
    _sync(repo)
    forge.set(graphql_down=True)
    forge.edit(1, checks=[{"status": "IN_PROGRESS", "conclusion": ""}])  # something else moved
    _sync(repo)
    row = _row(repo)
    assert (row["queue"], row["queue_position"]) == ("queued", 1), row
    assert row["checks"] == "pending", "the other change must still be recorded"


def test_a_queue_state_change_without_a_move_is_seen(pr_repo):
    repo, forge = _approved_and_green(pr_repo)
    _sync(repo)
    forge.edit(1, queue={"seq": 1, "state": "UNMERGEABLE"})
    _sync(repo)
    assert _row(repo)["queue_state"] == "UNMERGEABLE"


def test_an_unaskable_queue_never_triggers_a_merge(pr_repo, monkeypatch):
    """Approved and green, but the queue call hangs (ForgeUnavailable): the request may
    already be queued, so ddflow must not ask the forge to merge it."""
    from ddflow.infra import forge as FG

    repo, _forge = _approved_and_green(pr_repo)
    real = FG._run

    def hang_on_graphql(repo_, argv, **kw):
        if argv[:3] == ["gh", "api", "graphql"]:
            raise FG.ForgeUnavailable("gh api graphql timed out")
        return real(repo_, argv, **kw)

    monkeypatch.setattr(FG, "_run", hang_on_graphql)
    from ddflow import api

    out = api.pr_sync(repo)
    assert out.data["waiting"], out.data
    assert "merge queue could not be read" in out.data["waiting"][0]["note"], out.data["waiting"]
    assert not _forge.calls("pr", "merge"), "merged a request whose queue state is unknown"


def test_pr_sync_text_says_why_an_unreadable_queue_holds_the_merge(pr_repo):
    repo, forge = _approved_and_green(pr_repo)
    forge.set(graphql_down=True)
    _, out, err = run_cli(repo, "pr", "sync")
    assert "merge queue could not be read" in out, (out, err)
    assert not forge.calls("pr", "merge")


# -- the local merge queue and its integrator (DX-merge-queue) ----------------------------
#
# Five agents racing for main each re-merged it and re-ran a ten-minute ci several times, and
# the pin files conflicted every time. Now an agent enqueues a finished item and goes on; one
# integrator lands the line. The project here has a pin (`pin.txt`, derived from `knobs/`)
# that two branches both re-record, and a ci command that fails on a stale pin, so a green
# landing proves the integrator regenerated it.

import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from conftest import append_config, pass_pipeline  # noqa: E402

from ddflow.services import mergequeue as MQ  # noqa: E402

GEN = """\
import sys
from pathlib import Path

knobs = sorted(p.read_text().strip() for p in Path("knobs").glob("*.txt"))
want = f"{len(knobs)} knobs\\n" + "".join(f"- {k}\\n" for k in knobs)
pin = Path("pin.txt")
if "--check" in sys.argv:
    sys.exit(0 if pin.exists() and pin.read_text() == want else 1)
pin.write_text(want)
"""

CHECK = """\
import subprocess, sys, time
from pathlib import Path

slow = Path({slow!r})
if slow.exists():
    time.sleep(float(slow.read_text() or 2))
if any("BAD" in p.read_text() for p in Path("knobs").glob("*.txt")):
    sys.exit(1)
sys.exit(subprocess.run([sys.executable, "gen.py", "--check"]).returncode)
"""

RECIPES = """\
[[pin]]
name = "demo"
files = ["pin.txt"]
check = ["{python}", "gen.py", "--check"]
regen = ["{python}", "gen.py"]
"""


def _git(where: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout


@pytest.fixture
def proj(repo: Path, monkeypatch) -> Path:
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)  # a pin regeneration refuses xdist
    run_cli(repo, "init")
    (repo / "knobs").mkdir()
    (repo / "knobs" / "a.txt").write_text("a\n")
    (repo / "gen.py").write_text(GEN)
    (repo / "check.py").write_text(CHECK.format(slow=str(repo.parent / "slow")))
    (repo / ".ddflow" / "pins.toml").write_text(RECIPES)
    subprocess.run([sys.executable, "gen.py"], cwd=repo, check=True)
    err, _ = append_config(
        repo,
        '[ci]\ncommand = "python check.py"\n\n[gates]\n'
        'task_pipeline = ["research", "rules", "implement", "rubber_duck", "critic", "standards",'
        ' "ci", "unit_tests", "bug_hunt", "dedupe", "merge"]\n',
    )
    assert not err
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    return repo


def _finish(proj: Path, item: str, text: str = "") -> Path:
    """Claim ``item`` as its own agent, add a knob and re-record the pin in its tree, commit,
    and satisfy every gate but the two the integrator produces."""
    name = item.lower()
    run_cli(proj, "task", "add", item, "--title", f"knob {name}", "--globs", f"knobs/{name}.txt")
    code, out, err = run_cli(proj, "--json", "claim", item, agent=name)
    assert code == 0, out + err
    tree = Path(json.loads(out)["worktree"])
    (tree / "knobs" / f"{name}.txt").write_text(text or f"{name}\n")
    subprocess.run([sys.executable, "gen.py"], cwd=tree, check=True)
    _git(tree, "add", "-A")
    _git(tree, "commit", "-qm", f"add knob {name}")
    pass_pipeline(proj, item, omit=("ci", "merge"))
    return tree


def _queue(proj: Path, item: str):
    return run_cli(proj, "merge", item, "--queue", agent=item.lower())


def _status(proj: Path) -> dict:
    code, out, err = run_cli(proj, "--json", "queue", "status")
    assert code == 0, out + err
    return {r["item"]: r for r in json.loads(out)["entries"]}


def _ci_outcome(proj: Path, item: str) -> str:
    code, out, err = run_cli(proj, "--json", "show", item)
    assert code == 0, out + err
    return json.loads(out)["gates"]["ci"]["outcome"]


def _landed(proj: Path) -> list[str]:
    log = _git(proj, "log", "--first-parent", "--reverse", "--format=%s").splitlines()
    return [ln.split()[1].rstrip(":") for ln in log if ln.startswith("merge T")]


def test_three_items_that_each_add_a_knob_land_in_queue_order_with_green_pins(proj):
    for item in ("T1", "T2", "T3"):
        _finish(proj, item)
    for item in ("T2", "T3", "T1"):  # the order they are queued, not the order they were made
        code, out, err = _queue(proj, item)
        assert code == 0, out + err
    assert _git(proj, "log", "-1", "--format=%s").strip() == "ddflow"  # nothing landed yet
    assert [r["state"] for r in _status(proj).values()] == ["queued"] * 3

    code, out, err = run_cli(proj, "queue", "run", agent="integrator")
    assert code == 0, out + err
    assert _landed(proj) == ["T2", "T3", "T1"]
    assert (proj / "pin.txt").read_text() == "4 knobs\n- a\n- t1\n- t2\n- t3\n"
    assert subprocess.run([sys.executable, "gen.py", "--check"], cwd=proj).returncode == 0
    assert _git(proj, "status", "--porcelain", "--untracked-files=no").strip() == ""
    assert {r["state"] for r in _status(proj).values()} == {"merged"}
    assert _ci_outcome(proj, "T3") == "passed"
    # the later branches' pins conflicted with what had landed; they were re-recorded, not merged
    assert "regenerate pins: demo" in _git(proj, "log", "--format=%s")


def test_a_failed_entry_is_parked_and_does_not_block_the_next(proj):
    _finish(proj, "T1", "BAD\n")  # ci fails on it
    _finish(proj, "T2")
    for item in ("T1", "T2"):
        assert _queue(proj, item)[0] == 0
    code, out, err = run_cli(proj, "--json", "queue", "run", agent="integrator")
    assert code == 1, out + err
    rows = {p["item"]: p for p in json.loads(out)["processed"]}
    assert rows["T1"]["state"] == "parked" and "ci" in rows["T1"]["reason"]
    assert rows["T2"]["state"] == "merged"
    assert _landed(proj) == ["T2"]
    assert _status(proj)["T1"]["state"] == "parked"
    assert "ci" in _status(proj)["T1"]["reason"]


def test_two_integrators_do_not_run_at_once(proj):
    _finish(proj, "T1")
    assert _queue(proj, "T1")[0] == 0
    (proj.parent / "slow").write_text("3")  # ci takes three seconds
    first = subprocess.Popen(
        [sys.executable, "-m", "ddflow", "--repo", str(proj), "--agent", "one", "queue", "run"],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(200):
            if MQ.holder(proj):
                break
            time.sleep(0.05)
        assert MQ.holder(proj), "the first integrator never took the lock"
        code, out, err = run_cli(proj, "queue", "run", agent="two")
        assert code == 2, out + err  # not 0: it did nothing
        assert "another integrator" in err + out
        assert "running" in run_cli(proj, "queue", "status")[1]
    finally:
        first.communicate(timeout=120)
    assert first.returncode == 0
    assert _landed(proj) == ["T1"]
    assert MQ.holder(proj) is None


def test_gates_are_still_required_to_queue_and_to_run(proj):
    _finish(proj, "T1")
    _finish(proj, "T2")
    # a gate that was never recorded: refused at the door, naming the gate
    run_cli(proj, "task", "add", "T3", "--title", "no gates", "--globs", "knobs/t3.txt")
    run_cli(proj, "claim", "T3", agent="t3")
    code, out, err = _queue(proj, "T3")
    assert code == 3 and "rubber_duck" in err + out, out + err
    assert MQ.get(proj, "T3") is None
    # a gate that fails after it was queued: parked unrun, not landed
    assert _queue(proj, "T1")[0] == 0
    assert _queue(proj, "T2")[0] == 0
    rec = run_cli(
        proj,
        "gate",
        "record",
        "T1",
        "implement",
        "--outcome",
        "failed",
        "--reason",
        "found",
        "--evidence",
        "x",
    )
    assert rec[0] == 0, rec
    code, out, err = run_cli(proj, "--json", "queue", "run", agent="integrator")
    assert code == 1, out + err
    rows = {p["item"]: p for p in json.loads(out)["processed"]}
    assert rows["T1"]["state"] == "parked" and "implement" in rows["T1"]["reason"]
    assert rows["T2"]["state"] == "merged"
    assert _landed(proj) == ["T2"]


def test_max_batch_bounds_a_run_and_status_orders_the_rest(proj):
    for item in ("T1", "T2"):
        _finish(proj, item)
        assert _queue(proj, item)[0] == 0
    code, out, err = run_cli(proj, "queue", "run", "--max-batch", "1", agent="integrator")
    assert code == 0, out + err
    assert _landed(proj) == ["T1"]
    rows = _status(proj)
    assert rows["T1"]["state"] == "merged" and rows["T2"]["state"] == "queued"
    assert rows["T2"]["position"] == 1 and rows["T2"]["eta_s"] > 0
    assert run_cli(proj, "queue", "run", agent="integrator")[0] == 0
    assert _landed(proj) == ["T1", "T2"]
    assert run_cli(proj, "queue", "run", agent="integrator")[0] == 2  # empty: nothing to do


def test_the_knobs_are_overridable_per_run_and_by_config(proj):
    _finish(proj, "T1")
    assert _queue(proj, "T1")[0] == 0
    # config: unit_tests on -> the integrator runs the gate, which this project cannot run
    err, _ = append_config(proj, "[merge_queue]\nrun_unit_tests = true\n")
    assert not err
    code, out, err = run_cli(proj, "--json", "queue", "run", agent="integrator")
    body = json.loads(out)
    assert code == 1 and "unit_tests" in body["processed"][0]["reason"], out + err
    # the CLI flag beats the config for one run: the same entry, added again, now lands
    assert _queue(proj, "T1")[0] == 0
    code, out, err = run_cli(proj, "queue", "run", "--no-unit-tests", agent="integrator")
    assert code == 0, out + err
    assert _landed(proj) == ["T1"]
    # bad numbers are refused, not clamped
    assert run_cli(proj, "queue", "run", "--max-batch", "0")[0] == 3
