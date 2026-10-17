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


def test_a_regeneration_that_times_out_is_not_reported_as_current(tree):
    """The check finds the pin stale, the re-recording does not finish: the outcome says it
    could not (exit 2, the pin's name and why), never an ok that prints the pin as current."""
    from ddflow.api import pin_files as PF

    cfg = Config.load(tree)
    cfg.pins.after_merge_timeout_s = 1
    stuck = PN.Recipe(
        "stuck",
        ("stuck.txt",),
        (PY, "-c", "raise SystemExit(1)"),
        (PY, "-c", "import time; time.sleep(60)"),
    )
    rep = PN.regenerate(tree, cfg, recipes=[stuck], after_merge=True)
    out = PF._regenerate_outcome(rep, check=False)
    assert out.exit == 2, out  # NOTHING: could not finish
    assert "stuck" in out.reason and "timed out" in out.reason, out.reason
    assert out.data["pins"][0]["unavailable"] and not out.data["pins"][0]["regenerated"]


def test_the_hook_says_so_when_another_run_holds_the_tree(tree):
    """A second hook run while one is re-recording: it prints that it did not check, it is
    not silent (an empty line reads as 'all current')."""
    (tree / ".ddflow").mkdir()
    (tree / ".ddflow" / "pins.toml").write_text(
        '[[pin]]\nname = "quick"\nfiles = ["quick.txt"]\n'
        f'check = ["{PY}", "-c", "pass"]\nregen = ["{PY}", "-c", "pass"]\n'
    )
    git_dir = subprocess.run(
        ["git", "-C", str(tree), "rev-parse", "--absolute-git-dir"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    with file_lock(Path(git_dir) / "ddflow-pins.lock", 0):
        line = PM.after_merge(tree, Config.load(tree))
    assert "unavailable" in line and "quick" in line and "another pins regeneration" in line, line


def test_a_run_asked_for_still_reports_a_recipe_that_outlives_timeout_s_as_failed(tree):
    """Only the hook reports a timeout as unavailable: `ddflow pins regenerate` keeps the
    documented `timeout_s` behaviour (tests/test_pins_recipes.py)."""
    cfg = Config.load(tree)
    cfg.pins.timeout_s = 1
    slow = _recipe("slow", "import time; time.sleep(60)")
    rep = PN.regenerate(tree, cfg, recipes=[slow])
    assert [p.name for p in rep.failed] == ["slow"] and not rep.unavailable


def _damaging(name: str, then: str) -> PN.Recipe:
    """A stale pin whose re-recording duplicates the file's lines, deletes a sibling and then
    ``then`` (the damage a regeneration killed under load left behind)."""
    code = (
        "import pathlib\n"
        f"p = pathlib.Path('{name}.txt'); p.write_text(p.read_text() * 50)\n"
        f"pathlib.Path('{name}.d/gone.txt').unlink()\n"
        f"pathlib.Path('{name}.d/new.txt').write_text('stray')\n"
        f"pathlib.Path('{name}.d/sub').mkdir()\n"
        f"{then}\n"
    )
    return PN.Recipe(
        name, (f"{name}.txt", f"{name}.d/"), (PY, "-c", "raise SystemExit(1)"), (PY, "-c", code)
    )


def _pin_files(tree: Path, name: str) -> None:
    (tree / f"{name}.txt").write_text("a\nb\n")
    (tree / f"{name}.d").mkdir()
    (tree / f"{name}.d" / "gone.txt").write_text("keep me")


def test_a_killed_regeneration_puts_its_files_back(tree):
    _pin_files(tree, "dmg")
    cfg = Config.load(tree)
    cfg.pins.after_merge_timeout_s = 1
    rep = PN.regenerate(
        tree, cfg, recipes=[_damaging("dmg", "import time; time.sleep(60)")], after_merge=True
    )
    assert [p.name for p in rep.unavailable] == ["dmg"], rep
    assert "put back" in rep.unavailable[0].unavailable
    assert (tree / "dmg.txt").read_text() == "a\nb\n"
    assert (tree / "dmg.d" / "gone.txt").read_text() == "keep me"
    assert not (tree / "dmg.d" / "new.txt").exists()
    assert not (tree / "dmg.d" / "sub").exists()  # nor the directory the run made


def test_a_failed_regeneration_puts_its_files_back_too(tree):
    _pin_files(tree, "dmg")
    rep = PN.regenerate(tree, Config.load(tree), recipes=[_damaging("dmg", "raise SystemExit(3)")])
    assert [p.name for p in rep.failed] == ["dmg"]
    assert "put back" in rep.failed[0].error
    assert (tree / "dmg.txt").read_text() == "a\nb\n"
    assert (tree / "dmg.d" / "gone.txt").read_text() == "keep me"


def test_the_hook_says_so_when_it_could_not_run_at_all(tree):
    (tree / ".ddflow").mkdir()
    (tree / ".ddflow" / "pins.toml").write_text("[[pin]\nbroken")
    line = PM.after_merge(tree, Config.load(tree))
    assert line.startswith("pins: "), line


def test_a_symlinked_pin_file_stays_a_symlink_when_it_is_put_back(tree):
    (tree / "real.txt").write_text("a\nb\n")
    (tree / "link.txt").symlink_to("real.txt")
    code = "import pathlib\npathlib.Path('link.txt').write_text('junk')\nraise SystemExit(2)\n"
    recipe = PN.Recipe("lnk", ("link.txt",), (PY, "-c", "raise SystemExit(1)"), (PY, "-c", code))
    rep = PN.regenerate(tree, Config.load(tree), recipes=[recipe])
    assert [p.name for p in rep.failed] == ["lnk"]
    assert (tree / "link.txt").is_symlink() and (tree / "real.txt").read_text() == "a\nb\n"


def test_a_symlinked_binary_pin_file_stays_a_symlink_when_it_is_put_back(tree):
    (tree / "real.bin").write_bytes(b"\xff\xfe\x00bin")
    (tree / "link.bin").symlink_to("real.bin")
    code = "import pathlib\npathlib.Path('link.bin').write_bytes(b'junk')\nraise SystemExit(2)\n"
    recipe = PN.Recipe("bin", ("link.bin",), (PY, "-c", "raise SystemExit(1)"), (PY, "-c", code))
    rep = PN.regenerate(tree, Config.load(tree), recipes=[recipe])
    assert [p.name for p in rep.failed] == ["bin"]
    assert (tree / "link.bin").is_symlink()
    assert (tree / "real.bin").read_bytes() == b"\xff\xfe\x00bin"
