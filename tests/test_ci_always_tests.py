"""The selected-scope ci run also takes the repo-wide guards and pins (DX-ci-always-run-guards).

An import closure cannot know that a new constant, a new knob or a new printed line affects the
ratchet, a transcript or a README table; `[ci].always_tests` (and `ALWAYS = True`) name the tests
that run for every item, apart from the selection, and are recorded as `always`."""

from __future__ import annotations

import json

from test_ci_selected_scope import _branch, _calls, _write, project, stubs  # noqa: F401

from ddflow.config import Config
from ddflow.services import ci as CI
from ddflow.services import ci_scope as CS

PASSING = "def test_guard():\n    assert True\n"


def _run(project):  # noqa: F811
    return CI.run(project, Config.load(project), base="main", for_item=True)


def test_the_default_set_names_the_guards_that_broke_main():
    got = Config().ci.always_tests
    for want in (
        "tests/test_constants_ratchet.py",
        "tests/test_uc_surf_setup_ops_transcript.py",
        "tests/test_uc_surf_loop_transcript.py",
        "tests/test_readme_*.py",
        "tests/golden/**",
    ):
        assert want in got


def test_a_change_in_a_constants_bearing_file_also_runs_the_ratchet_test(project, stubs):  # noqa: F811
    _write(project, {"tests/test_constants_ratchet.py": PASSING})
    _branch(project, {"pkg/mod.py": "A = 2\nNEW_LIMIT = 5\n"})
    res = _run(project)
    assert res.ok, res.reason
    assert res.scope["tests"] == ["tests/test_mod.py"]  # the closure never reaches the ratchet
    assert res.scope["always_tests"] == ["tests/test_constants_ratchet.py"]
    first, second = _calls(stubs, "pytest")
    assert "test_mod.py" in first and "ratchet" not in first
    assert "tests/test_constants_ratchet.py" in second and "test_mod" not in second
    assert res.scope["always"] == 1 and res.scope["always_s"] >= 0
    assert len(json.dumps(res.scope)) < 800


def test_the_always_set_runs_when_the_closure_is_empty(project, stubs):  # noqa: F811
    _write(project, {"tests/test_readme_table.py": PASSING})
    _branch(project, {"notes/memo.md": "hi\n"})
    res = _run(project)
    assert res.ok, res.reason
    assert res.scope["scope"] == "selected" and res.scope["selected"] == 0
    (call,) = _calls(stubs, "pytest")
    assert "tests/test_readme_table.py" in call and "test_mod" not in call


def test_a_test_declaring_always_runs_without_a_glob(project, stubs):  # noqa: F811
    _write(project, {"tests/test_zzz_invariant.py": "ALWAYS = True\n" + PASSING})
    _branch(project, {"pkg/mod.py": "A = 2\n"})
    res = _run(project)
    assert res.scope["always_tests"] == ["tests/test_zzz_invariant.py"]


def test_a_selected_test_is_not_run_twice(project, stubs):  # noqa: F811
    cfg = Config.load(project)
    cfg.ci.always_tests = ["tests/test_mod.py"]
    _branch(project, {"pkg/mod.py": "A = 2\n"})
    tree = project
    plan = CS.plan(cfg, tree, "main", requested="selected")
    assert plan.tests == ["tests/test_mod.py"] and plan.always == []


def test_an_empty_set_leaves_only_the_selection(project, stubs):  # noqa: F811
    _write(project, {"tests/test_constants_ratchet.py": PASSING})
    _branch(project, {"pkg/mod.py": "A = 2\n"})
    cfg = Config.load(project)
    cfg.ci.always_tests = []
    res = CI.run(project, cfg, base="main", for_item=True)
    assert res.scope["always"] == 0 and len(_calls(stubs, "pytest")) == 1


def test_a_failing_guard_is_named_in_the_checks_and_the_selection_still_passed(project, stubs):  # noqa: F811
    import stat

    bad = next(p for p in __import__("os").environ["PATH"].split(":") if p.endswith("/bin"))
    pytest_stub = __import__("pathlib").Path(bad) / "pytest"
    pytest_stub.write_text(
        f'#!/bin/sh\necho "$@" >> {stubs}/pytest\ncase "$*" in *ratchet*) exit 1;; esac\n'
    )
    pytest_stub.chmod(pytest_stub.stat().st_mode | stat.S_IXUSR)
    _write(project, {"tests/test_constants_ratchet.py": PASSING})
    _branch(project, {"pkg/mod.py": "A = 2\n"})
    res = _run(project)
    assert res.status == "failed"
    seen = {c.id.split(" ")[0]: c.ok for c in res.checks if "tests (" in c.id}
    assert seen == {"selected": True, "always": False}
