"""Operator shell lines run through CommandRunner (B-uni-cmdrunner.2-callsites).

A command an operator wrote (a gate, a reviewer's command, an accepted test command) is
started, bounded and checked for "is it installed" in `services/cmdrunner.py` alone. This
guard fails on a new site that runs a shell line itself -- `P.run_shell`, `P.spawn_shell`,
any call with ``shell=True`` -- or that asks `shutil.which` about a command's first word.
Programs ddflow starts from an argv it built (the MCP worker, a companion, the verify
handshake) are not shell lines and are not what this guard is about.
"""

from __future__ import annotations

import ast
from pathlib import Path

GOVERNS = ("ddflow/**",)

PKG = Path(__file__).resolve().parents[1] / "ddflow"

#: The two modules that own shell-line execution.
HOMES = frozenset({"infra/proc.py", "services/cmdrunner.py"})

#: `which` on the first element of an argv ddflow built itself (never an operator's line).
EXEMPT = {
    ("api/operations.py", "which(<command>[i])"): "pre-commit execs shlex.split(entry), no shell",
    ("infra/forge.py", "which(<command>[i])"): "argv[0] of a gh/glab call ddflow assembled",
    ("services/companions.py", "which(<command>[i])"): "a companion's own detect argv",
}

_SHELL_CALLS = frozenset({"run_shell", "spawn_shell"})


def _sites(source: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in _SHELL_CALLS:
            found.append((node.lineno, f"{name}(...)"))
        if any(k.arg == "shell" and getattr(k.value, "value", None) is True for k in node.keywords):
            found.append((node.lineno, "shell=True"))
        if name == "which" and node.args and isinstance(node.args[0], ast.Subscript):
            found.append((node.lineno, "which(<command>[i])"))
    return found


def test_the_detector_sees_each_kind_of_direct_run():
    src = (
        "import shutil\nfrom ..infra import proc as P\n"
        "P.run_shell('x', timeout_s=1)\nP.spawn_shell('x')\n"
        "P.popen('x', shell=True)\nshutil.which(words[0])\nshutil.which('ruff')\n"
    )
    assert [what for _, what in _sites(src)] == [
        "run_shell(...)",
        "spawn_shell(...)",
        "shell=True",
        "which(<command>[i])",
    ]


def test_no_module_runs_an_operator_shell_line_outside_the_runner():
    bad = []
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG).as_posix()
        if rel in HOMES:
            continue
        bad += [
            f"ddflow/{rel}:{line} {what}"
            for line, what in _sites(path.read_text())
            if (rel, what) not in EXEMPT
        ]
    assert not bad, (
        "run an operator's command through services.cmdrunner.CommandRunner (run or start), "
        "not by hand:\n  " + "\n  ".join(bad)
    )


def _decl(line):
    from ddflow.services import cmdrunner as CR

    return CR.Declared(line, "test")


def test_start_reports_a_missing_program_without_starting_it():
    from ddflow.services import cmdrunner as CR

    s = CR.CommandRunner().start(_decl("ddflow-no-such-tool --x"))
    assert s.proc is None and s.unavailable.kind == CR.MISSING
    assert s.unavailable.missing == "ddflow-no-such-tool"


def test_start_hands_back_a_live_process_in_its_own_group():
    import os

    from ddflow.infra import proc as P
    from ddflow.services import cmdrunner as CR

    s = CR.CommandRunner().start(_decl("cat"), stdin=P.PIPE)
    assert s.unavailable is None
    try:
        assert os.getpgid(s.proc.pid) == s.proc.pid
        out, _ = s.proc.communicate("hello", timeout=10)
        assert out == "hello"
    finally:
        P.kill_group(s.proc)


def test_start_refuses_a_bare_string():
    import pytest

    from ddflow.services import cmdrunner as CR

    with pytest.raises(TypeError):
        CR.CommandRunner().start("true")


def test_a_run_can_fold_stderr_into_its_output(tmp_path):
    from ddflow.services import cmdrunner as CR

    r = CR.CommandRunner().run(
        _decl("echo a; echo b >&2"), cwd=tmp_path, timeout_s=10, merge_stderr=True
    )
    assert r.ran and r.out == "a\nb\n" and r.err == ""


def test_the_onboarding_run_keeps_its_old_codes(tmp_path):
    from ddflow.services import onboard_tests as OT

    assert OT._run_bounded("echo hi; exit 3", tmp_path, 10) == (3, "hi\n")
    assert OT._run_bounded("ddflow-no-such-tool", tmp_path, 10)[0] == 127
    assert OT._run_bounded("sleep 30", tmp_path, 1)[0] is None


def test_a_hook_entry_is_judged_as_pre_commit_runs_it(tmp_path):
    from ddflow.api import operations as OP

    # pre-commit execs the split entry: an env prefix names no program there.
    assert not OP._command_found("FOO=bar true", tmp_path)
    assert OP._command_found("true --x", tmp_path)
    assert not OP._command_found("ddflow-no-such-tool --x", tmp_path)


def test_start_redacts_the_reason_of_a_missing_program():
    from ddflow.core.redact import Redactor
    from ddflow.services import cmdrunner as CR

    red = Redactor("log", secret_patterns=[r"hunter2"])
    s = CR.CommandRunner(redactor=red).start(_decl("hunter2-tool --x"))
    assert s.unavailable.kind == CR.MISSING and "hunter2" not in s.unavailable.reason
