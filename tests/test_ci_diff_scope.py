"""The default pre-push CI run checks the change being pushed, not every file of the merged
tree (Bf333cb2f5d).

The run happens in a scratch tree of the branch MERGED with the base, and
`pre-commit run --hook-stage pre-push --all-files` ran every hook over every file there:
an error the base itself carries (a ruff finding that landed with some other change) failed
the push of a branch whose diff does not touch it, which in turn skipped the suite and
deadlocked the push. The default run now runs over the branch's own diff; the earlier
tripwire fix (ci-default-all-files-fails-tripwire-hooks) is its special case.
"""

from __future__ import annotations

import shutil

import pytest
from conftest import run_cli
from helpers import commit as _commit
from helpers import git as _git

from ddflow.config import Config
from ddflow.services import ci as CI

pytestmark = pytest.mark.skipif(
    shutil.which("pre-commit") is None, reason="pre-commit not installed"
)

CONFIG = """\
repos:
  - repo: local
    hooks:
      - id: changed-python-clean
        name: the pushed python files carry no BAD marker
        language: system
        entry: scripts/lint.sh
        types_or: [python, pyi]
"""

SCRIPT = "#!/bin/sh\nif grep -q BAD \"$@\"; then exit 1; fi\nexit 0\n"


def _project(repo):
    run_cli(repo, "init")
    (repo / ".pre-commit-config.yaml").write_text(CONFIG)
    (repo / "scripts").mkdir()
    script = repo / "scripts" / "lint.sh"
    script.write_text(SCRIPT)
    script.chmod(0o755)
    (repo / "bad.py").write_text("BAD = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "a base that carries a lint error", "--no-verify")
    return _git(repo, "rev-parse", "--abbrev-ref", "HEAD")


def test_an_error_on_the_base_outside_the_branch_diff_does_not_fail_the_push(repo):
    default = _project(repo)
    _git(repo, "switch", "-q", "-c", "work")
    _commit(repo, {"docs/README.md": "docs only\n"}, "docs")
    res = CI.run(repo, Config.load(repo), base=default)
    assert res.status == "passed", (res.status, res.reason, res.output_tail)


def test_the_branchs_own_bad_python_still_fails_the_push(repo):
    default = _project(repo)
    _git(repo, "switch", "-q", "-c", "work")
    _commit(repo, {"mine.py": "BAD = 2\n"}, "introduces the marker")
    res = CI.run(repo, Config.load(repo), base=default)
    assert res.status == "failed", (res.status, res.reason, res.output_tail)
