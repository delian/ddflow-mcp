"""The `[pins]` knobs and the recipe file's contract."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ddflow.config import Config
from ddflow.services import pins as PN
from ddflow.services import pins_merge as PM


@pytest.fixture(autouse=True)
def _outside_xdist(monkeypatch):
    """A regeneration refuses to run in an xdist worker, which is where this suite runs."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)


def _repo(tmp_path: Path, toml: str, name: str = ".ddflow/pins.toml") -> Path:
    (tmp_path / ".ddflow").mkdir()
    (tmp_path / name).write_text(toml)
    return tmp_path


def _cfg(**pins) -> Config:
    cfg = Config()
    for k, v in pins.items():
        setattr(cfg.pins, k, v)
    return cfg


SLOW = '[[pin]]\nname = "slow"\nfiles = ["x"]\ncheck = ["sleep", "5"]\nregen = ["true"]\n'
GOOD = '[[pin]]\nname = "ok"\nfiles = ["x"]\ncheck = ["true"]\nregen = ["true"]\n'


def test_recipes_file_names_where_the_recipes_live(tmp_path):
    repo = _repo(tmp_path, GOOD, "elsewhere.toml")
    assert PN.load_recipes(repo, Config()) == []
    assert [r.name for r in PN.load_recipes(repo, _cfg(recipes_file="elsewhere.toml"))] == ["ok"]


def test_a_recipe_that_outlives_timeout_s_is_reported_failed(tmp_path):
    repo = _repo(tmp_path, SLOW)
    rep = PN.regenerate(repo, _cfg(timeout_s=1))
    assert [p.name for p in rep.failed] == ["slow"]
    assert "still stale" in rep.failed[0].error
    assert not PN.regenerate(repo, _cfg(timeout_s=30), check=True).stale  # only slow, not stale


@pytest.mark.parametrize(
    "bad,word",
    [
        ('[[pin]]\nname = "a"\ncheck = ["true"]\n', "regen"),
        ('[[pin]]\nfiles = []\ncheck = ["true"]\nregen = ["true"]\n', "no name"),
        (GOOD + GOOD, "twice"),
        (GOOD + 'env = ["CI=1"]\n', "env must be a table"),
    ],
)
def test_a_malformed_recipe_file_is_a_clear_problem(tmp_path, bad, word):
    rep = PN.regenerate(_repo(tmp_path, bad), Config())
    assert word in rep.problem


def test_xdist_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    assert "xdist" in PN.regenerate(_repo(tmp_path, GOOD), Config()).problem


def test_after_merge_off_does_nothing_and_leave_does_not_commit(tmp_path):
    repo = tmp_path
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "t@e"),
        ("config", "user.name", "T"),
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    (repo / "x").write_text("old\n")
    toml = (
        '[[pin]]\nname = "x"\nfiles = ["x"]\ncheck = ["sh", "-c", "grep -q new x"]\n'
        'regen = ["sh", "-c", "echo new > x"]\n'
    )
    _repo(repo, toml)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "c"], check=True)
    assert PM.after_merge(repo, _cfg(after_merge="off")) == ""
    assert (repo / "x").read_text() == "old\n"
    assert "not committed" in PM.after_merge(repo, _cfg(after_merge="leave"))
    assert (repo / "x").read_text() == "new\n"


def test_nothing_is_installed_when_after_merge_is_off(tmp_path):
    """With after_merge off no hook re-records the other side's pins, so a keep-ours driver
    would silently drop them: neither the driver nor the attribute is written."""
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    repo = _repo(tmp_path, GOOD)
    assert PM.install(repo, _cfg(after_merge="off")) == []
    assert not (repo / ".gitattributes").exists()
    assert PM.install(repo, _cfg(after_merge="amend"))
    assert f"merge={PM.DRIVER}" in (repo / ".gitattributes").read_text()
