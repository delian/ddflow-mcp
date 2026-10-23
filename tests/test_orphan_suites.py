"""`doctor` names ci suites that outlived their ddflow owner (Ba3b7509530 follow-up)."""

from __future__ import annotations

from pathlib import Path

from ddflow.services import orphan_suites as OS

SUITE = "pre-commit run --hook-stage pre-push --all-files"


def _proc(root: Path, pid: int, ppid: int, cmd: str, start_ticks: int, pgrp: int | None = None):
    d = root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    rest = ["S", str(ppid), str(pgrp or pid), "0"] + ["0"] * 15 + [str(start_ticks)]
    (d / "stat").write_text(f"{pid} (x y) {' '.join(rest)} 0 0\n")
    (d / "cmdline").write_bytes(cmd.replace(" ", "\0").encode())


def _tree(tmp: Path) -> Path:
    root = tmp / "proc"
    root.mkdir()
    (root / "stat").write_text("cpu 1\nbtime 1000\n")
    _proc(root, 1, 0, "systemd", 0)
    return root


def _find(root: Path, **kw):
    # ticks=100: start 100 ticks = 1 s after boot (t=1001); now far later.
    return OS.orphaned_ci_suites(
        root, now=1001 + kw.pop("age", 30000), boot_time=1000, ticks=100, **kw
    )


def test_a_suite_under_a_dead_owner_is_named(tmp_path: Path):
    root = _tree(tmp_path)
    _proc(root, 50, 1, "sh -c " + SUITE, 100)  # the gate's shell, reparented to init
    _proc(root, 51, 50, SUITE, 100, pgrp=50)
    found = _find(root)
    assert [o.pid for o in found] == [50] and found[0].pgid == 50  # one per group
    assert "kill -- -50" in OS.doctor_notes(found)[0]


def test_a_suite_with_a_live_ddflow_above_it_is_not_orphaned(tmp_path: Path):
    root = _tree(tmp_path)
    _proc(root, 40, 1, "python -m ddflow gate run T ci", 100)
    _proc(root, 50, 40, "sh -c " + SUITE, 100)
    _proc(root, 51, 50, SUITE, 100)
    assert _find(root) == []


def test_a_young_suite_is_left_alone(tmp_path: Path):
    root = _tree(tmp_path)
    _proc(root, 51, 1, SUITE, 100)
    assert _find(root, age=60) == []


def test_another_command_is_not_a_suite(tmp_path: Path):
    root = _tree(tmp_path)
    _proc(root, 51, 1, "pre-commit run --all-files", 100)
    assert _find(root) == []


def test_another_users_suite_is_not_the_callers(tmp_path: Path):
    root = _tree(tmp_path)
    _proc(root, 51, 1, SUITE, 100)
    assert _find(root, uid=1234567) == []


def test_an_unreadable_proc_gives_nothing(tmp_path: Path):
    assert OS.orphaned_ci_suites(tmp_path / "nope") == []


def test_doctor_names_an_orphaned_suite(monkeypatch, repo):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from conftest import run_cli

    run_cli(repo, "init")
    fake = OS.Orphan(4242, 4242, 30000.0, SUITE)
    monkeypatch.setattr(OS, "orphaned_ci_suites", lambda *a, **k: [fake])
    from ddflow.api import doctor

    out = doctor(repo)
    assert "orphaned ci suite: pid 4242" in str(out.data) + (out.reason or "")
