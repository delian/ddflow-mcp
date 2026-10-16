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

GEN = """\
import sys
from pathlib import Path

knobs = sorted(Path("knobs.txt").read_text().split())
want = f"{len(knobs)} knobs\\n" + "".join(f"- {k}\\n" for k in knobs)
pin = Path("pin.txt")
if "--check" in sys.argv:
    sys.exit(0 if pin.exists() and pin.read_text() == want else 1)
pin.write_text(want)
"""

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
    env.pop("PYTEST_XDIST_WORKER", None)  # a regeneration refuses to run under xdist
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
    (repo / ".gitattributes").write_text(
        (repo / ".gitattributes").read_text() + "knobs.txt merge=union\n"
    )
    git(repo, "commit", "-qam", "union knobs")
    env = {**os.environ, "PYTHONPATH": SRC}
    env.pop("PYTEST_XDIST_WORKER", None)
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


def test_check_never_re_records_even_when_the_recipe_sets_env(repo: Path):
    (repo / ".ddflow" / "pins.toml").write_text(
        '[[pin]]\nname = "e"\nfiles = ["e.txt"]\n'
        "check = [\"{python}\", \"-c\", \"import os,sys; open('e.txt','w').write(os.environ.get('W','')); sys.exit(1)\"]\n"
        "regen = [\"{python}\", \"-c\", \"import os; open('e.txt','w').write(os.environ['W'])\"]\n"
        'env = { W = "1" }\n'
    )
    ddflow_cli(repo, "pins", "regenerate", "--check")
    assert (repo / "e.txt").read_text() == ""


MIXED_GEN = """\
import sys
from pathlib import Path

knobs = sorted(Path("knobs.txt").read_text().split())
block = "<!-- ddflow:begin doc/knobs sha=x -->\\n" + "".join(f"- {k}\\n" for k in knobs)
block += "<!-- ddflow:end doc/knobs -->"
doc = Path("doc.md")
text = doc.read_text()
head, rest = text.split("<!-- ddflow:begin doc/knobs", 1)
tail = rest.split("<!-- ddflow:end doc/knobs -->", 1)[1]
want = head + block + tail
if "--check" in sys.argv:
    sys.exit(0 if want == text else 1)
doc.write_text(want)
"""

PROSE = "Intro line.\n\n<!-- ddflow:begin doc/knobs sha=x -->\n- a\n<!-- ddflow:end doc/knobs -->\n\nMiddle one.\n\nMiddle two.\n\nOutro line.\n"


@pytest.fixture
def mixed(repo: Path) -> Path:
    (repo / "mixed_gen.py").write_text(MIXED_GEN)
    (repo / "doc.md").write_text(PROSE)
    with (repo / ".ddflow" / "pins.toml").open("a") as f:
        f.write(
            '\n[[pin]]\nname = "mixed"\nfiles = ["doc.md"]\n'
            'check = ["{python}", "mixed_gen.py", "--check"]\nregen = ["{python}", "mixed_gen.py"]\n'
        )
    assert ddflow_cli(repo, "pins", "regenerate", "--install").returncode == 0
    (repo / ".gitattributes").write_text(
        (repo / ".gitattributes").read_text() + "knobs.txt merge=union\n"
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "mixed")
    return repo


def edit_branch(repo: Path, name: str, *, prose: dict[str, str], knob: str = "") -> None:
    git(repo, "checkout", "-q", "-b", name, "main")
    text = (repo / "doc.md").read_text()
    for old, new in prose.items():
        text = text.replace(old, new)
    (repo / "doc.md").write_text(text)
    if knob:
        (repo / "knobs.txt").write_text(f"a\n{knob}\n")
    ddflow_cli(repo, "pins", "regenerate")
    git(repo, "commit", "-qam", name)


def merge(repo: Path, other: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": SRC}
    env.pop("PYTEST_XDIST_WORKER", None)
    return subprocess.run(
        ["git", "-C", str(repo), "merge", "--no-edit", other],
        capture_output=True,
        text=True,
        env=env,
    )


def test_the_other_sides_prose_edit_survives_in_a_mixed_pin_file(mixed: Path):
    edit_branch(mixed, "one", prose={}, knob="b")
    git(mixed, "checkout", "-q", "main")
    edit_branch(mixed, "two", prose={"Outro line.": "Outro, reworded."}, knob="c")
    git(mixed, "checkout", "-q", "one")
    done = merge(mixed, "two")
    assert done.returncode == 0, done.stdout + done.stderr
    text = (mixed / "doc.md").read_text()
    assert "Outro, reworded." in text
    assert "- b\n" in text and "- c\n" in text  # the generated block is regenerated
    assert ddflow_cli(mixed, "pins", "regenerate", "--check").returncode == 0


def test_prose_edited_in_different_places_by_both_sides_merges(mixed: Path):
    edit_branch(mixed, "one", prose={"Intro line.": "Intro, reworded."}, knob="b")
    git(mixed, "checkout", "-q", "main")
    edit_branch(mixed, "two", prose={"Outro line.": "Outro, reworded."})
    git(mixed, "checkout", "-q", "one")
    done = merge(mixed, "two")
    assert done.returncode == 0, done.stdout + done.stderr
    text = (mixed / "doc.md").read_text()
    assert "Intro, reworded." in text and "Outro, reworded." in text
    assert "- b\n" in text


def test_a_prose_conflict_stays_a_conflict(mixed: Path):
    edit_branch(mixed, "one", prose={"Middle one.": "Mine."})
    git(mixed, "checkout", "-q", "main")
    edit_branch(mixed, "two", prose={"Middle one.": "Theirs."})
    git(mixed, "checkout", "-q", "one")
    done = merge(mixed, "two")
    assert done.returncode != 0
    text = (mixed / "doc.md").read_text()
    assert "<<<<<<<" in text and "Mine." in text and "Theirs." in text
    assert "<<<ddflow-generated" not in text  # the placeholder never leaks


def test_generated_regions_are_regenerated_not_blended(mixed: Path):
    edit_branch(mixed, "one", prose={}, knob="b")
    git(mixed, "checkout", "-q", "main")
    edit_branch(mixed, "two", prose={"Middle two.": "Second."}, knob="c")
    git(mixed, "checkout", "-q", "one")
    (mixed / "knobs.txt").write_text("a\nb\nc\n")  # what the union of the sources comes to
    git(mixed, "commit", "-qam", "both knobs")
    done = merge(mixed, "two")
    assert done.returncode == 0, done.stdout + done.stderr
    text = (mixed / "doc.md").read_text()
    assert "- a\n- b\n- c\n" in text and "Second." in text
    assert git(mixed, "status", "--porcelain").strip() == ""
