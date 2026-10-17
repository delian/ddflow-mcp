"""The post-merge pins hook is bounded and cannot overlap itself (Bf965d87b66, B36c484af97).

Under load the hook ran the whole golden suite inside `git merge` for 6 to 22 minutes, and
two overlapping runs left README.md with 6472 duplicated lines and the golden snapshots
deleted. The hook now runs only the recipes that allow it, bounds each command by
`[pins].after_merge_timeout_s`, reports one that cannot finish as UNAVAILABLE (never stale,
never failed), and two re-recordings never overlap in one tree.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from ddflow.config import Config
from ddflow.infra.fsio import file_lock
from ddflow.services import pins as PN
from ddflow.services import pins_merge as PM

PY = sys.executable


@pytest.fixture
def tree(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)  # a regeneration refuses xdist
    r = tmp_path / "proj"
    r.mkdir()
    subprocess.run(["git", "-C", str(r), "init", "-q", "-b", "main"], check=True)
    return r


def _recipe(name: str, check: str, *, after_merge: bool = True) -> PN.Recipe:
    return PN.Recipe(
        name,
        (f"{name}.txt",),
        (PY, "-c", check),
        (PY, "-c", "pass"),
        after_merge=after_merge,
    )


def test_the_hook_does_not_run_a_recipe_that_says_no(tree, tmp_path):
    marker = tmp_path / "suite-ran"
    suite = _recipe("suite", f"open({str(marker)!r}, 'w'); raise SystemExit(1)", after_merge=False)
    quick = _recipe("quick", "pass")
    rep = PN.regenerate(tree, Config.load(tree), recipes=[suite, quick], after_merge=True)
    assert [p.name for p in rep.pins] == ["quick"]
    assert not marker.exists(), "the post-merge hook ran a recipe marked after_merge = false"
    # `pins regenerate` itself still runs every recipe
    PN.regenerate(tree, Config.load(tree), recipes=[suite, quick], check=True)
    assert marker.exists()


def test_a_recipe_that_cannot_finish_in_time_is_unavailable_not_stale_or_failed(tree):
    cfg = Config.load(tree)
    cfg.pins.after_merge_timeout_s = 1
    slow = _recipe("slow", "import time; time.sleep(60)")
    began = time.monotonic()
    rep = PN.regenerate(tree, cfg, recipes=[slow], after_merge=True)
    assert time.monotonic() - began < 30
    assert [p.name for p in rep.unavailable] == ["slow"], rep
    assert "timed out" in rep.unavailable[0].unavailable
    assert not rep.stale and not rep.failed


def test_the_hook_says_which_pins_it_could_not_check(tree):
    (tree / ".ddflow").mkdir()
    (tree / ".ddflow" / "pins.toml").write_text(
        '[[pin]]\nname = "slow"\nfiles = ["slow.txt"]\n'
        f'check = [{PY!r}, "-c", "import time; time.sleep(60)"]\n'
        f'regen = [{PY!r}, "-c", "pass"]\n'.replace("'", '"')
    )
    cfg = Config.load(tree)
    cfg.pins.after_merge_timeout_s = 1
    line = PM.after_merge(tree, cfg)
    assert "unavailable" in line and "slow" in line and "timed out" in line, line


def test_two_re_recordings_never_overlap_in_one_tree(tree):
    git_dir = subprocess.run(
        ["git", "-C", str(tree), "rev-parse", "--absolute-git-dir"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    with file_lock(Path(git_dir) / "ddflow-pins.lock", 0):
        rep = PN.regenerate(tree, Config.load(tree), recipes=[_recipe("quick", "pass")])
    assert [p.name for p in rep.unavailable] == ["quick"], rep
    assert "another pins regeneration" in rep.unavailable[0].unavailable
