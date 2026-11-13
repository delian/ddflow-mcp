"""`ddflow doctor` says when completion is registered but the engine does not answer."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from ddflow.services import completion_selfcheck as CS
from ddflow.services import repo_hygiene
from ddflow.services.completion import install

ROOT = Path(__file__).resolve().parents[1]


def _exe(directory: Path, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "ddflow"
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _register(home: Path, shell: str = "zsh") -> Path:
    path = install.target_file(shell, home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(install.install_text(shell, ""))
    return path


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    for var in ("ZDOTDIR", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(h))
    return h


def test_silent_when_nothing_is_registered(home, tmp_path, monkeypatch):
    _exe(tmp_path / "bin", "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    assert CS.notes(home) == []


def test_registered_but_silent_engine_is_named(home, tmp_path, monkeypatch):
    rc = _register(home)
    _exe(tmp_path / "bin", "#!/bin/sh\nexit 0\n")  # runs, answers nothing: the original defect
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    (note,) = CS.notes(home)
    assert str(rc) in note and "does not answer" in note and "reinstall" in note


def test_registered_without_ddflow_on_path_is_named(home, monkeypatch, tmp_path):
    _register(home)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    (note,) = CS.notes(home)
    assert "no `ddflow` executable is on PATH" in note


def test_registered_and_answering_engine_is_silent(home, tmp_path, monkeypatch):
    _register(home)
    _exe(
        tmp_path / "bin",
        f'#!/bin/sh\nPYTHONPATH={ROOT} exec {sys.executable} -m ddflow "$@"\n',
    )
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    assert CS.notes(home) == []


def test_doctor_host_notes_carry_it(home, tmp_path, monkeypatch):
    _register(home)
    _exe(tmp_path / "bin", "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    assert any("does not answer" in n for n in repo_hygiene.host_notes())


def test_a_startup_file_that_is_not_text_does_not_crash_the_doctor(home):
    rc = install.target_file("zsh", home)
    rc.write_bytes(b"\xff\xfe not utf-8 " + install.install_text("zsh", "").encode())
    assert CS.registered(home) == []


def test_an_engine_that_writes_non_text_is_a_note_not_a_crash(home, tmp_path, monkeypatch):
    _register(home)
    _exe(
        tmp_path / "bin",
        "#!/bin/sh\nprintf '\\377\\376' > \"$_ARGCOMPLETE_STDOUT_FILENAME\"\n",
    )
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    (note,) = CS.notes(home)
    assert "does not answer" in note
