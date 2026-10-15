"""`ddflow pins regenerate`: pin files are re-recorded by their own recipes, never hand-merged.

A project lists its pin recipes as data in `.ddflow/pins.toml`. Each says how to ask "is this
pin current?" (exit 0) and how to re-record it. The temporary repository here has one pin, a
count-and-list of the lines of `knobs.txt`, standing in for the golden snapshot, the setup
transcript and the README knob table."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import ddflow
from ddflow.surfaces.cli import main

GEN = '''\
import sys
from pathlib import Path

knobs = sorted(Path("knobs.txt").read_text().split())
want = f"{len(knobs)} knobs\\n" + "".join(f"- {k}\\n" for k in knobs)
pin = Path("pin.txt")
if "--check" in sys.argv:
    sys.exit(0 if pin.exists() and pin.read_text() == want else 1)
pin.write_text(want)
'''

RECIPES = """\
[[pin]]
name = "demo"
files = ["pin.txt"]
check = ["{python}", "gen.py", "--check"]
regen = ["{python}", "gen.py"]

[[pin]]
name = "other"
files = ["other.txt"]
check = ["{python}", "-c", "import sys; sys.exit(0)"]
regen = ["{python}", "-c", "open('other.txt','w').write('touched')"]
"""

SRC = str(Path(ddflow.__file__).resolve().parent.parent)


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return done.stdout


def ddflow_cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": SRC}
    env.pop("DDFLOW_AGENT", None)
    return subprocess.run(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), "--agent", "t", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=repo,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "proj"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    for k, v in (("user.email", "t@e.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        git(r, "config", k, v)
    (r / "gen.py").write_text(GEN)
    (r / "knobs.txt").write_text("a\n")
    (r / "other.txt").write_text("keep\n")
    (r / ".ddflow").mkdir()
    (r / ".ddflow" / "pins.toml").write_text(RECIPES)
    (r / ".gitignore").write_text("__pycache__/\n.ddflow/local/\n")
    ddflow_cli(r, "pins", "regenerate")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "base")
    return r


def test_check_lists_a_stale_pin_and_regenerate_fixes_only_it(repo: Path):
    assert ddflow_cli(repo, "pins", "regenerate", "--check").returncode == 0
    (repo / "knobs.txt").write_text("a\nb\n")
    stale = ddflow_cli(repo, "pins", "regenerate", "--check")
    assert stale.returncode == 1
    assert "demo" in stale.stdout and "other" not in stale.stdout
    assert (repo / "pin.txt").read_text() == "1 knobs\n- a\n"  # --check wrote nothing
    done = ddflow_cli(repo, "pins", "regenerate")
    assert done.returncode == 0, done.stderr
    assert (repo / "pin.txt").read_text() == "2 knobs\n- a\n- b\n"
    assert (repo / "other.txt").read_text() == "keep\n"  # it was current: untouched
    assert ddflow_cli(repo, "pins", "regenerate", "--check").returncode == 0


def test_only_names_one_pin_and_refuses_an_unknown_one(repo: Path):
    (repo / "knobs.txt").write_text("a\nb\n")
    assert ddflow_cli(repo, "pins", "regenerate", "--check", "--only", "other").returncode == 0
    bad = ddflow_cli(repo, "pins", "regenerate", "--only", "nosuch")
    assert bad.returncode != 0 and "nosuch" in bad.stderr + bad.stdout
    assert (repo / "pin.txt").read_text() == "1 knobs\n- a\n"


def test_two_branches_that_each_add_a_knob_merge_into_green_pins(repo: Path):
    inst = ddflow_cli(repo, "pins", "regenerate", "--install")
    assert inst.returncode == 0, inst.stderr
    assert "merge=pinsregen" in (repo / ".gitattributes").read_text()
    git(repo, "add", ".gitattributes")
    git(repo, "commit", "-qm", "driver")
    for branch, knob in (("one", "b"), ("two", "c")):
        git(repo, "checkout", "-q", "-b", branch, "main")
        (repo / "knobs.txt").write_text(f"a\n{knob}\n")
        ddflow_cli(repo, "pins", "regenerate")
        git(repo, "commit", "-qam", f"add {knob}")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-edit", "one")
    # the second merge would conflict on knobs.txt and pin.txt without the driver; knobs.txt
    # is a source and merges by union here, only the pin needs the driver
    (repo / ".gitattributes").write_text((repo / ".gitattributes").read_text() + "knobs.txt merge=union\n")
    git(repo, "commit", "-qam", "union knobs")
    env = {**os.environ, "PYTHONPATH": SRC}
    done = subprocess.run(
        ["git", "-C", str(repo), "merge", "--no-edit", "two"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert (repo / "knobs.txt").read_text().split() == ["a", "b", "c"]
    assert (repo / "pin.txt").read_text() == "3 knobs\n- a\n- b\n- c\n"
    assert git(repo, "status", "--porcelain").strip() == ""  # committed into the merge
    assert ddflow_cli(repo, "pins", "regenerate", "--check").returncode == 0
