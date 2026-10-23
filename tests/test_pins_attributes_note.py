# ruff: noqa: F811 -- the `repo` fixture is imported from test_pins_regenerate
"""B53b3b594b3: `pins regenerate` said nothing when it added a `merge=pinsregen` line to
`.gitattributes`, so the changed file looked like a side effect to be discarded. It still
follows the recipes (DX-pins-attributes-follow-recipes) but now says what it wrote and why."""

from __future__ import annotations

from pathlib import Path

from test_pins_regenerate import RECIPES, ddflow_cli, git, repo  # noqa: F401


def _installed_then_a_recipe_added(repo: Path) -> None:
    assert ddflow_cli(repo, "pins", "regenerate", "--install").returncode == 0
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "driver")
    (repo / ".ddflow" / "pins.toml").write_text(
        RECIPES
        + '\n[[pin]]\nname = "third"\nfiles = ["third.txt"]\n'
        + 'check = ["{python}", "-c", "import sys; sys.exit(0)"]\n'
        + "regen = [\"{python}\", \"-c\", \"open('third.txt','w').write('x')\"]\n"
    )


def test_a_gitattributes_line_it_adds_is_reported_with_its_reason(repo: Path):
    _installed_then_a_recipe_added(repo)
    done = ddflow_cli(repo, "pins", "regenerate")
    assert done.returncode == 0, done.stderr
    assert "third.txt merge=pinsregen" in (repo / ".gitattributes").read_text()
    assert ".gitattributes" in done.stdout and "third.txt" in done.stdout, done.stdout
    assert "recipe" in done.stdout


def test_nothing_is_said_when_gitattributes_is_already_current(repo: Path):
    assert ddflow_cli(repo, "pins", "regenerate", "--install").returncode == 0
    done = ddflow_cli(repo, "pins", "regenerate")
    assert done.returncode == 0, done.stderr
    assert ".gitattributes" not in done.stdout + done.stderr


def test_the_line_is_reported_even_when_the_run_then_fails(repo: Path):
    _installed_then_a_recipe_added(repo)
    done = ddflow_cli(repo, "pins", "regenerate", "--only", "nosuch")
    assert done.returncode != 0
    assert "third.txt merge=pinsregen" in (repo / ".gitattributes").read_text()
    assert "third.txt merge=pinsregen" in done.stdout + done.stderr, done.stderr
