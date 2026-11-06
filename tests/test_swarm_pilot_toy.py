"""The toy phase and baseline runner of the swarm pilot (SW-pilot-toy): sandbox only, no model."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "demos"), str(ROOT)]

import swarm_pilot_run as R  # noqa: E402
import swarm_pilot_toy as T  # noqa: E402

from ddflow.services import swarm_metrics as S  # noqa: E402
from tests.sandbox import scrub_git_env  # noqa: E402


@pytest.fixture(autouse=True)
def _no_git_redirect(monkeypatch):
    """A hook's GIT_DIR must not send this file's own git calls to the host."""
    scrub_git_env(monkeypatch)


def _git(repo: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *argv], capture_output=True, text=True, check=True
    ).stdout.strip()


def _chain(item: T.ToyItem, by_id: dict[str, T.ToyItem]) -> int:
    return 1 + max((_chain(by_id[d], by_id) for d in item.needs), default=0)


def test_the_toy_phase_is_twelve_items_with_disjoint_globs():
    assert len(T.ITEMS) == 12
    assert len({i.id for i in T.ITEMS}) == 12
    globs = [g for i in T.ITEMS for g in i.globs]
    assert len(globs) == len(set(globs)) == 24


def test_the_toy_graph_leaves_room_for_three_lanes():
    by_id = {i.id: i for i in T.ITEMS}
    assert all(d in by_id for i in T.ITEMS for d in i.needs)
    assert max(_chain(i, by_id) for i in T.ITEMS) == 4  # 12 items over a chain of 4: width 3
    assert sum(not i.needs for i in T.ITEMS) >= 3


def test_one_toy_item_is_sent_back_by_its_gate():
    assert [i.rework for i in T.ITEMS].count(1) == 1 and max(i.rework for i in T.ITEMS) == 1


# -- the sandbox never touches the host ------------------------------------------------------


def test_a_sandbox_inside_the_host_is_refused():
    with pytest.raises(T.Refused, match="inside"):
        T.guard_new(ROOT / ".ddflow" / "pilot-sandbox")


def test_the_host_itself_is_refused():
    with pytest.raises(T.Refused):
        T.guard_new(ROOT)
    with pytest.raises(T.Refused):
        T.Sandbox.open(ROOT)


def test_a_parent_of_the_host_is_refused():
    with pytest.raises(T.Refused, match="holds the host"):
        T.guard_new(ROOT.parent)


def test_opening_a_parent_of_the_host_is_refused():
    with pytest.raises(T.Refused, match="holds the host"):
        T.Sandbox.open(ROOT.parent)


def test_no_constructor_route_reaches_the_host():
    with pytest.raises(T.Refused, match="inside"):
        T.Sandbox(ROOT)


def test_a_link_in_the_host_keeps_home_and_temp_files_out_of_it(tmp_path, monkeypatch):
    """Through a link the repository resolves away from the host; its home must follow it."""
    host, outside = tmp_path / "host", tmp_path / "sb"
    host.mkdir()
    outside.mkdir()
    monkeypatch.setattr(T, "ROOT", host)
    (host / "out").symlink_to(outside)
    sb = T.Sandbox(host / "out")
    assert sb.repo == outside.resolve() and sb.home == tmp_path.resolve() / "sb.home"
    assert sb.env["TMPDIR"] == str(sb.home)


def test_a_home_that_links_into_the_host_is_refused(tmp_path, monkeypatch):
    host = tmp_path / "host"
    host.mkdir()
    monkeypatch.setattr(T, "ROOT", host)
    (tmp_path / "sb.home").symlink_to(host)
    with pytest.raises(T.Refused, match="inside"):
        T.Sandbox(tmp_path / "sb")


def test_a_home_that_is_the_host_is_refused(tmp_path, monkeypatch):
    host = tmp_path / "pilot.home"
    host.mkdir()
    monkeypatch.setattr(T, "ROOT", host)
    with pytest.raises(T.Refused, match="inside"):
        T.Sandbox(tmp_path / "pilot")


def test_a_symlink_into_the_host_is_refused(tmp_path):
    link = tmp_path / "link"
    link.symlink_to(ROOT / "docs")
    with pytest.raises(T.Refused, match="inside"):
        T.guard_new(link / "pilot")


def test_a_sandbox_inside_another_repository_is_refused(tmp_path):
    outer = tmp_path / "outer"
    outer.mkdir()
    subprocess.run(["git", "init", "-q", str(outer)], check=True)
    with pytest.raises(T.Refused, match="work tree"):
        T.guard_new(outer / "pilot")


def test_a_directory_with_files_in_it_is_refused(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "keep.txt").write_text("mine")
    with pytest.raises(T.Refused, match="not empty"):
        T.guard_new(tmp_path / "x")


def test_a_repository_that_is_no_sandbox_is_refused(tmp_path):
    repo = tmp_path / "real"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    with pytest.raises(T.Refused, match="no sandbox"):
        T.Sandbox.open(repo)


def test_a_linked_worktree_with_a_manifest_is_refused(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    subprocess.run(["git", "init", "-q", str(main)], check=True)
    subprocess.run(
        ["git", "-C", str(main), "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-q",
         "--allow-empty", "-m", "x"],
        check=True,
    )  # fmt: skip
    linked = tmp_path / "linked"
    _git(main, "worktree", "add", "-q", str(linked))
    (linked / T.MANIFEST).parent.mkdir(parents=True)
    (linked / T.MANIFEST).write_text("{}")
    with pytest.raises(T.Refused, match="own git work tree"):
        T.Sandbox.open(linked)


def test_a_repository_with_a_remote_is_refused(tmp_path):
    repo = tmp_path / "real"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "remote", "add", "origin", "https://example.invalid/x.git")
    (repo / T.MANIFEST).parent.mkdir(parents=True)
    (repo / T.MANIFEST).write_text("{}")
    with pytest.raises(T.Refused, match="remote"):
        T.Sandbox.open(repo)


def test_the_environment_carries_no_redirect_and_no_key(tmp_path, monkeypatch):
    for key in ("GIT_DIR", "GIT_WORK_TREE", "DDFLOW_AGENT", "KIMI_API_KEY", "PYTHONPATH"):
        monkeypatch.setenv(key, "/elsewhere")
    env = T.clean_env(tmp_path)
    assert env["HOME"] == str(tmp_path) and env["PYTHONPATH"] == str(ROOT)
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert not [k for k in env if k.startswith("DDFLOW") or k.endswith("_KEY")]
    assert not [k for k in env if k in ("GIT_DIR", "GIT_WORK_TREE")]


def test_temp_files_and_path_entries_stay_out_of_the_host(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(ROOT / "tmp"))
    monkeypatch.setenv("PATH", f"{ROOT / 'bin'}{os.pathsep}/usr/bin")
    env = T.clean_env(tmp_path)
    assert env["TMPDIR"] == str(tmp_path) and env["PATH"] == "/usr/bin"


def test_a_file_is_no_place_for_a_sandbox(tmp_path):
    (tmp_path / "f").write_text("x")
    with pytest.raises(T.Refused, match="not empty"):
        T.guard_new(tmp_path / "f")


def _sandbox(tmp_path: Path, **kw) -> T.Sandbox:
    return T.setup(tmp_path / "sb", **kw)


@pytest.fixture(scope="module")
def tiny(tmp_path_factory) -> T.Sandbox:
    """Three items by two stub lanes: every real verb, in a real repository."""
    base = tmp_path_factory.mktemp("pilot")
    sb = T.setup(base / "sb", items=T.ITEMS[:3], min_samples=1)
    R.run(sb, 2, R.StubWorker(0.0), max_s=240)
    return sb


def test_the_guard_runs_on_every_verb_a_sandbox_is_given(tiny):
    for verb in ("review", "lanes", "config", "reviewers"):
        with pytest.raises(T.Refused, match="not a pilot verb"):
            tiny.ddflow(verb)
    with pytest.raises(T.Refused, match="--force"):
        tiny.ddflow("complete", "toy-01", "--force")


@pytest.mark.parametrize(
    "argv",
    [
        ("--git-dir", "/x/.git", "status"),
        ("config", "core.worktree", "/x"),
        ("config", "user.email", "x", "--file", "/x/.git/config"),
        ("config",),
        ("push", "origin"),
        (),
    ],
)
def test_git_cannot_be_pointed_elsewhere(tiny, argv):
    with pytest.raises(T.Refused, match="not a pilot verb"):
        tiny.git(*argv)


def test_git_cannot_run_outside_the_sandbox(tiny):
    with pytest.raises(T.Refused, match="not a pilot verb"):
        tiny.git("add", "-A", cwd=ROOT)


def test_a_second_run_of_one_sandbox_is_refused(tiny):
    with pytest.raises(T.Refused, match="already ran"):
        R.run(T.Sandbox.open(tiny.repo), 1, R.StubWorker(0.0), max_s=1)


def test_a_run_needs_a_lane(tmp_path):
    sb = _sandbox(tmp_path, items=T.ITEMS[:1], min_samples=1)
    with pytest.raises(T.Refused, match="at least one lane"):
        R.run(sb, 0, R.StubWorker(0.0), max_s=1)
    assert "lanes" not in sb.manifest()


def test_a_host_that_cannot_answer_is_not_called_isolated(tiny, monkeypatch):
    monkeypatch.setattr(T, "ROOT", tiny.repo / "no-such-dir")
    with pytest.raises(RuntimeError, match="cannot ask the host"):
        R._isolated(tiny)


def test_a_tag_named_main_does_not_hide_a_known_commit(tmp_path, monkeypatch):
    """The host holds the commit but not the tag object: only the peeled name finds it."""
    sb = _sandbox(tmp_path, items=T.ITEMS[:1], min_samples=1)
    host = tmp_path / "host"
    subprocess.run(["git", "clone", "-q", "--no-tags", str(sb.repo), str(host)], check=True)
    subprocess.run(
        ["git", "-C", str(sb.repo), "tag", "-a", "main", "-m", "x"], check=True, env=sb.env
    )
    tag = _git(sb.repo, "rev-parse", "refs/tags/main")
    missing = subprocess.run(["git", "-C", str(host), "cat-file", "-e", tag], check=False)
    assert missing.returncode == 1
    monkeypatch.setattr(T, "ROOT", host)
    assert not R._isolated(sb)


def test_a_sandbox_whose_head_the_host_knows_is_not_isolated(tiny, monkeypatch):
    monkeypatch.setattr(T, "ROOT", tiny.repo)  # its own head: as if the host held it
    assert not R._isolated(tiny)


def test_a_real_run_merges_every_item_on_the_sandbox_main_only(tiny):
    got = R.side(tiny)
    assert got["merged"] == 3 and got["overrides"] == 0 and got["isolated"]
    head = _git(tiny.repo, "rev-parse", "main")
    host = subprocess.run(
        ["git", "-C", str(ROOT), "cat-file", "-e", f"{head}^{{commit}}"], check=False
    )
    assert host.returncode != 0
    assert (tiny.repo / "toy" / "toy-03.md").is_file()
    assert _git(tiny.repo, "remote") == ""


def test_the_run_leaves_no_trace_in_the_host_log(tiny):
    common = Path(_git(ROOT, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    host_log = common.parent / ".ddflow" / "events"
    assert host_log.is_dir()
    assert not [p for p in host_log.glob("*.jsonl") if p.stem.startswith(T.AGENT_PREFIX)]


def test_the_manifest_says_what_ran(tiny):
    m = json.loads((tiny.repo / T.MANIFEST).read_text())
    assert m["worker"] == "stub" and m["lanes"] == 2 and m["live_model"] is False
    assert m["items"] == ["toy-01", "toy-02", "toy-03"]


def test_no_review_gate_is_in_the_sandbox_pipeline(tiny):
    code, out, _ = tiny.ddflow("show", "toy-01")
    assert code == 0 and "rubber_duck" not in out and "critic" not in out


# -- the report ---------------------------------------------------------------------------------


def _figures(per_hour=2.0, hours=0.5, rate=0.1, merged=12) -> dict:
    return {
        "merged": merged,
        "throughput": {"per_hour": per_hour, "wall_s": 3600.0},
        "rework": {"rate": rate},
        "cost": {"usd_per_item": None, "agent_hours_per_item": hours},
        "critical_path": {"length": 4, "slack_s": 1.0, "parallelism": 1.0},
        "unavailable": {},
    }


def _side(lanes=1, worker="stub", overrides=0, isolated=True, work_s=1.0, **fig) -> dict:
    return {
        "lanes": lanes,
        "worker": worker,
        "live_model": False,
        "work_s": work_s,
        "items": 12,
        "overrides": overrides,
        "isolated": isolated,
        **_figures(**fig),
    }


def test_the_report_states_the_cost_target_is_not_measured():
    rep = R.judge(_side(), _side(lanes=3, per_hour=4.0, hours=0.6, rate=0.1))
    assert rep["compare"]["targets"] == {"throughput": True, "cost": None, "rework": True}
    text = R.render(rep)
    assert "agent-hours" in text and "not dollars" in text and S.COST_NOT_MEASURED in text
    assert "1.3x" in text and "NOT MEASURED" in text


def test_the_report_of_stub_runs_says_it_is_no_pilot_result():
    text = R.render(R.judge(_side(), _side(lanes=3)))
    assert "stub" in text and "not a pilot result" in text


def test_a_missed_throughput_target_is_said():
    rep = R.judge(_side(per_hour=2.0), _side(lanes=3, per_hour=3.0))
    assert rep["compare"]["targets"]["throughput"] is False and "NOT MET" in R.render(rep)


def test_rework_outside_five_points_is_said():
    rep = R.judge(_side(rate=0.1), _side(lanes=3, rate=0.2, per_hour=9.0))
    assert rep["compare"]["targets"]["rework"] is False


@pytest.mark.parametrize(
    ("after", "word"),
    [
        ({"worker": "hand"}, "worker"),
        ({"work_s": 2.0}, "work_s"),
        ({"merged": 11}, "merged"),
        ({"overrides": 1}, "override"),
        ({"isolated": False}, "isolated"),
    ],
)
def test_a_pair_that_cannot_be_compared_is_not_judged(after, word):
    rep = R.judge(_side(), _side(lanes=3, **after))
    assert not rep["comparable"] and any(word in why for why in rep["why_not"])
    assert set(rep["compare"]["targets"].values()) == {None}  # the JSON judges nothing either
    text = R.render(rep)
    assert "NOT COMPARABLE" in text and "met" not in text.replace("NOT MEASURED", "").lower()


@pytest.mark.parametrize(
    ("before", "after"),
    [(3, 1), (3, 3), (None, None), (None, 3), (0, 3), (-1, 3), (1, None), (True, 3)],
)
def test_the_baseline_must_have_the_fewer_lanes(before, after):
    rep = R.judge(_side(lanes=before), _side(lanes=after, per_hour=9.0))
    assert not rep["comparable"] and any("lanes" in why for why in rep["why_not"])


def test_a_missing_figure_is_na_not_zero():
    rep = R.judge(_side(), _side(lanes=3, per_hour=None))
    assert rep["compare"]["targets"]["throughput"] is None
    assert "n/a" in R.render(rep)


def test_a_failing_lane_stops_the_run_and_never_forces(tmp_path):
    sb = _sandbox(tmp_path, items=T.ITEMS[:1], min_samples=1)

    class Broken:
        label, work_s = "stub", 0.0

        def work(self, tree, item, attempt):
            raise OSError("disk full")

    with pytest.raises(R.RunFailed, match="disk full"):
        R.run(sb, 1, Broken(), max_s=60)
    assert (
        sb.ddflow("gate", "list", "--overrides")[1].strip() == "no gate was passed by an override"
    )


def test_a_finished_board_never_reports_a_deadline():
    board = R.Board(["toy-01"], 0)
    board.finish("toy-01")
    assert board.over() and board.failure == ""


def test_the_deadline_reads_the_items_under_the_lock():
    """Unlocked, a lane finishing beside it raised `Set changed size` and failed a finished run."""
    board = R.Board(["toy-01"], 0)

    class Watched(set):
        def __bool__(self):
            assert board.lock.locked()
            return len(self) > 0

        def __iter__(self):
            assert board.lock.locked()
            return super().__iter__()

    board.left = Watched(board.left)
    assert board.over() and "deadline" in board.failure


def test_a_lane_never_claims_what_another_lane_took():
    """B-merged-uncompleted-claimable: `next` offers a merged item until it is completed."""
    board = R.Board(["toy-01"], 60)
    assert board.take("toy-01") and not board.take("toy-01")
    board.release("toy-01")
    assert board.take("toy-01")


def test_the_run_stops_at_its_deadline(tmp_path):
    sb = _sandbox(tmp_path, items=T.ITEMS[:1], min_samples=1)
    with pytest.raises(R.RunFailed, match="deadline"):
        R.run(sb, 1, R.StubWorker(0.0), max_s=0)


def test_the_host_git_environment_cannot_redirect_a_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_DIR", str(ROOT / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(ROOT))
    before = subprocess.run(
        ["git", "--git-dir", str(ROOT / ".git"), "worktree", "list"],
        capture_output=True,
        text=True,
        check=True,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    ).stdout
    sb = _sandbox(tmp_path, items=T.ITEMS[:1], min_samples=1)
    assert sb.git("rev-parse", "--show-toplevel").strip() == str(sb.repo)
    after = subprocess.run(
        ["git", "--git-dir", str(ROOT / ".git"), "worktree", "list"],
        capture_output=True,
        text=True,
        check=True,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    ).stdout
    assert str(sb.repo) not in after and str(sb.repo) not in before


@pytest.mark.slow
def test_baseline_and_three_lanes_full_toy_phase(tmp_path):
    """The whole harness at its real size: 12 items, one lane against three, stub workers."""
    one = T.setup(tmp_path / "one")
    three = T.setup(tmp_path / "three")
    R.run(one, 1, R.StubWorker(1.0), max_s=900)
    R.run(three, 3, R.StubWorker(1.0), max_s=900)
    rep = R.judge(R.side(one), R.side(three))
    assert rep["comparable"], rep["why_not"]
    assert rep["before"]["merged"] == rep["after"]["merged"] == 12
    assert rep["compare"]["targets"]["cost"] is None
    assert rep["compare"]["rework_points"] == 0
