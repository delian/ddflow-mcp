"""The one runner for operator-configured commands (B-uni-cmdrunner)."""

from __future__ import annotations

import threading
import time

import pytest

from ddflow.core.redact import Redactor
from ddflow.services import ci as CI
from ddflow.services import cmdrunner as CR
from ddflow.services.slots import Slots


def decl(line: str) -> CR.Declared:
    return CR.Declared(line, "test")


def _gone(pid: int, wait_s: float = 5.0) -> bool:
    """The process is dead: not signalable, or an unreaped zombie (state Z)."""
    import os
    from pathlib import Path

    end = time.monotonic() + wait_s
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        try:
            if Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z":
                return True
        except (FileNotFoundError, ProcessLookupError):
            return True  # reaped between the two looks (reading an exiting process raises ESRCH)
        except (OSError, IndexError):
            raise AssertionError(f"cannot tell whether {pid} is gone") from None
        time.sleep(0.05)
    return False


def test_a_bare_string_is_not_a_declared_command():
    with pytest.raises(TypeError, match="Declared"):
        CR.CommandRunner().run("echo hi", timeout_s=5)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="came from"):
        CR.Declared("echo hi", " ")


def test_a_command_that_ran_reports_its_exit_and_output():
    run = CR.CommandRunner().run(decl("echo out; echo err >&2; exit 3"), timeout_s=10)
    assert run.ran and run.code == 3 and run.status == "ran" and run.kind == ""
    assert run.out == "out\n" and run.err == "err\n" and run.output == "out\nerr\n"
    assert run.output_bytes == len(run.output) and run.digest


def test_the_digest_is_of_the_raw_output_whatever_is_clipped_or_masked():
    plain = CR.CommandRunner().run(decl("printf 'aaaa token=hunter2 bbbb'"), timeout_s=10)
    red = Redactor("log", secret_patterns=[r"hunter2"])
    kept = CR.CommandRunner(max_output=8, redactor=red).run(
        decl("printf 'aaaa token=hunter2 bbbb'"), timeout_s=10
    )
    assert kept.digest == plain.digest and kept.output_bytes == plain.output_bytes
    assert kept.truncated and len(kept.out) < len(plain.out) + 80
    assert "hunter2" not in kept.out
    assert kept.out.rstrip().endswith("bbbb")  # the tail is what is kept


def test_redaction_is_counted_and_applied_to_both_streams():
    red = Redactor("log", secret_patterns=[r"hunter2"])
    run = CR.CommandRunner(redactor=red).run(decl("echo hunter2; echo hunter2 >&2"), timeout_s=10)
    assert "hunter2" not in run.output and sum(run.redactions.values()) == 2


@pytest.mark.parametrize(
    "line",
    [
        "definitely-not-installed-xyz --flag",
        "A=1 definitely-not-installed-xyz",
    ],
)
def test_a_missing_binary_is_unavailable_and_never_run(line, tmp_path):
    marker = tmp_path / "ran"
    run = CR.CommandRunner().run(decl(f"{line} ; touch {marker}"), timeout_s=5)
    assert not run.ran and run.status == "unavailable" and run.kind == CR.MISSING
    assert run.missing.startswith("definitely-not-installed-xyz") or "xyz" in run.missing
    assert run.code is None and not marker.exists()


def test_check_installed_false_leaves_the_shell_to_say_so():
    run = CR.CommandRunner().run(
        decl("definitely-not-installed-xyz"), timeout_s=5, check_installed=False
    )
    assert run.status == "unavailable" and run.kind == CR.NOT_FOUND and run.code == 127


def test_a_suite_that_chose_to_exit_127_is_a_failure_not_a_missing_tool():
    run = CR.CommandRunner().run(decl("exit 127"), timeout_s=5)
    assert run.ran and run.code == 127


def test_a_timeout_is_unavailable_and_kills_the_whole_group(tmp_path):
    pidfile = tmp_path / "pid"
    started = time.monotonic()
    run = CR.CommandRunner().run(
        decl(f"sleep 30 & echo $! > {pidfile}; wait"), timeout_s=1, cwd=tmp_path
    )
    assert run.status == "unavailable" and run.kind == CR.TIMEOUT
    assert run.reason == "timed out after 1s" and time.monotonic() - started < 15
    assert _gone(int(pidfile.read_text()))


def test_a_failing_tick_is_a_command_that_could_not_run():
    def boom() -> None:
        raise OSError("lease lost")

    run = CR.CommandRunner().run(decl("sleep 5"), timeout_s=10, on_tick=boom, tick_s=0.05)
    assert run.status == "unavailable" and run.kind == CR.COULD_NOT_RUN
    assert "lease lost" in run.reason


def test_a_bad_cwd_could_not_run(tmp_path):
    run = CR.CommandRunner().run(decl("true"), timeout_s=5, cwd=tmp_path / "gone")
    assert run.status == "unavailable" and run.kind == CR.COULD_NOT_RUN


def test_slots_admit_one_at_a_time_and_a_full_queue_is_unavailable(tmp_path):
    slots = Slots(tmp_path, "cmd")
    results: list[CR.CommandRun] = []
    first = CR.CommandRunner(slots=slots, slot_limit=1)
    t = threading.Thread(
        target=lambda: results.append(first.run(decl("sleep 1.5"), timeout_s=10)),
    )
    t.start()
    for _ in range(100):  # until the first run holds its slot
        if slots.busy(1):
            break
        time.sleep(0.02)
    second = CR.CommandRunner(slots=slots, slot_limit=1, slot_wait_s=0).run(
        decl("echo hi"), timeout_s=10
    )
    t.join()
    assert second.status == "unavailable" and second.kind == CR.BUSY and second.code is None
    assert results[0].ran
    again = CR.CommandRunner(slots=slots, slot_limit=1).run(decl("echo hi"), timeout_s=10)
    assert again.ran and again.waited_s >= 0


