"""`pins regenerate` says what it is doing and runs independent recipes at once (DX-pins-regenerate-slow)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.config import Config
from ddflow.services import pins as PN


@pytest.fixture(autouse=True)
def _outside_xdist(monkeypatch):
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)


def _cfg(**pins) -> Config:
    cfg = Config()
    for k, v in pins.items():
        setattr(cfg.pins, k, v)
    return cfg


def _py(code: str) -> str:
    return f'["{sys.executable}", "-c", {_toml_str(code)}]'


def _toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _recipes(tmp_path: Path, *pins: tuple[str, str, str]) -> Path:
    """(name, file, python code the check runs) per pin; regen is a no-op."""
    body = ""
    for name, file, code in pins:
        body += (
            f'[[pin]]\nname = "{name}"\nfiles = ["{file}"]\ncheck = {_py(code)}\n'
            f"regen = {_py('pass')}\n"
        )
    (tmp_path / ".ddflow").mkdir(exist_ok=True)
    (tmp_path / ".ddflow" / "pins.toml").write_text(body)
    return tmp_path


#: A check that holds until its partner has started, so it can only pass if both run at once.
def _meet(mine: str, other: str) -> str:
    return (
        "import os, sys, time\n"
        f"open({mine!r}, 'w').close()\n"
        "end = time.time() + 20\n"
        f"while not os.path.exists({other!r}) and time.time() < end:\n"
        "    time.sleep(0.05)\n"
        f"sys.exit(0 if os.path.exists({other!r}) else 1)\n"
    )


#: A check that fails if another one using the same marker is running.
def _alone(marker: str) -> str:
    return (
        "import os, sys, time\n"
        f"if os.path.exists({marker!r}): sys.exit(1)\n"
        f"open({marker!r}, 'w').close()\n"
        "time.sleep(0.6)\n"
        f"os.remove({marker!r})\n"
    )


def test_every_recipe_reports_when_it_starts_and_finishes(tmp_path):
    repo = _recipes(tmp_path, ("one", "a", "pass"), ("two", "b", "pass"))
    lines: list[str] = []
    rep = PN.regenerate(repo, _cfg(parallel=1), on_progress=lines.append)
    assert not rep.problem and not rep.failed
    assert "pins: one: checking" in lines and any(x.startswith("pins: two: current") for x in lines)


def test_independent_checks_run_at_once(tmp_path):
    a, b = str(tmp_path / "a.go"), str(tmp_path / "b.go")
    repo = _recipes(tmp_path, ("a", "fa", _meet(a, b)), ("b", "fb", _meet(b, a)))
    rep = PN.regenerate(repo, _cfg(parallel=2), check=True)
    assert [p.name for p in rep.stale] == []  # each saw the other: they overlapped


def test_parallel_one_runs_them_in_turn(tmp_path):
    a, b = str(tmp_path / "a.go"), str(tmp_path / "b.go")
    repo = _recipes(tmp_path, ("a", "fa", _meet(a, b)), ("b", "fb", _meet(b, a)))
    rep = PN.regenerate(repo, _cfg(parallel=1, timeout_s=3), check=True)
    assert [p.name for p in rep.stale] == ["a"]  # a waited for a partner that had not started


def test_re_recording_is_one_recipe_after_another(tmp_path):
    """The checks fail until regenerated; each regeneration holds a marker and fails if
    another one is running."""
    m = str(tmp_path / "busy")
    body = ""
    for n in ("a", "b", "c"):
        done = str(tmp_path / f"{n}.done")
        check = f"import os, sys; sys.exit(0 if os.path.exists({done!r}) else 1)"
        regen = (
            "import os, sys, time\n"
            f"if os.path.exists({m!r}): sys.exit(1)\n"
            f"open({m!r}, 'w').close()\n"
            "time.sleep(0.5)\n"
            f"open({done!r}, 'w').close()\n"
            f"os.remove({m!r})\n"
        )
        body += (
            f'[[pin]]\nname = "{n}"\nfiles = ["{n}"]\ncheck = {_py(check)}\nregen = {_py(regen)}\n'
        )
    (tmp_path / ".ddflow").mkdir()
    (tmp_path / ".ddflow" / "pins.toml").write_text(body)
    rep = PN.regenerate(tmp_path, _cfg(parallel=4))
    assert [p.name for p in rep.pins if p.regenerated] == ["a", "b", "c"]
    assert not rep.failed


def test_a_long_recipe_is_named_while_it_runs(tmp_path):
    repo = _recipes(tmp_path, ("slow", "a", "import time; time.sleep(3)"))
    lines: list[str] = []
    PN.regenerate(repo, _cfg(progress_s=1), on_progress=lines.append)
    assert any("still running" in x and "slow" in x for x in lines)


def test_the_command_prints_progress_on_stderr(repo):
    run_cli(repo, "init")
    _recipes(repo, ("ok", "a", "pass"))
    code, out, err = run_cli(repo, "pins", "regenerate")
    assert code == 0, out + err
    assert "pins: ok: checking" in err and "ok: current" in out
