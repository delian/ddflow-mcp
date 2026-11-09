"""Shared helpers for the tests/test_upgrade_dry_run_*.py modules.

B-upgrade.12-dry-run: the dry-run upgrade on a throwaway clone (D-dry-run-from-running-version).
A small fixture project (a real 0.1.3 release's) is rehearsed end to end: this source tree is the
new ddflow, the old one is this code declaring 0.1.3 (a simulation, which the report says).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ddflow.services import upgrade_dryrun as DR

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "releases" / "0.1.3" / "project"
SCRIPT = ROOT / "scripts" / "ci" / "upgrade-dry-run"
BUDGET = int(
    re.search(
        r"^TOOLS_LIST_BUDGET\s*=\s*([\d_]+)",
        (ROOT / "tests" / "test_mcp_tool_budget.py").read_text("utf-8"),
        re.M,
    )
    .group(1)
    .replace("_", "")
)


def git(repo: Path, *args: str) -> None:
    """Run git in ``repo`` without inheriting the developer's git config. A global
    ``commit.gpgsign`` (no signing key) would fail the fixture commit, and a global
    ``core.excludesfile`` would silently drop fixture files (bug Beb7591cc4a): the two ``-c``
    pins outrank every config FILE (user, global, XDG, system) and name those two offenders.
    """
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.excludesfile=",
            *args,
        ],
        check=True,
        capture_output=True,
    )


def project(tmp_path: Path, *, confirm_free: bool) -> Path:
    """The 0.1.3 fixture as a git project. ``confirm_free`` drops the one operator-set value
    the new defaults would ask the operator to confirm."""
    root = tmp_path / "proj"
    shutil.copytree(FIXTURE, root)
    if confirm_free:
        cfg = root / ".ddflow" / "config.toml"
        cfg.write_text(cfg.read_text("utf-8").replace("max_parallel = 4\n", ""), "utf-8")
    git(root, "init", "--quiet", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "--quiet", "--no-verify", "-m", "fixture")
    return root


def options(tmp_path: Path, root: Path, **kw) -> DR.Options:
    kw.setdefault("old", DR.simulated_old("0.1.3"))
    kw.setdefault("tools_budget", BUDGET)
    return DR.Options(source=root, work=kw.pop("work", tmp_path / "scratch"), **kw)


@pytest.fixture(scope="module", name="clean")
def clean_fixture(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, DR.Report, DR.Live]:
    """One full rehearsal of a project that needs no confirmation, with the live project as it was before."""
    tmp = tmp_path_factory.mktemp("dry")
    root = project(tmp, confirm_free=True)
    before = DR.snapshot_live(root)
    report = DR.run_all(options(tmp, root, diag_dir=tmp / "diag"))
    return root, report, before


def problems(before: DR.Live, after: DR.Live, mine: DR.Footprint | None = None) -> list[str]:
    return [*DR.live_leaks(before, after, mine), *DR.live_moved(before, after, mine)]


def rows(report: DR.Report) -> dict[int, DR.Check]:
    return {c.step: c for c in report.checks}