@pytest.mark.parametrize(
    "command,expected",
    [
        ("", ""),
        ("   ", ""),
        ("true", ""),
        ("exit 3", ""),
        ("cd frontend && npm test", ""),
        ("(cd x && y)", ""),
        ("{ a; }", ""),
        ("if true; then x; fi", ""),
        ("./local-script --flag", ""),
        ("/no/such/dir/tool", ""),
        ("A=1 B=2 definitely-not-installed-xyz run", "definitely-not-installed-xyz"),
        ("SKIP=tests true", ""),
        ("A=1", ""),
        ("definitely-not-installed-xyz | cat", "definitely-not-installed-xyz"),
        ("definitely-not-installed-xyz && true", "definitely-not-installed-xyz"),
        ("python3 -c 'print(1)'", ""),
        # B8c962616bb: no opinion where the shell, not `which`, must speak
        ('claude -p "unbalanced', ""),
        ("foo;bar", ""),
        # compound-command words the shell, not `which`, must run
        ("while ! ping -c1 host; do sleep 1; done", ""),
        ("until true; do x; done", ""),
        ("case x in x) y;; esac", ""),
        ("! grep -q foo file", ""),
        ("time make", ""),
        ("command /opt/tools/mytool --check", ""),
        ("exec /opt/tools/mytool", ""),
    ],
)
def test_executable_missing_is_one_answer_for_every_caller(command, expected):
    assert CR.executable_missing(command) == expected
    assert CI.tool_missing(command) == expected  # B8c962616bb: ci no longer disagrees


def test_ci_does_not_report_an_unsplittable_command_as_a_missing_tool():
    """B8c962616bb: the whole command used to be named as the tool that is not installed."""
    assert CI.tool_missing('make "unbalanced') == ""
    assert CI.tool_missing("lint;fix") == ""


def test_the_installed_check_uses_the_path_the_command_will_run_under(tmp_path):
    """A tool only on the PATH handed to the command is installed for it."""
    tool = tmp_path / "u1-only-here"
    tool.write_text("#!/bin/sh\necho found\n")
    tool.chmod(0o755)
    assert CR.executable_missing("u1-only-here --x") == "u1-only-here"
    assert CR.executable_missing("u1-only-here --x", str(tmp_path)) == ""
    run = CR.CommandRunner().run(
        decl("u1-only-here"), timeout_s=5, env={"PATH": f"{tmp_path}:/usr/bin:/bin"}
    )
    assert run.ran and run.out == "found\n"


def test_a_command_reviewer_is_checked_against_the_path_it_runs_under(tmp_path):
    from ddflow.services.review import Reviewer, _chat_command

    tool = tmp_path / "u1-reviewer-tool"
    tool.write_text("#!/bin/sh\ncat >/dev/null\necho reviewed\n")
    tool.chmod(0o755)
    rev = Reviewer(
        name="x",
        kind="command",
        command="u1-reviewer-tool",
        model="m",
        env={"PATH": f"{tmp_path}:/usr/bin:/bin"},
    )
    out, err = _chat_command(rev, "sys", "user", 30)
    assert "not on PATH" not in err and "reviewed" in out, (out, err)


def test_a_reason_quoting_the_commands_stderr_is_redacted_like_its_output():
    red = Redactor("log", secret_patterns=[r"hunter2"])
    run = CR.CommandRunner(redactor=red).run(
        decl("echo 'hunter2 not found' >&2; exit 127"), timeout_s=5
    )
    assert run.kind == CR.NOT_FOUND
    assert "hunter2" not in run.reason and "hunter2" not in run.output


def test_every_unavailable_path_redacts_its_reason_and_counts_once(tmp_path):
    red = Redactor("log", secret_patterns=[r"hunter2"])
    runner = CR.CommandRunner(redactor=red)
    gone = runner.run(decl("true"), timeout_s=5, cwd=tmp_path / "hunter2")
    assert gone.kind == CR.COULD_NOT_RUN and "hunter2" not in gone.reason
    nf = runner.run(decl("echo hunter2 not found >&2; exit 127"), timeout_s=5)
    assert nf.kind == CR.NOT_FOUND and "hunter2" not in nf.reason
    assert sum(nf.redactions.values()) == 1  # the reason repeats stderr; one secret, one count


def test_the_shared_word_list_covers_both_lists_it_replaced():
    """The gate's old builtins and ci's old shell words: no line either let through is now
    called a missing tool."""
    old_gate = {
        "exit", "true", "false", "cd", "echo", "test", "[", ":", "set", "unset", "export",
        "eval", "source", ".", "read", "wait", "trap", "shift", "return",
    }  # fmt: skip
    old_ci = {"cd", "export", "set", "source", ".", "{", "if", "for", "test", "[", "[[", "(("}
    assert (old_gate | old_ci) <= CR.SHELL_WORDS
    for word in sorted(old_gate | old_ci):
        assert CR.executable_missing(f"{word} x") == ""


def test_a_later_missing_binary_in_a_compound_gate_is_unavailable_not_failed(tmp_path):
    """The pre-flight sees only the first word; the shell's 127 + "not found" is the net."""
    import dataclasses

    from ddflow.services import gates as G

    gd = dataclasses.replace(
        G.DEFAULT_GATES["unit_tests"],
        command="true && definitely-not-installed-xyz --check",
        timeout_s=10,
    )
    outcome, ev = G.run_command_gate(gd, tmp_path)
    assert outcome == "unavailable" and ev["exit"] == 127 and "not found" in ev["reason"]
