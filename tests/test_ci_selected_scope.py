"""An item's ci run takes the non-test hooks plus the tests its change reaches; the whole
suite stays for --full, the merge queue's batch and every change the selector cannot map
(DX-ci-selected-scope, D-test-economy-r2)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from conftest import run_cli
from helpers import git as _git

from ddflow.api import cirun as A
from ddflow.config import Config
from ddflow.services import ci as CI
from ddflow.services import ci_scope as CS

HOOKS = """\
repos:
  - repo: local
    hooks:
      - id: tests
        name: tests
        entry: >-
          pytest tests/ -m "not slow"
          -q
        language: system
"""


@pytest.fixture
def stubs(tmp_path, monkeypatch):
    """`pre-commit` and `pytest` on PATH that log how they were called."""
    bin_dir, log = tmp_path / "bin", tmp_path / "log"
    bin_dir.mkdir()
    log.mkdir()
    for name, body in (
        ("pre-commit", f"echo \"SKIP=$SKIP\" >> {log}/precommit\necho 'ruff check....Passed'\n"),
        ("pytest", f'echo "$@" >> {log}/pytest\n'),
    ):
        f = bin_dir / name
        f.write_text("#!/bin/sh\n" + body)
        f.chmod(f.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv(A.IN_GATE_ENV, raising=False)
    return log


def _write(repo, files):
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "fixture")


@pytest.fixture
def project(repo):
    run_cli(repo, "init")
    _write(
        repo,
        {
            ".pre-commit-config.yaml": HOOKS,
            "pkg/__init__.py": "",
            "pkg/mod.py": "A = 1\n",
            "pkg/other.py": "B = 1\n",
            "tests/conftest.py": "",
            "tests/test_mod.py": "from pkg import mod\n\ndef test_a():\n    assert mod.A\n",
            "tests/test_other.py": "from pkg import other\n\ndef test_b():\n    assert other.B\n",
        },
    )
    return repo


def _branch(repo, files):
    _git(repo, "switch", "-q", "-c", "work")
    _write(repo, files)


def _calls(log: Path, name: str) -> list[str]:
    f = log / name
    return f.read_text().splitlines() if f.exists() else []


def test_an_item_runs_only_the_selected_test_files_and_records_the_scope(project, stubs):
    _branch(project, {"pkg/mod.py": "A = 2\n"})
    res = CI.run(project, Config.load(project), base="main", for_item=True)
    assert res.ok, res.reason
    assert _calls(stubs, "precommit") == ["SKIP=tests,scenarios"]
    (call,) = _calls(stubs, "pytest")
    assert "tests/test_mod.py" in call and "test_other" not in call and "tests/ " not in call
    assert "-m not slow" in call and "-q" in call  # the hook's own options are kept
    assert res.scope["scope"] == "selected" and res.scope["selected"] == 1
    assert res.scope["tests"] == ["tests/test_mod.py"] and res.scope["cut"] == 0
    assert len(json.dumps(res.scope)) < 600  # small evidence


def test_full_runs_the_whole_suite_through_the_unmodified_hook(project, stubs):
    _branch(project, {"pkg/mod.py": "A = 2\n"})
    res = CI.run(project, Config.load(project), base="main", for_item=True, full=True)
    assert res.ok and res.scope["scope"] == "full"
    assert _calls(stubs, "precommit") == ["SKIP="] and not _calls(stubs, "pytest")


def test_the_knob_full_and_a_ref_that_is_no_item_run_the_whole_suite(project, stubs):
    _branch(project, {"pkg/mod.py": "A = 2\n"})
    cfg = Config.load(project)
    cfg.ci.tests_scope = "full"
    assert CI.run(project, cfg, base="main", for_item=True).scope["scope"] == "full"
    assert CI.run(project, Config.load(project), base="main").scope == {}
    assert not _calls(stubs, "pytest")


def test_a_hub_or_suite_configuration_change_falls_back_to_the_whole_suite(project, stubs):
    cfg = Config.load(project)
    cfg.gates.unit_tests_hub_importers = 2
    _write(
        project,
        {
            f"tests/test_u{i}.py": "from pkg import other\n\ndef test_u():\n    assert other.B\n"
            for i in range(2)
        },
    )
    _branch(project, {"pkg/other.py": "B = 2\n"})
    hub = CI.run(project, cfg, base="main", for_item=True)
    assert hub.scope["scope"] == "full" and "hub" in hub.scope["why"]
    _git(project, "switch", "-q", "main")
    _git(project, "branch", "-q", "-D", "work")
    _branch(project, {"pyproject.toml": "[project]\nname='x'\n"})
    cfgd = CI.run(project, Config.load(project), base="main", for_item=True)
    assert cfgd.scope["scope"] == "full" and "pyproject.toml" in cfgd.scope["why"]
    assert not _calls(stubs, "pytest")


def test_an_unknown_file_type_and_no_reaching_test_run_the_whole_suite(project, stubs):
    _branch(project, {"data/blob.bin": "x"})
    assert "file type" in CI.run(project, Config.load(project), base="main", for_item=True).scope["why"]
    _git(project, "switch", "-q", "main")
    _git(project, "branch", "-q", "-D", "work")
    _branch(project, {"notes/readme.md": "hi\n"})
    none = CI.run(project, Config.load(project), base="main", for_item=True)
    assert none.scope["scope"] == "full" and "no test reaches" in none.scope["why"]


def test_the_merge_queue_integrator_asks_for_the_whole_suite(monkeypatch):
    from ddflow.api import mergequeue_run as MR

    seen = {}

    def fake(repo, **kw):
        seen.update(kw)
        raise RuntimeError("stop")

    monkeypatch.setattr(MR, "ci_run", fake)
    monkeypatch.setattr(MR.GS, "pipeline_for", lambda it, cfg: ["ci"])
    monkeypatch.setattr(MR.MQ, "log_path", lambda repo, e: Path("log"))
    with pytest.raises(RuntimeError):
        MR._run_checks(Path("."), _Cfg(), _It(), _E(), {}, unit_tests=False, timeout_s=0, agent="")
    assert seen["full"] is True


class _Cfg:
    pass


class _It:
    id = "T1"


class _E:
    pass


def test_the_marker_line_carries_the_scope_and_the_gate_reads_it_back():
    ev = {"scope": "selected", "selected": 2, "tests": ["tests/a.py"], "cut": 0}
    out = "noise\n" + CS.marker_line(ev) + "\nci: passed\n"
    assert CS.parse_marker(out) == ev and CS.parse_marker("nothing") == {}


def _after(project, ran):
    from types import SimpleNamespace as NS

    from ddflow.services import testscope as TSC

    cfg = Config.load(project)
    it = NS(fixes=[])
    return TSC._after_ci(cfg, NS(bugs={}), it, "pytest -q", project, "main", 3, ran)


def test_unit_tests_is_covered_by_a_ci_run_of_equal_or_wider_scope(project, stubs):
    _branch(project, {"pkg/mod.py": "A = 2\n"})
    ran = CI.run(project, Config.load(project), base="main", for_item=True).scope
    sc = _after(project, ran)
    assert sc.scope == "covered" and sc.covered_by.startswith("ci selected run ")
    assert sc.evidence()["covered_by"] == sc.covered_by
    wide = _after(project, {"scope": "full", "sha": "abc"})
    assert wide.scope == "covered" and "ci full run" in wide.covered_by


def test_unit_tests_runs_only_the_difference_ci_did_not_take(project, stubs):
    _branch(project, {"pkg/mod.py": "A = 2\n", "pkg/other.py": "B = 2\n"})
    ran = {"scope": "selected", "sha": "abc", "selected": 1, "tests": ["tests/test_mod.py"]}
    sc = _after(project, ran)
    assert sc.scope == "selected"
    assert [t.path for t in sc.tests] == ["tests/test_other.py"]
    assert "tests/test_other.py" in sc.command and "test_mod" not in sc.command


def test_the_item_ci_gate_records_scope_selected_count_and_cut_set(project, stubs):
    (project / ".ddflow" / "config.toml").write_text(
        '[gates]\ntask_pipeline = ["implement", "ci", "merge"]\n'
    )
    _git(project, "add", "-A")
    _git(project, "commit", "-qm", "cfg")
    run_cli(project, "phase", "add", "P1", "--globs", "pkg/**")
    run_cli(project, "task", "add", "T1", "--phase", "P1", "--globs", "pkg/mod.py")
    assert run_cli(project, "claim", "T1")[0] == 0
    _, out, _ = run_cli(project, "--json", "show", "T1")
    tree = Path(json.loads(out)["worktree"])
    tree = tree if tree.is_absolute() else project / tree
    _write(tree, {"pkg/mod.py": "A = 3\n"})
    res = A.run(project, ref="T1")
    assert res.exit == 0, res.data
    _, out, _ = run_cli(project, "--json", "show", "T1")
    ev = json.loads(out)["gates"]["ci"]["evidence"]["ci_scope"]
    assert ev["scope"] == "selected" and ev["selected"] == 1 and ev["cut"] == 0
    full = A.run(project, ref="T1", full=True)
    _, out, _ = run_cli(project, "--json", "show", "T1")
    assert json.loads(out)["gates"]["ci"]["evidence"]["ci_scope"]["scope"] == "full", full.data
