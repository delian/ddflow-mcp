"""The clock-env importers C6 moved behind the Clock/Env/Proc/Fsio ports stay moved."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from ddflow.adapters import env, fsio, proc
from ddflow.services import archdeps as A

ROOT = Path(__file__).resolve().parents[1]

MOVED = (
    "ddflow.adapters.mcp_client",
    "ddflow.adapters.platform",
    "ddflow.adapters.resource_probe",
    "ddflow.api.guard_launch",
    "ddflow.api.handoff",
    "ddflow.api.lifecycle.merge",
    "ddflow.api.lifecycle.ready",
    "ddflow.api.lifecycle.wait_approval",
    "ddflow.api.perf",
    "ddflow.api.perfreport",
    "ddflow.api.recover_adopt",
    "ddflow.services.completion.values",
    "ddflow.services.installmanifest",
    "ddflow.services.output_budget",
    "ddflow.services.perf.store",
    "ddflow.services.tree_bin",
    "ddflow.surfaces.mcp_lifecycle",
)


def test_the_moved_modules_no_longer_import_the_clock_or_environment():
    fam = {f.family: f for f in A.scan(ROOT).families}["clock-env"]
    assert not set(MOVED) & set(fam.importers)


def test_current_pid_and_path_sep_are_the_hosts():
    assert proc.current_pid() == os.getpid()
    assert env.PATH_SEP == os.pathsep


def test_wait_rusage_reports_exit_code_and_signal_death_like_subprocess():
    ok = subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])
    code, ru = proc.wait_rusage(ok.pid)
    assert code == 3 and ru.ru_utime >= 0
    killed = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    killed.send_signal(signal.SIGKILL)
    code, _ = proc.wait_rusage(killed.pid)
    assert code == -signal.SIGKILL


def test_append_line_appends_whole_lines_and_makes_parents(tmp_path):
    target = tmp_path / "a" / "b.jsonl"
    fsio.append_line(target, "one")
    fsio.append_line(target, "two")
    assert target.read_text() == "one\ntwo\n"


def test_completion_dir_probe_treats_an_unstattable_entry_as_not_a_directory(monkeypatch, tmp_path):
    from ddflow.services.completion import values

    (tmp_path / "d").mkdir()
    assert values._is_dir(str(tmp_path / "d")) is True
    assert values._is_dir(str(tmp_path / "missing")) is False

    def denied(self, **_k):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(Path, "stat", denied)
    assert values._is_dir(str(tmp_path / "d")) is False
