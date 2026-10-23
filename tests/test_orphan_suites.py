"""`doctor` names ci suites that outlived their ddflow owner (Ba3b7509530 follow-up)."""

from __future__ import annotations

import os
from pathlib import Path

from ddflow.services import orphan_suites as OS

SUITE = "pre-commit run --hook-stage pre-push --all-files"
SH = "sh -c '" + SUITE + "'"


def _proc(root: Path, pid: int, ppid: int, cmd: str, start_ticks: int, pgrp: int | None = None):
    d = root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    rest = ["S", str(ppid), str(pgrp or pid), "0"] + ["0"] * 15 + [str(start_ticks)]
    (d / "stat").write_text(f"{pid} (x y) {' '.join(rest)} 0 0\n")
    (d / "cmdline").write_bytes(
        b"\0".join(w.encode() for w in cmd.split("\x1f"))
        if "\x1f" in cmd
        else cmd.replace(" ", "\0").encode()
    )


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
    _proc(root, 50, 1, "sh\x1f-c\x1f" + SUITE, 100)  # the gate's shell, reparented to init
    _proc(root, 51, 50, SUITE, 100, pgrp=50)
    found = _find(root)
    assert [o.pid for o in found] == [51] and found[0].pgid == 50  # the suite, not its shell
    assert "kill -- -50" in OS.doctor_notes(found)[0]


def test_a_suite_with_a_live_ddflow_above_it_is_not_orphaned(tmp_path: Path):
    root = _tree(tmp_path)
    _proc(root, 40, 1, "python -m ddflow gate run T ci", 100)
    _proc(root, 50, 40, "sh\x1f-c\x1f" + SUITE, 100)
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
    assert _find(root, uid=os.getuid() + 1) == []


def test_an_unreadable_proc_gives_nothing(tmp_path: Path):
    assert OS.orphaned_ci_suites(tmp_path / "nope") == []


def test_host_notes_name_an_orphaned_suite(monkeypatch, repo):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from conftest import run_cli

    run_cli(repo, "init")
    fake = OS.Orphan(4242, 4242, 30000.0, SUITE)
    monkeypatch.setattr(OS, "orphaned_ci_suites", lambda *a, **k: [fake])
    from ddflow.services import repo_hygiene as RH

    assert any("orphaned ci suite: pid 4242" in n for n in RH.host_notes())


def test_a_shell_that_only_mentions_the_command_is_not_a_suite(tmp_path: Path):
    root = _tree(tmp_path)
    _proc(root, 51, 1, "sh\x1f-c\x1fecho " + SUITE + "; sleep 3600", 100)
    assert _find(root) == []


def test_a_wrapper_script_with_ddflow_in_its_path_is_not_a_ddflow_process(tmp_path: Path):
    root = _tree(tmp_path)
    _proc(root, 40, 1, "/bin/bash /home/u/ddflow/wrapper.sh", 100)
    _proc(root, 51, 40, SUITE, 100)
    assert [o.pid for o in _find(root)] == [51]


def test_python_dash_m_ddflow_is_a_ddflow_process(tmp_path: Path):
    root = _tree(tmp_path)
    _proc(root, 40, 1, "/usr/bin/python3 -m ddflow.surfaces.cli gate run T ci", 100)
    _proc(root, 51, 40, SUITE, 100)
    assert _find(root) == []


def test_the_note_never_advises_kill_minus_one(tmp_path: Path):
    note = OS.doctor_notes([OS.Orphan(7, 1, 30000.0, SUITE)])[0]
    assert "kill 7`" in note and "kill -- -1" not in note


def test_a_ddflow_that_is_pid_1_counts(tmp_path: Path):
    root = tmp_path / "proc"
    root.mkdir()
    (root / "stat").write_text("cpu 1\nbtime 1000\n")
    _proc(root, 1, 0, "/usr/bin/ddflow mcp", 0)
    _proc(root, 51, 1, SUITE, 100)
    assert _find(root) == []


def test_an_editor_opened_on_a_file_named_ddflow_is_not_a_ddflow_process(tmp_path: Path):
    root = _tree(tmp_path)
    _proc(root, 40, 1, "/usr/bin/vim ddflow", 100)
    _proc(root, 51, 40, SUITE, 100)
    assert [o.pid for o in _find(root)] == [51]


def test_a_pipx_style_shebang_launch_is_a_suite(tmp_path: Path):
    root = _tree(tmp_path)
    _proc(
        root, 51, 1,
        "/usr/bin/python3\x1f/home/u/.local/bin/pre-commit\x1frun\x1f--hook-stage\x1fpre-push\x1f--all-files",
        100,
    )  # fmt: skip
    assert [o.pid for o in _find(root)] == [51]


def test_an_interpreter_option_before_the_ddflow_script_is_skipped(tmp_path: Path):
    root = _tree(tmp_path)
    _proc(root, 40, 1, "python3\x1f-u\x1f/home/u/.local/bin/ddflow\x1fgate", 100)
    _proc(root, 51, 40, SUITE, 100)
    assert _find(root) == []


def test_a_script_that_merely_receives_dash_m_is_not_the_pre_commit_module(tmp_path: Path):
    root = _tree(tmp_path)
    _proc(
        root, 51, 1,
        "python\x1fscript.py\x1f-m\x1fpre_commit_hooks\x1frun\x1f--hook-stage=pre-push\x1f--all-files",
        100,
    )  # fmt: skip
    assert _find(root) == []


def test_an_unreadable_ancestor_suppresses_the_finding(tmp_path: Path):
    root = _tree(tmp_path)
    _proc(root, 40, 1, "whatever", 100)
    (root / "40" / "cmdline").unlink()
    (root / "40" / "cmdline").mkdir()  # reading it raises: present but unreadable
    _proc(root, 51, 40, SUITE, 100)
    assert _find(root) == []


def test_an_interpreter_option_before_pre_commit_or_dash_m_is_skipped(tmp_path: Path):
    root = _tree(tmp_path)
    args = "run\x1f--hook-stage\x1fpre-push\x1f--all-files"
    _proc(root, 51, 1, "python3\x1f-u\x1f/usr/local/bin/pre-commit\x1f" + args, 100)
    _proc(root, 61, 1, "python3\x1f-u\x1f-m\x1fpre_commit\x1f" + args, 100)
    assert sorted(o.pid for o in _find(root)) == [51, 61]


def test_an_ancestor_that_cannot_be_looked_up_suppresses_the_finding(tmp_path: Path):
    root = _tree(tmp_path)
    _proc(root, 51, 77, SUITE, 100)  # its parent 77 is not visible at all (hidepid)
    assert _find(root) == []
