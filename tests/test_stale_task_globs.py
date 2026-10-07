"""An open task's glob naming a module that became a package is reported (bug B56dc2baaf6).

After the module splits, open tasks still declared `ddflow/api/reporting.py` and the like.
That file is gone -- `ddflow/api/reporting/` is a package -- so a lease on the glob covers
none of the real files and the conflict detector cannot see two tasks editing the same
code. Nothing said so. `doctor` now names each such glob with its correction.
"""

from __future__ import annotations

import subprocess

from conftest import run_cli

TRACKED = ["pkg/mod/__init__.py", "pkg/mod/a.py", "pkg/other.py", "tests/test_x.py"]


def test_a_module_glob_whose_module_is_now_a_package_is_named_with_its_fix():
    from ddflow.core.schedule import stale_package_globs

    assert stale_package_globs(["pkg/mod.py", "pkg/other.py"], TRACKED) == [
        ("pkg/mod.py", "pkg/mod/")
    ]


def test_a_glob_that_matches_or_is_merely_new_is_not_reported():
    """A glob matching a tracked file is fine; one matching nothing with no package of its
    name is a file the task will create -- not this bug."""
    from ddflow.core.schedule import stale_package_globs

    assert (
        stale_package_globs(["pkg/mod/", "pkg/*.py", "pkg/new.py", "tests/test_y.py"], TRACKED)
        == []
    )


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_doctor_names_an_open_tasks_stale_package_glob(repo):
    (repo / "pkg" / "mod").mkdir(parents=True)
    (repo / "pkg" / "mod" / "__init__.py").write_text("", "utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "pkg")
    assert run_cli(repo, "init")[0] == 0
    assert run_cli(repo, "phase", "add", "P1", "--title", "p")[0] == 0
    code, out, err = run_cli(
        repo, "task", "add", "T1", "--phase", "P1", "--title", "t", "--globs", "pkg/mod.py"
    )
    assert code == 0, err
    _code, out, err = run_cli(repo, "doctor")
    assert "T1" in out + err and "pkg/mod.py" in out + err and "pkg/mod/" in out + err, out + err
