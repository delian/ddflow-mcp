"""The after-merge check exports the range the merge added to the hooks that read it
(PRE_COMMIT_FROM_REF), so a pushed-range scanner such as gitleaks-pushed.sh never falls back to
`origin/main..HEAD` -- thousands of unpushed local commits (B-ci-gitleaks-over-the-pushed-commits)."""

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
      - id: range-scanner
        name: needs the pushed range
        language: system
        entry: sh -c 'test "$PRE_COMMIT_FROM_REF" = "$WANT_FROM" && test -n "$PRE_COMMIT_TO_REF"'
        always_run: true
        pass_filenames: false
"""


@pytest.mark.parametrize("on_merge", ["full", "fast"])
def test_the_after_merge_check_exports_the_merged_range(repo, monkeypatch, on_merge):
    run_cli(repo, "init")
    (repo / ".pre-commit-config.yaml").write_text(CONFIG)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "adopt", "--no-verify")
    default = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _git(repo, "switch", "-q", "-c", "work")
    _commit(repo, {"ok.txt": "fine\n"}, "work")
    before = _git(repo, "rev-parse", default)
    _git(repo, "switch", "-q", default)
    _git(repo, "merge", "-q", "--no-ff", "work", "-m", "merge work")
    monkeypatch.setenv("WANT_FROM", before)
    cfg = Config.load(repo)
    cfg.ci.on_merge = on_merge
    cmd, _ = CI.main_command(repo, cfg)
    res = CI.run(repo, cfg, ref="HEAD", command=cmd, changed_from=before)
    assert res.status == "passed", (res.status, res.reason, res.output_tail)


def test_a_command_run_as_written_after_a_merge_still_sees_the_merged_range(repo, monkeypatch):
    """A whole-suite or operator-written command keeps `--all-files` (pre-commit then exports no
    range); the hooks that scan commits must still get the merge's range, not their fallback."""
    run_cli(repo, "init")
    (repo / ".pre-commit-config.yaml").write_text(CONFIG)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "adopt", "--no-verify")
    default = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _git(repo, "switch", "-q", "-c", "work")
    _commit(repo, {"ok.txt": "fine\n"}, "work")
    before = _git(repo, "rev-parse", default)
    _git(repo, "switch", "-q", default)
    _git(repo, "merge", "-q", "--no-ff", "work", "-m", "merge work")
    monkeypatch.setenv("WANT_FROM", before)
    cfg = Config.load(repo)
    cfg.ci.command = "pre-commit run --hook-stage pre-push --all-files"
    res = CI.run(repo, cfg, ref="HEAD", command=cfg.ci.command, changed_from=before)
    assert res.status == "passed", (res.status, res.reason, res.output_tail)
