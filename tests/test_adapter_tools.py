"""ADP-tools: the `ExternalTool` port, its table-described adapters, the parsers and the wiring.

No real tool is run: a scripted runner stands in for the process, and the production runner is
exercised with `python3` (always present) and a binary that is not.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import ClassVar

import pytest

from ddflow.adapters import tool_parsers as TP
from ddflow.adapters import tools as T
from ddflow.adapters.fakes.tools import FakeExternalTool
from ddflow.adapters.tool_types import (
    CLEAN,
    FINDINGS,
    MISSING,
    TIMEOUT,
    UNAVAILABLE,
    Finding,
    RunResult,
    ToolParseError,
)

PKG = Path(__file__).resolve().parents[1] / "ddflow"
TOOLS_NOW = {"roborev", "gitleaks", "pre-commit", "prek"}


class Script:
    """A runner that answers from a list, in order, and remembers what it was asked."""

    def __init__(self, *answers: RunResult) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[list[str], float]] = []
        self.cwds: list[str | None] = []

    def __call__(self, argv, *, timeout_s, cwd=None):
        self.calls.append((list(argv), timeout_s))
        self.cwds.append(cwd)
        return self.answers.pop(0)


def ok(out: str = "", code: int = 0, err: str = "") -> RunResult:
    return RunResult(True, code, out, err)


def gone(kind: str, reason: str = "") -> RunResult:
    return RunResult(False, None, kind=kind, reason=reason)


GITLEAKS_HIT = json.dumps(
    [{"RuleID": "aws-key", "File": "a.py", "StartLine": 3, "Secret": "REDACTED", "Match": "x"}]
)


# -- the table ----------------------------------------------------------------------------


def test_the_table_describes_the_tools_the_repo_shells_to():
    assert TOOLS_NOW <= set(T.SPECS)
    for tid in TOOLS_NOW:
        spec = T.SPECS[tid]
        assert spec.argv and spec.version_argv and spec.install, tid
        assert spec.parser in TP.PARSERS, (tid, spec.parser)


def test_a_table_row_naming_no_known_parser_is_refused():
    with pytest.raises(ValueError, match="parser"):
        T.spec_from_row({"id": "x", "argv": ["x"], "version_argv": ["x", "-v"], "parser": "nope"})


# -- probe --------------------------------------------------------------------------------


def test_probe_reads_the_version_and_says_installed():
    p = T.tool("gitleaks", Script(ok("8.30.1\n"))).probe()
    assert (p.installed, p.version, p.usable) == (True, "8.30.1", True)


def test_probe_of_a_missing_tool_is_a_fact_and_carries_the_hint():
    p = T.tool("gitleaks", Script(gone(MISSING, "'gitleaks' is not installed"))).probe()
    assert p.installed is False and not p.usable
    assert p.hint == T.SPECS["gitleaks"].install and p.hint in p.reason


def test_probe_that_could_not_tell_is_none_not_false():
    p = T.tool("gitleaks", Script(gone(TIMEOUT, "timed out after 5s"))).probe()
    assert p.installed is None and not p.usable and "timed out" in p.reason


def test_probe_that_exits_nonzero_says_no():
    p = T.tool("gitleaks", Script(ok("", 2, "bad flag"))).probe()
    assert p.installed is False and "exited 2" in p.reason


@pytest.mark.parametrize(
    ("pin", "version", "want"),
    [("8.30.1", "8.30.1", True), ("8.30.1", "8.30.10", True), ("8.30.1", "8.9.0", False),
     ("8.30", "8.30.0", True), ("8.30.1", "garbled", None), ("", "1.0", None)],
)  # fmt: skip
def test_pinned_version_is_a_minimum(pin, version, want):
    p = T.tool("gitleaks", Script(ok(f"v {version}\n")), pin=pin).probe()
    assert p.pinned_ok is want


def test_an_unmet_pin_makes_run_unavailable_and_names_the_upgrade():
    sc = Script(ok("8.1.0"))
    r = T.tool("gitleaks", sc, pin="8.30.1").run()
    assert (
        r.status == UNAVAILABLE and "8.30.1" in r.reason and T.SPECS["gitleaks"].install in r.reason
    )
    assert len(sc.calls) == 1, "the scan itself must not have run"


def test_probe_uses_its_own_timeout():
    sc = Script(ok("1.2.3"))
    T.tool("gitleaks", sc, probe_timeout_s=7).probe()
    assert sc.calls[0][1] == 7


# -- run: every failure is unavailable, never clean ----------------------------------------


def test_exit_zero_with_an_empty_report_is_clean():
    r = T.tool("gitleaks", Script(ok("[]"))).run()
    assert (r.status, r.findings, r.clean) == (CLEAN, (), True)


def test_a_findings_exit_with_a_parsed_report_is_findings():
    r = T.tool("gitleaks", Script(ok(GITLEAKS_HIT, 1))).run()
    assert r.status == FINDINGS
    assert r.findings == (Finding("gitleaks", "aws-key", "high", "a.py", 3, ""),)


def test_the_secret_is_not_in_the_normalized_finding():
    r = T.tool("gitleaks", Script(ok(GITLEAKS_HIT, 1))).run()
    assert "REDACTED" not in json.dumps([f.as_dict() for f in r.findings])


@pytest.mark.parametrize("kind", [MISSING, TIMEOUT, "could_not_run", "not_found", "busy"])
def test_a_run_that_did_not_happen_is_unavailable_with_the_runners_reason(kind):
    r = T.tool("gitleaks", Script(gone(kind, f"why {kind}"))).run()
    assert r.status == UNAVAILABLE and r.kind == kind and f"why {kind}" in r.reason


def test_a_missing_tool_reason_names_the_install_hint():
    r = T.tool("gitleaks", Script(gone(MISSING, "not installed"))).run()
    assert T.SPECS["gitleaks"].install in r.reason and "operator" in r.reason


def test_an_unexpected_exit_code_is_unavailable():
    r = T.tool("gitleaks", Script(ok("", 2, "boom"))).run()
    assert r.status == UNAVAILABLE and "exited 2" in r.reason


def test_unparseable_output_is_unavailable_not_clean():
    r = T.tool("gitleaks", Script(ok("this is not json", 0))).run()
    assert r.status == UNAVAILABLE and "parse" in r.reason.lower()


def test_a_findings_exit_that_yields_no_findings_is_unavailable():
    r = T.tool("gitleaks", Script(ok("[]", 1))).run()
    assert r.status == UNAVAILABLE


def test_clean_exit_with_findings_in_the_output_reports_them():
    r = T.tool("gitleaks", Script(ok(GITLEAKS_HIT, 0))).run()
    assert r.status == FINDINGS


def test_a_tool_without_a_parser_never_reads_a_nonzero_exit_as_clean_or_findings():
    assert T.tool("roborev", Script(ok("fine", 0))).run().status == CLEAN
    assert T.tool("roborev", Script(ok("bad", 1))).run().status == UNAVAILABLE


def test_run_appends_args_and_passes_the_timeout_and_cwd():
    sc = Script(ok("[]"))
    T.tool("gitleaks", sc, run_timeout_s=11).run(["--source", "x"], cwd="/w")
    argv, timeout = sc.calls[0]
    assert argv[: len(T.SPECS["gitleaks"].argv)] == list(T.SPECS["gitleaks"].argv)
    assert argv[-2:] == ["--source", "x"] and timeout == 11 and sc.cwds == ["/w"]


def test_gitleaks_always_runs_redacted():
    assert "--redact" in T.SPECS["gitleaks"].argv


def test_a_runner_that_raises_is_unavailable_not_a_crash():
    def boom(argv, *, timeout_s, cwd=None):
        raise OSError("disk gone")

    assert T.tool("gitleaks", boom).run().status == UNAVAILABLE


def test_install_hint_is_only_data():
    t = T.tool("gitleaks", Script())
    assert t.install_hint() == T.SPECS["gitleaks"].install and t.install_hint()


# -- parsers ------------------------------------------------------------------------------


def test_gitleaks_parser_tolerates_log_lines_before_the_report():
    out = "11:00AM INF 1 commits scanned.\n" + GITLEAKS_HIT + "\n"
    assert [f.rule for f in TP.PARSERS["gitleaks"]("gitleaks", out)] == ["aws-key"]


def test_precommit_parser_names_each_failed_hook():
    out = (
        "ruff check..............................................................Passed\n"
        "ruff format.............................................................Failed\n"
        "- hook id: ruff-format\n"
        "gitleaks................................................(no files to check)Skipped\n"
        "end-of-file-fixer.......................................................Failed\n"
    )
    got = TP.PARSERS["pre-commit"]("pre-commit", out)
    assert [f.rule for f in got] == ["ruff format", "end-of-file-fixer"]
    assert all(f.tool == "pre-commit" and f.severity == "error" for f in got)


def test_precommit_names_a_failed_hook_whose_name_leaves_only_two_pad_dots():
    # A long hook name eats the pad to the terminal width, so only two dots separate it from
    # the state; two dots must still read as a result (B-ADP-tools-checks).
    line = "x" * 72 + "..Failed"
    assert [f.rule for f in TP.PARSERS["pre-commit"]("pre-commit", line)] == ["x" * 72]


def test_the_normalized_parser_reads_its_own_shape_and_refuses_others():
    rows = [{"rule": "r1", "severity": "low", "file": "f", "line": 2, "advisory": "CVE-1"}]
    got = TP.PARSERS["normalized"]("t", json.dumps(rows))
    assert got == [Finding("t", "r1", "low", "f", 2, "CVE-1")]
    with pytest.raises(ToolParseError):
        TP.PARSERS["normalized"]("t", '{"not": "a list"}')
    with pytest.raises(ToolParseError):
        TP.PARSERS["normalized"]("t", '[{"severity": "low"}]')


# -- the fake and conformance --------------------------------------------------------------


def test_the_fake_scripts_exit_codes_and_outputs():
    f = FakeExternalTool(
        "scan", probe=ok("scan 2.0.1"), run=ok(json.dumps([{"rule": "r"}]), 1), finding_codes=(1,)
    )
    assert f.probe().version == "2.0.1"
    assert f.run().status == FINDINGS and f.run().findings[0].rule == "r"
    assert FakeExternalTool("scan", run=gone(MISSING, "x")).run().status == UNAVAILABLE


# -- layering and wiring --------------------------------------------------------------------


def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            out.add(("." * n.level) + (n.module or ""))
    return out


@pytest.mark.parametrize("mod", ["tools", "tool_types", "tool_parsers"])
def test_tool_modules_import_nothing_above_adapters_and_start_no_process(mod):
    bad = {
        i
        for i in _imports(PKG / "adapters" / f"{mod}.py")
        if i.startswith(("ddflow", "subprocess", "shutil", "socket", "urllib"))
        or i.startswith("..")
    }
    assert not bad, bad


def test_tool_modules_are_under_twenty_kib():
    for name in ("tools.py", "tool_types.py", "tool_parsers.py", "tools.toml"):
        assert (PKG / "adapters" / name).stat().st_size < 20 * 1024, name


def test_the_production_runner_maps_missing_and_timeout_and_runs_a_real_argv():
    from ddflow.services.toolrun import command_tool_runner

    run = command_tool_runner()
    done = run([sys.executable, "-c", "print('hi')"], timeout_s=20)
    assert (done.ran, done.code, done.out.strip()) == (True, 0, "hi")
    miss = run(["ddflow-no-such-tool-xyz", "--version"], timeout_s=5)
    assert (miss.ran, miss.kind) == (False, MISSING) and "ddflow-no-such-tool-xyz" in miss.reason
    slow = run([sys.executable, "-c", "import time; time.sleep(30)"], timeout_s=0.3)
    assert (slow.ran, slow.kind) == (False, TIMEOUT)


def test_an_argument_with_shell_syntax_stays_one_argv_token():
    from ddflow.services.toolrun import command_tool_runner

    done = command_tool_runner()(
        [sys.executable, "-c", "import sys; print(sys.argv[1])", "a b; echo $HOME"], timeout_s=20
    )
    assert done.out.strip() == "a b; echo $HOME"


def test_the_default_tool_takes_its_timeouts_from_the_knobs(tmp_path):
    from ddflow.services.toolrun import external_tool

    t = external_tool("gitleaks", tmp_path)
    assert (t.probe_timeout_s, t.run_timeout_s) == (20.0, 300.0)
    (tmp_path / ".ddflow").mkdir()
    (tmp_path / ".ddflow" / "config.toml").write_text(
        "[companions]\ntool_probe_timeout_s = 3\ntool_run_timeout_s = 9\n"
    )
    t = external_tool("gitleaks", tmp_path)
    assert (t.probe_timeout_s, t.run_timeout_s) == (3, 9)


# -- conformance: the same cases against the default adapter and the fake ------------------


class _Kit:
    """The default adapter over a scripted runner, built the way the fake's `scripted` is:
    `kit.scripted(probe=, run=)` takes the `RunResult`s the "process" gave. The row is the
    generic `normalized` one, so no real tool is named or run."""

    ROW: ClassVar[dict] = {
        "id": "t", "argv": ["t"], "version_argv": ["t", "-v"], "parser": "normalized",
        "install": "install-t", "finding_codes": [1],
    }  # fmt: skip

    def scripted(self, probe=None, run=None):
        answers = {"-v": probe or ok("tool 1.2.3"), "run": run or ok("[]")}

        def runner(argv, *, timeout_s, cwd=None):
            return answers["-v" if argv[-1] == "-v" else "run"]

        return T.TableTool(T.spec_from_row(self.ROW), runner)


def tool_cases(kit) -> None:
    p = kit.scripted(probe=ok("tool v1.2.3")).probe()
    assert (p.installed, p.version, p.usable) == (True, "1.2.3", True)
    absent = kit.scripted(probe=gone(MISSING, "gone")).probe()
    assert absent.installed is False and not absent.usable and absent.hint == "install-t"
    assert kit.scripted(probe=gone(TIMEOUT, "slow")).probe().installed is None
    hit = json.dumps([{"rule": "r1", "severity": "low", "file": "f", "line": 2}])
    got = kit.scripted(run=ok(hit, 1)).run()
    assert got.status == FINDINGS and got.findings[0].rule == "r1" and got.findings[0].line == 2
    assert kit.scripted(run=ok("[]")).run().status == CLEAN
    for bad in (
        gone(MISSING, "gone"),
        gone(TIMEOUT, "slow"),
        ok("[]", 2),
        ok("not json"),
        ok("[]", 1),
    ):
        assert kit.scripted(run=bad).run().status == UNAVAILABLE, bad
    tool = kit.scripted()
    assert tool.install_hint() == "install-t" and tool.tool_id() == "t"
    assert [f.rule for f in tool.parse(hit)] == ["r1"]


CONFORMANCE = {
    "ExternalTool": {
        "real": lambda monkeypatch: _Kit(),
        "cases": tool_cases,
        "fake": lambda cls: cls("t", finding_codes=(1,), install="install-t"),
    }
}


def test_the_default_adapter_passes_the_tool_cases():
    tool_cases(_Kit())


def test_the_fake_passes_the_tool_cases():
    tool_cases(FakeExternalTool("t", finding_codes=(1,), install="install-t"))


def test_a_parse_failure_never_echoes_the_output():
    for fn in ("gitleaks", "normalized"):
        with pytest.raises(ToolParseError) as e:
            TP.PARSERS[fn]("t", "Secret: AKIAIOSFODNN7EXAMPLE")
        assert "AKIA" not in str(e.value)
    with pytest.raises(ToolParseError) as e:
        TP.PARSERS["gitleaks"]("t", '[{"Secret": "hunter2"}]')
    assert "hunter2" not in str(e.value)


def test_null_fields_are_empty_not_the_word_none():
    got = TP.PARSERS["normalized"]("t", '[{"rule": "r", "severity": null, "file": null}]')
    assert (got[0].severity, got[0].file) == ("", "")


def test_an_indented_array_after_log_lines_is_read():
    out = 'log line\n  [\n    {"rule": "x"}\n  ]'
    assert [f.rule for f in TP.PARSERS["normalized"]("t", out)] == ["x"]


def test_precommit_ignores_indented_output_that_looks_like_a_result():
    out = "mypy....Failed\n  - hook id: mypy\n    test_foo....Failed\n"
    assert [f.rule for f in TP.PARSERS["pre-commit"]("pre-commit", out)] == ["mypy"]


def test_a_pin_that_could_not_be_checked_is_not_usable():
    from ddflow.adapters.tool_types import ToolProbe

    assert not ToolProbe("t", True, "", "1.0", None).usable
    assert ToolProbe("t", True, "", "", None).usable


def test_a_pinned_tool_retries_a_probe_that_did_not_succeed():
    # A transient probe (timeout) is not cached: the next pinned run probes again instead of
    # freezing `unavailable` across runs (B-ADP-tools-checks).
    sc = Script(gone(TIMEOUT, "probe timed out"), ok("8.30.1"), ok("[]"))
    t = T.tool("gitleaks", sc, pin="8.30.1")
    assert t.run().status == UNAVAILABLE  # probes once, times out
    assert len(sc.calls) == 1
    assert t.run().status == CLEAN  # probes AGAIN, now good, then scans
    assert len(sc.calls) == 3  # probe + scan of the second run


def test_a_bad_version_regex_is_refused_when_the_row_is_read():
    row = {"id": "x", "argv": ["x"], "version_argv": ["x", "-v"], "version_re": "("}
    with pytest.raises(ValueError, match="version_re"):
        T.spec_from_row(row)


def test_a_version_regex_without_a_match_group_value_does_not_crash():
    row = {"id": "x", "argv": ["x"], "version_argv": ["x", "-v"], "version_re": r"(\d+)?"}
    t = T.TableTool(T.spec_from_row(row), Script(ok("no digits")))
    assert t.probe().installed is True


def test_a_parser_that_raises_something_else_is_still_unavailable():
    t = T.tool("gitleaks", Script(ok("[]")))
    t.parse = lambda out: (_ for _ in ()).throw(TypeError("bad"))
    assert t.run().status == UNAVAILABLE


def test_the_fake_scripted_copy_keeps_the_other_script():
    f = FakeExternalTool("s", probe=gone(MISSING, "x")).scripted(run=ok("[]"))
    assert f.probe().installed is False


def test_a_falsy_injected_runner_is_still_used():
    from ddflow.services.toolrun import command_tool_runner

    class Falsy:
        def __bool__(self):
            return False

        def run(self, declared, **kw):
            from ddflow.services.cmdrunner import CommandRun

            return CommandRun("ran", declared.line, code=0, out="from-injected")

    assert command_tool_runner(Falsy())(["x"], timeout_s=1).out == "from-injected"


def test_a_diff_line_that_looks_like_a_hook_result_is_not_one():
    out = "mypy....Failed\n+test....Failed\n-old....Failed\n"
    assert [f.rule for f in TP.PARSERS["pre-commit"]("pre-commit", out)] == ["mypy"]


def test_json_infinity_is_a_zero_line_not_a_crash():
    got = TP.PARSERS["normalized"]("t", '[{"rule": "r", "line": 1e309}]')
    assert got[0].line == 0


def test_a_parser_that_raises_anything_is_unavailable():
    t = T.tool("gitleaks", Script(ok("[]")))
    t.parse = lambda out: None.strip()
    assert t.run().status == UNAVAILABLE


def test_a_foreign_parser_exception_text_is_not_copied_into_the_reason():
    t = T.tool("gitleaks", Script(ok("[]")))
    t.parse = lambda out: (_ for _ in ()).throw(ValueError('row {"Secret":"hunter2"}'))
    r = t.run()
    assert r.status == UNAVAILABLE and "hunter2" not in r.reason and "ValueError" in r.reason


def test_a_parse_error_reason_names_only_the_exception_type():
    t = T.tool("gitleaks", Script(ok("[]")))
    t.parse = lambda out: (_ for _ in ()).throw(ToolParseError('row {"Secret":"hunter2"}'))
    r = t.run()
    assert "hunter2" not in r.reason and "ToolParseError" in r.reason


def test_the_array_that_runs_to_the_end_is_the_report():
    out = '["starting scan"]\nlog\n    []\n[{"rule": "r1"}]\n'
    assert [f.rule for f in TP.PARSERS["normalized"]("t", out)] == ["r1"]


@pytest.mark.parametrize(
    ("version", "pin", "want"),
    [
        ("8.30.0-rc.1", "8.30.0", False),
        ("8.30.1-rc.1", "8.30.0", True),
        ("v8.30.0", "8.30.0", True),
    ],
)
def test_a_prerelease_is_older_than_its_release(version, pin, want):
    assert T.version_at_least(version, pin) is want


def test_a_pathological_version_regex_is_refused_as_a_value_error():
    row = {
        "id": "x",
        "argv": ["x"],
        "version_argv": ["x", "-v"],
        "version_re": "a{100000000000000000000000}",
    }
    with pytest.raises(ValueError, match="version_re"):
        T.spec_from_row(row)


def test_precommit_reads_a_note_after_the_state():
    assert [f.rule for f in TP.PARSERS["pre-commit"]("p", "mypy.....Failed (exit 1)\n")] == ["mypy"]


def test_the_fake_does_not_join_stdout_and_stderr_digits():
    f = FakeExternalTool("s", probe=RunResult(True, 0, "v1.2", "3"))
    assert f.probe().version == "1.2"


@pytest.mark.parametrize(
    ("version", "pin", "want"),
    [("8.30.0-rc.1", "8.30.0-rc.1", True), ("1.2.3+build.1", "1.2.3", True),
     ("1.2.3\nfoo", "1.2.3", True), ("1.2.3-beta", "1.2.3", False)],
)  # fmt: skip
def test_pre_release_detection_is_exact(version, pin, want):
    assert T.version_at_least(version, pin) is want


def test_a_deeply_nested_array_is_a_parse_error_not_a_crash():
    with pytest.raises(ToolParseError):
        TP.PARSERS["normalized"]("t", "[" * 100000)


def test_the_last_array_is_the_report_even_with_trailing_log_text_and_nested_arrays():
    out = '[]\n[\n  {"rule": "r1", "tags": [\n  ["x"]]}\n]\nscan complete\n'
    assert [f.rule for f in TP.PARSERS["normalized"]("t", out)] == ["r1"]


def test_two_different_prereleases_of_one_core_are_not_ordered_so_not_a_pass():
    assert T.version_at_least("1.0.0-rc.1", "1.0.0-rc.2") is False
    assert T.version_at_least("1.0.0-rc.2", "1.0.0-rc.2") is True
