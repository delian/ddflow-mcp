"""Run the last failed tests first and record the time to the first failure (FF-failed-first).

A rerun after a fix executed the whole selected set in default order, so a test that was still
broken reported at the end of a run that costs minutes. The ordering is done by ddflow from its
own gate evidence (not pytest's cache: ddflow runs in scratch worktrees with xdist) and handed
to the project's pytest as a plugin plus a file named by an environment variable."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ddflow.config import Config
from ddflow.services.ci import failed_first as FF

SLOW = 0.1  # seconds each fixture test takes: 36 tests before the failing one cost 3.6 s


@pytest.fixture(autouse=True)
def _outside_any_failed_first_run(monkeypatch):
    """This file runs inside `ddflow ci run`, which is itself a failed-first run: the outer run's
    variables must not leak into the runs these tests start."""
    for name in (FF.IDS_ENV, FF.OUT_ENV, FF.ACTIVE_ENV):
        monkeypatch.delenv(name, raising=False)


def _toml(text: str) -> str:
    """``text`` as a TOML basic string (JSON escapes, astral characters kept raw): shell quoting is not TOML quoting."""
    return json.dumps(text, ensure_ascii=False)


def _suite(root: Path, n: int = 40, failing: int = 37) -> Path:
    """A fixture suite of ``n`` tests where test ``failing`` fails."""
    body = ["import time", ""]
    for i in range(1, n + 1):
        body += [f"def test_{i:02d}():", f"    time.sleep({SLOW})"]
        body += ["    assert False, 'broken'"] if i == failing else ["    assert True"]
        body.append("")
    (root / "test_many.py").write_text("\n".join(body))
    return root


def _pytest(root: Path, env: dict[str, str]) -> list[str]:
    """The test ids in the order pytest ran them."""
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-v",
            "--color=no",
            "--rootdir",
            str(root),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return [
        ln.split(" ")[0] for ln in run.stdout.splitlines() if "::test_" in ln.split(" ")[0:1][0]
    ]


def _env(extra: dict[str, str]) -> dict[str, str]:
    import os

    return {
        k: v
        for k, v in os.environ.items()
        if k not in ("PYTEST_ADDOPTS", "PYTHONPATH", "PY_COLORS")
        and not k.startswith("DDFLOW_FAILED_FIRST_")
    } | extra


def test_default_order_reports_the_failure_at_its_position(tmp_path):
    root = _suite(tmp_path)
    ran = _pytest(root, _env({}))
    assert ran.index("test_many.py::test_37") == 36  # no ordering hook today


def test_the_rerun_with_the_ids_runs_the_failing_test_first(tmp_path):
    root = _suite(tmp_path)
    with FF.session(FF.Plan(ids=("test_many.py::test_37",)), parent_env=_env({})) as sess:
        ran = _pytest(root, sess.env)
        found = sess.outcome()
    assert ran[0] == "test_many.py::test_37"
    assert sorted(ran) == sorted(f"test_many.py::test_{i:02d}" for i in range(1, 41))
    assert found["ran_failed_first"] == ["test_many.py::test_37"]  # noted by the plugin itself
    # the failure is reported before the second test could have finished: far below the 3.7 s
    # the default order needs, and below the time of test 1 plus 2 s
    assert found["first_failure_at_s"] < SLOW + 2.0


def test_tests_of_touched_files_come_after_the_failed_ids_and_before_the_rest(tmp_path):
    root = tmp_path
    (root / "test_a.py").write_text("def test_a1(): pass\ndef test_a2(): pass\n")
    (root / "test_b.py").write_text("def test_b1(): pass\ndef test_b2(): pass\n")
    (root / "test_c.py").write_text("def test_c1(): pass\n")
    plan = FF.Plan(ids=("test_c.py::test_c1",), files=("test_b.py",))
    with FF.session(plan, parent_env=_env({})) as sess:
        ran = _pytest(root, sess.env)
    assert ran == [
        "test_c.py::test_c1",
        "test_b.py::test_b1",
        "test_b.py::test_b2",
        "test_a.py::test_a1",
        "test_a.py::test_a2",
    ]


def test_a_missing_ids_file_is_the_ordinary_order_never_an_error(tmp_path):
    (tmp_path / "test_a.py").write_text("def test_a1(): pass\ndef test_a2(): pass\n")
    with FF.session(FF.Plan(ids=("test_a.py::test_a2",)), parent_env=_env({})) as sess:
        Path(sess.env[FF.IDS_ENV]).unlink()
        ran = _pytest(tmp_path, sess.env)
    assert ran == ["test_a.py::test_a1", "test_a.py::test_a2"]


def test_the_session_keeps_the_environment_the_project_already_set(tmp_path):
    parent = _env({"PYTEST_ADDOPTS": "-q", "PYTHONPATH": "/x/y"})
    with FF.session(FF.Plan(ids=("a::b",)), parent_env=parent) as sess:
        assert "PYTEST_ADDOPTS" not in sess.env  # no `-p`: a lost PYTHONPATH must not break pytest
        assert sess.env["PYTHONPATH"].endswith("/x/y")
        plugin_dir = Path(sess.env["PYTHONPATH"].split(":")[0])
        assert (plugin_dir / f"{FF.PLUGIN}.py").is_file()
        assert (
            "[pytest11]"
            in (plugin_dir / f"{FF.PLUGIN}-0.dist-info" / "entry_points.txt").read_text()
        )
    assert not plugin_dir.exists()  # a run leaves nothing behind


def test_a_pytest_that_lost_pythonpath_runs_normally_and_orders_nothing(tmp_path):
    (tmp_path / "test_a.py").write_text("def test_a1(): pass\ndef test_a2(): pass\n")
    with FF.session(FF.Plan(ids=("test_a.py::test_a2",)), parent_env=_env({})) as sess:
        bare = {k: v for k, v in sess.env.items() if k != "PYTHONPATH"}
        ran = _pytest(tmp_path, _env(bare))
        assert sess.outcome().get("ran_failed_first", []) == []
    assert ran == ["test_a.py::test_a1", "test_a.py::test_a2"]


def test_a_pytest_a_test_starts_inside_the_run_stands_down(tmp_path):
    """A test that runs pytest itself (a fixture project, a bisect) must not order its tests by
    the outer run's ids or note its own failures into the outer run's evidence."""
    (tmp_path / "test_a.py").write_text("def test_a1(): pass\ndef test_a2(): pass\n")
    outer = tmp_path / "outer.py"
    outer.write_text(
        "import subprocess, sys\n"
        "def test_outer():\n"
        "    r = subprocess.run([sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider', '-v',"
        " '--color=no', 'test_a.py'], capture_output=True, text=True)\n"
        "    ids = [l.split()[0] for l in r.stdout.splitlines() if l.startswith('test_a.py::')]\n"
        "    assert ids == ['test_a.py::test_a1', 'test_a.py::test_a2'], r.stdout\n"
    )
    plan = FF.Plan(ids=("test_a.py::test_a2",))
    with FF.session(plan, parent_env=_env({})) as sess:
        run = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "outer.py"],
            cwd=tmp_path, env=sess.env, capture_output=True, text=True, timeout=120,
        )  # fmt: skip
        assert run.returncode == 0, run.stdout + run.stderr
        rows = [json.loads(x) for x in Path(sess.env[FF.OUT_ENV]).read_text().splitlines()]
    assert [r["ev"] for r in rows] == [
        "start"
    ]  # the outer session only; the nested one noted nothing


def test_no_plan_no_session(tmp_path):
    with FF.session(FF.Plan(), parent_env=_env({})) as sess:
        assert sess.env == {}
        assert sess.outcome() == {}


def _item(**gates):
    return SimpleNamespace(
        gates={
            g: SimpleNamespace(outcome=o, evidence={"failed_tests": ids})
            for g, (o, ids) in gates.items()
        }
    )


def test_previous_ids_are_the_last_failed_ci_and_unit_tests_ids_capped(tmp_path):
    it = _item(
        ci=("failed", ["t.py::a", "t.py::b", "t.py::c"]),
        unit_tests=("failed", ["t.py::b", "u.py::d"]),
    )
    assert FF.previous_failing_ids(tmp_path, it, 50) == ["t.py::a", "t.py::b", "t.py::c", "u.py::d"]
    assert FF.previous_failing_ids(tmp_path, it, 2) == ["t.py::a", "t.py::b"]
    assert FF.previous_failing_ids(tmp_path, it, 0) == []


def test_a_passed_gate_leaves_nothing_to_run_first(tmp_path):
    assert FF.previous_failing_ids(tmp_path, _item(ci=("passed", ["t.py::a"])), 50) == []
    assert FF.previous_failing_ids(tmp_path, None, 50) == []


def test_the_whole_list_comes_from_the_kept_blob_when_the_evidence_cut_it(tmp_path):
    from ddflow.services.gates import baseline as BL

    stub = BL.write_full(tmp_path, "abc123", [f"t.py::t{i}" for i in range(80)])
    it = SimpleNamespace(
        gates={
            "ci": SimpleNamespace(
                outcome="failed",
                evidence={
                    "failed_tests": ["t.py::t0"],
                    "failed_tests_more": 79,
                    "failing_ids": stub,
                },
            )
        }
    )
    got = FF.previous_failing_ids(tmp_path, it, 50, BL.read_full)
    assert got == [f"t.py::t{i}" for i in range(50)]


def test_the_knobs_are_declared_with_their_defaults():
    cfg = Config.load(Path("/nonexistent-ddflow-repo"))
    assert cfg.suites.failed_first is True
    assert cfg.suites.failed_first_max_ids == 50


def test_the_knob_can_be_set_by_the_config_file(tmp_path):
    (tmp_path / ".ddflow").mkdir()
    (tmp_path / ".ddflow" / "config.toml").write_text(
        "[suites]\nfailed_first = false\nfailed_first_max_ids = 3\n"
    )
    cfg = Config.load(tmp_path)
    assert cfg.suites.failed_first is False
    assert cfg.suites.failed_first_max_ids == 3


def test_evidence_is_written_as_json_lines_the_run_can_read(tmp_path):
    out = tmp_path / "o.jsonl"
    out.write_text(
        json.dumps({"ev": "start", "at": 10.0})
        + "\n"
        + json.dumps({"ev": "fail", "at": 10.5, "id": "t.py::x"})
        + "\n"
        + json.dumps({"ev": "fail", "at": 11.0, "id": "t.py::y"})
        + "\n"
    )
    out.write_text(out.read_text() + json.dumps({"ev": "ordered", "ids": ["t.py::x"]}) + "\n")
    got = FF.read_outcome(out)
    assert got == {
        "first_failure_at_s": 0.5,
        "first_failure_id": "t.py::x",
        "ran_failed_first": ["t.py::x"],
    }


@pytest.mark.parametrize("text", ["", "not json\n", '{"ev":"start","at":1}\n'])
def test_an_unreadable_or_failure_free_record_says_nothing_of_the_time(tmp_path, text):
    out = tmp_path / "o.jsonl"
    out.write_text(text)
    got = FF.read_outcome(out)
    assert "first_failure_at_s" not in got and "ran_failed_first" not in got


def test_the_failed_first_action_lists_what_the_next_run_would_put_first(repo, monkeypatch):
    import json

    from conftest import run_cli
    from test_ci_gate import _item_with_ci

    from ddflow.api import cirun as AR

    _item_with_ci(repo, monkeypatch, agent="holder-ff")
    out = AR.ci_tool(repo, action="failed-first", ref="T1", agent="holder-ff")
    assert out.exit == 0
    assert out.data["item"] == "T1" and out.data["ids"] == [] and out.data["enabled"] is True
    code, text, _ = run_cli(repo, "--json", "ci", "failed-first", "--ref", "T1")
    assert code == 0 and json.loads(text)["item"] == "T1"
    assert AR.ci_tool(repo, action="failed-first", ref="NOPE", agent="x").exit == 2


def test_a_rerun_of_an_item_is_handed_the_ids_its_last_ci_run_failed(repo, monkeypatch, tmp_path):
    from test_ci_gate import _ci_gate, _item_with_ci

    from ddflow.api import cirun as AR

    _item_with_ci(repo, monkeypatch, agent="holder-ff2")
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_x.py").write_text("def test_a(): pass\ndef test_b(): pass\n")
    cfg = repo / ".ddflow" / "config.toml"
    pipeline = '[gates]\ntask_pipeline = ["implement", "ci", "merge"]\n'
    fail = "echo FAILED test_x.py::test_b - boom; false"
    cfg.write_text(f"[ci]\ncommand = {_toml(fail)}\n{pipeline}")
    first = AR.run(repo, ref="T1", agent="holder-ff2")
    assert first.exit == 1, first.data
    assert "ran_failed_first" not in (_ci_gate(repo)["evidence"].get("ci_scope") or {})
    run = f"cd {shlex.quote(str(suite))} && {shlex.quote(sys.executable)} -m pytest -p no:cacheprovider -q"
    cfg.write_text(f"[ci]\ncommand = {_toml(run)}\n{pipeline}")
    second = AR.run(repo, ref="T1", agent="holder-ff2")
    assert second.exit == 0, second.data
    scope = _ci_gate(repo)["evidence"]["ci_scope"]
    assert scope["ran_failed_first"] == ["test_x.py::test_b"]  # what the plugin really ordered


def test_a_run_the_plugin_never_reached_claims_nothing(repo, monkeypatch, tmp_path):
    from test_ci_gate import _ci_gate, _item_with_ci

    from ddflow.api import cirun as AR

    _item_with_ci(repo, monkeypatch, agent="holder-ff5")
    cfg = repo / ".ddflow" / "config.toml"
    pipeline = '[gates]\ntask_pipeline = ["implement", "ci", "merge"]\n'
    cfg.write_text(f"[ci]\ncommand = {_toml('echo FAILED t.py::test_a - boom; false')}\n{pipeline}")
    AR.run(repo, ref="T1", agent="holder-ff5")
    seen = tmp_path / "seen.json"
    cp = f'cp "${FF.IDS_ENV}" {shlex.quote(str(seen))}'
    cfg.write_text(f"[ci]\ncommand = {_toml(cp)}\n{pipeline}")
    assert AR.run(repo, ref="T1", agent="holder-ff5").exit == 0
    assert json.loads(seen.read_text())["ids"] == ["t.py::test_a"]  # it was handed the ids
    assert "ran_failed_first" not in (_ci_gate(repo)["evidence"].get("ci_scope") or {})


def test_the_knob_turns_the_ordering_off(repo, monkeypatch, tmp_path):
    from test_ci_gate import _item_with_ci

    from ddflow.api import cirun as AR

    _item_with_ci(repo, monkeypatch, agent="holder-ff3")
    cfg = repo / ".ddflow" / "config.toml"
    pipeline = (
        '[gates]\ntask_pipeline = ["implement", "ci", "merge"]\n[suites]\nfailed_first = false\n'
    )
    cfg.write_text(f'[ci]\ncommand = "echo FAILED t.py::test_a - boom; false"\n{pipeline}')
    AR.run(repo, ref="T1", agent="holder-ff3")
    seen = tmp_path / "seen.txt"
    echo = f'echo "[${FF.IDS_ENV}]" > {shlex.quote(str(seen))}'
    cfg.write_text(f"[ci]\ncommand = {_toml(echo)}\n{pipeline}")
    assert AR.run(repo, ref="T1", agent="holder-ff3").exit == 0
    assert seen.read_text().strip() == "[]"


def test_the_outcome_is_read_by_timestamp_not_by_line_order(tmp_path):
    out = tmp_path / "o.jsonl"
    rows = [
        {"ev": "fail", "at": 12.0, "id": "t.py::late"},
        {"ev": "fail", "at": 10.5, "id": "t.py::early"},
        {"ev": "start", "at": 10.0},
    ]
    out.write_text("".join(json.dumps(r) + "\n" for r in rows))
    got = FF.read_outcome(out)
    assert got["first_failure_at_s"] == 0.5 and got["first_failure_id"] == "t.py::early"


def test_a_second_session_in_one_interpreter_notes_its_own_failure(tmp_path):
    (tmp_path / "test_a.py").write_text("def test_a():\n    assert False\n")
    with FF.session(FF.Plan(ids=("test_a.py::test_a",)), parent_env=_env({})) as sess:
        code = (
            "import pytest; "
            "pytest.main(['-q', '-p', 'no:cacheprovider']); "
            "pytest.main(['-q', '-p', 'no:cacheprovider'])"
        )
        subprocess.run(
            [sys.executable, "-c", code],
            cwd=tmp_path,
            env=sess.env,
            capture_output=True,
            timeout=60,
        )
        rows = [json.loads(x) for x in Path(sess.env[FF.OUT_ENV]).read_text().splitlines()]
    assert [r["ev"] for r in rows if r["ev"] != "ordered"] == ["start", "fail", "start", "fail"]


def test_the_failed_first_action_refuses_the_run_only_parameters(repo, monkeypatch):
    from test_ci_gate import _item_with_ci

    from ddflow.api import cirun as AR

    _item_with_ci(repo, monkeypatch, agent="holder-ff4")
    for extra in ({"base": "main"}, {"command": "true"}, {"full": True}, {"timeout_s": 5}):
        out = AR.ci_tool(repo, action="failed-first", ref="T1", agent="holder-ff4", **extra)
        assert out.exit == 3, extra


def test_the_selected_test_stages_get_the_failed_first_environment(tmp_path):
    """The default item rerun runs its pytest in the selected and always STAGES, not in the hook
    stage: the environment has to reach them, or the ordering silently does nothing."""
    from ddflow.config import Config
    from ddflow.services.ci import runner as R

    seen = tmp_path / "seen.txt"
    scope = SimpleNamespace(
        command=shlex.join(["sh", "-c", 'echo "$MARK" > "$1"', "sh", str(seen)]),
        always_command="",
        ok={},
        always_s=0.0,
        why="",
    )
    hooks = SimpleNamespace(output="", elapsed_s=0.0, ran=True, code=0)
    R._run_test_stages(
        Config.load(tmp_path), tmp_path, scope, 60, {}, None, (hooks, 0.0), set(), False,
        env_extra={"MARK": "reached"},
    )  # fmt: skip
    assert seen.read_text().strip() == "reached"


def test_an_id_the_collection_does_not_hold_is_not_claimed_as_ordered(tmp_path):
    (tmp_path / "test_a.py").write_text("def test_a1(): pass\n")
    with FF.session(FF.Plan(ids=("test_gone.py::test_x",)), parent_env=_env({})) as sess:
        _pytest(tmp_path, sess.env)
        assert sess.outcome().get("ran_failed_first", []) == []


def test_a_pytest_a_test_starts_in_the_same_process_stands_down(tmp_path):
    """`pytest.main` called by a test (an in-process fixture run) shares the outer run's pid."""
    (tmp_path / "test_a.py").write_text("def test_a1(): pass\ndef test_a2(): assert False\n")
    (tmp_path / "outer.py").write_text(
        "import pytest\n"
        "def test_outer():\n"
        "    assert pytest.main(['-p', 'no:cacheprovider', '-q', 'test_a.py']) == 1\n"
    )
    plan = FF.Plan(ids=("test_a.py::test_a2",))
    with FF.session(plan, parent_env=_env({})) as sess:
        run = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "outer.py"],
            cwd=tmp_path, env=sess.env, capture_output=True, text=True, timeout=120,
        )  # fmt: skip
        assert run.returncode == 0, run.stdout + run.stderr
        rows = [json.loads(x) for x in Path(sess.env[FF.OUT_ENV]).read_text().splitlines()]
    assert [r["ev"] for r in rows] == ["start"]  # the inner failure is not the outer run's


def test_with_plugin_autoload_disabled_nothing_is_ordered_and_nothing_claimed(tmp_path):
    (tmp_path / "test_a.py").write_text("def test_a1(): pass\ndef test_a2(): pass\n")
    parent = _env({"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"})
    with FF.session(FF.Plan(ids=("test_a.py::test_a2",)), parent_env=parent) as sess:
        ran = _pytest(tmp_path, {**parent, **sess.env})
        assert sess.outcome().get("ran_failed_first", []) == []
    assert ran == ["test_a.py::test_a1", "test_a.py::test_a2"]


def test_a_session_started_inside_a_failed_first_run_is_a_run_of_its_own(tmp_path):
    """`ddflow ci run` from a test of an outer failed-first run (the suite tests it) must order
    its own pytest: the outer run's marker is not inherited."""
    (tmp_path / "test_a.py").write_text("def test_a1(): pass\ndef test_a2(): pass\n")
    parent = _env({FF.ACTIVE_ENV: "1"})  # another process's marker, as the outer run leaves it
    with FF.session(FF.Plan(ids=("test_a.py::test_a2",)), parent_env=parent) as sess:
        ran = _pytest(tmp_path, {**parent, **sess.env})
    assert ran[0] == "test_a.py::test_a2"
