"""`gate run --timeout N`: a per-run timeout, bounded by [gates].max_run_timeout_s, so nobody
edits the shared local config to get a longer run on a loaded host
(B-gate-run-ci-timeout-under-load)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api.gates import _with_run_timeout
from ddflow.config import Config
from ddflow.services.gates import GateDef


def _slow_gate(repo: Path, seconds: int = 3, limit: int = 1) -> None:
    run_cli(repo, "init")
    (repo / ".ddflow" / "gates.toml").write_text(
        f'[gate.unit_tests]\ncommand = "sleep {seconds}; echo done"\ncwd = "repo"\n'
        f"timeout_s = {limit}\n"
    )
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "claim", "T1", "--no-worktree")


def test_without_the_override_the_gates_own_timeout_applies(repo: Path):
    _slow_gate(repo)
    code, out, _ = run_cli(repo, "--json", "gate", "run", "T1", "unit_tests")
    assert code == 2 and json.loads(out)["outcome"] == "unavailable"


def test_a_per_run_timeout_replaces_the_gates_own(repo: Path):
    _slow_gate(repo)
    code, out, _ = run_cli(repo, "--json", "gate", "run", "T1", "unit_tests", "--timeout", "60")
    assert code == 0, out
    assert json.loads(out)["outcome"] == "passed"


def test_a_timeout_above_the_knob_is_refused_naming_it(repo: Path):
    _slow_gate(repo)
    code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests", "--timeout", "99999999")
    assert code == 3, (out, err)
    assert "[gates].max_run_timeout_s" in out + err


def test_a_negative_timeout_is_refused(repo: Path):
    _slow_gate(repo)
    code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests", "--timeout", "-5")
    assert code == 3 and "max_run_timeout_s" in out + err


def test_the_ci_gate_passes_the_budget_to_ddflow_ci_run():
    gate = GateDef(id="ci", title="ci", command="ddflow ci run", timeout_s=3600)
    cfg = Config()
    out = _with_run_timeout(cfg, gate, 7200, "T1", "ci")
    assert out.timeout_s == 7200 and out.command == "ddflow ci run --timeout 7200"
    other = _with_run_timeout(
        cfg, GateDef(id="unit_tests", title="u", command="pytest"), 90, "T1", "u"
    )
    assert other.timeout_s == 90 and other.command == "pytest"


def test_ci_run_has_its_own_bounded_timeout_flag(repo: Path):
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "ci", "run", "--timeout", "99999999")
    assert code == 3 and "[gates].max_run_timeout_s" in out + err


def test_an_explicit_zero_timeout_is_refused_not_ignored(repo: Path):
    _slow_gate(repo)
    code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests", "--timeout", "0")
    assert code == 3 and "max_run_timeout_s" in out + err


def test_a_ci_command_that_already_carries_a_timeout_is_overridden():
    cfg = Config()
    gate = GateDef(id="ci", title="ci", command="ddflow ci run --timeout 7200", timeout_s=3600)
    assert _with_run_timeout(cfg, gate, 30, "T1", "ci").command == "ddflow ci run --timeout 30"


def test_a_gate_without_a_command_does_not_crash_the_override():
    human = GateDef(id="approve", title="a", command=None)
    assert _with_run_timeout(Config(), human, 30, "T1", "approve").timeout_s == 30


def test_the_override_replaces_the_equals_form_and_stays_before_a_double_dash():
    cfg = Config()
    eq = GateDef(id="ci", title="ci", command="ddflow ci run --timeout=60", timeout_s=1)
    assert _with_run_timeout(cfg, eq, 120, "T1", "ci").command == "ddflow ci run --timeout 120"
    dd = GateDef(id="ci", title="ci", command="ddflow ci run -- pytest", timeout_s=1)
    assert _with_run_timeout(cfg, dd, 120, "T1", "ci").command == (
        "ddflow ci run --timeout 120 -- pytest"
    )


def test_ci_status_refuses_a_timeout(repo: Path):
    from ddflow.api import ci_tool

    run_cli(repo, "init")
    out = ci_tool(repo, action="status", timeout_s=1)
    assert out.exit == 3 and "timeout" in out.reason


def test_the_test_runners_own_timeout_after_a_double_dash_is_kept():
    gate = GateDef(id="ci", title="ci", command="ddflow ci run -- pytest --timeout=30", timeout_s=1)
    out = _with_run_timeout(Config(), gate, 60, "T1", "ci")
    assert out.command == "ddflow ci run --timeout 60 -- pytest --timeout=30"


def test_a_tab_after_the_double_dash_also_ends_the_ci_part():
    gate = GateDef(
        id="ci", title="ci", command="ddflow ci run --\tpytest --timeout=30", timeout_s=1
    )
    out = _with_run_timeout(Config(), gate, 60, "T1", "ci")
    assert out.command == "ddflow ci run --timeout 60 --\tpytest --timeout=30"


def test_a_verb_that_merely_starts_with_run_is_not_rewritten():
    gate = GateDef(id="ci", title="ci", command="ddflow ci run-analyze x", timeout_s=1)
    out = _with_run_timeout(Config(), gate, 60, "T1", "ci")
    assert out.command == "ddflow ci run-analyze x" and out.timeout_s == 60


def test_tabs_between_the_command_words_are_still_the_ci_verb():
    gate = GateDef(id="ci", title="ci", command="ddflow\tci\trun\t--timeout\t5", timeout_s=1)
    out = _with_run_timeout(Config(), gate, 60, "T1", "ci")
    assert out.command == "ddflow\tci\trun --timeout 60"


def test_the_knob_is_an_int_whatever_the_manifest_spells():
    assert isinstance(Config().gates.max_run_timeout_s, int)


def test_a_quoted_option_value_that_mentions_timeout_is_left_alone():
    gate = GateDef(
        id="ci", title="ci", command='ddflow ci run --reason "run with --timeout 120"', timeout_s=1
    )
    out = _with_run_timeout(Config(), gate, 30, "T1", "ci")
    assert out.command == 'ddflow ci run --timeout 30 --reason "run with --timeout 120"'


def test_a_real_timeout_next_to_a_quoted_one_is_the_one_replaced():
    gate = GateDef(
        id="ci",
        title="ci",
        command="ddflow ci run --timeout 7200 --reason 'x --timeout 5' -- pytest --timeout=9",
        timeout_s=1,
    )
    out = _with_run_timeout(Config(), gate, 30, "T1", "ci")
    assert out.command == (
        "ddflow ci run --timeout 30 --reason 'x --timeout 5' -- pytest --timeout=9"
    )


def test_the_shell_idiom_for_a_single_quote_inside_single_quotes_is_one_word():
    gate = GateDef(
        id="ci",
        title="ci",
        command="ddflow ci run --reason 'it'\\''s --timeout 5' --timeout 9",
        timeout_s=1,
    )
    out = _with_run_timeout(Config(), gate, 30, "T1", "ci")
    assert out.command == "ddflow ci run --timeout 30 --reason 'it'\\''s --timeout 5'"


def test_a_quoted_number_after_timeout_is_still_the_timeout():
    for spelling in ("--timeout='5'", "--timeout '5'", '--timeout="5"'):
        gate = GateDef(id="ci", title="ci", command=f"ddflow ci run {spelling}", timeout_s=1)
        out = _with_run_timeout(Config(), gate, 30, "T1", "ci")
        assert out.command == "ddflow ci run --timeout 30", spelling


def test_ci_run_cannot_take_an_option_looking_value_so_nothing_is_swallowed(repo: Path):
    """The rewrite has no option-level parsing: it relies on `ddflow ci run` refusing a value that
    is itself `--timeout=N` (argparse: 'expected one argument'), so no valid command has one."""
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "ci", "run", "--base", "--timeout=5")
    assert code == 2 and "expected one argument" in out + err


def test_a_signed_timeout_is_replaced_too():
    gate = GateDef(id="ci", title="ci", command="ddflow ci run --timeout=+5", timeout_s=1)
    assert _with_run_timeout(Config(), gate, 30, "T1", "ci").command == (
        "ddflow ci run --timeout 30"
    )


def test_a_backslash_newline_inside_a_quoted_value_keeps_the_value_whole():
    command = 'ddflow ci run --reason "foo \\\n --timeout 7"'
    gate = GateDef(id="ci", title="ci", command=command, timeout_s=1)
    out = _with_run_timeout(Config(), gate, 30, "T1", "ci")
    assert out.command == 'ddflow ci run --timeout 30 --reason "foo \\\n --timeout 7"'


def test_a_timeout_on_a_continuation_line_and_a_negative_one_are_replaced():
    cases = {
        "ddflow ci run \\\n--timeout 30 -- pytest": "ddflow ci run --timeout 60  -- pytest",
        "ddflow ci run --timeout=-5 -- pytest": "ddflow ci run --timeout 60 -- pytest",
        "ddflow ci run --timeout +5": "ddflow ci run --timeout 60",
        "ddflow ci run --timeout='+5'": "ddflow ci run --timeout 60",
    }
    for before, after in cases.items():
        gate = GateDef(id="ci", title="ci", command=before, timeout_s=1)
        assert _with_run_timeout(Config(), gate, 60, "T1", "ci").command == after, before


def test_an_unclosed_quote_keeps_the_rest_as_written():
    gate = GateDef(
        id="ci", title="ci", command='ddflow ci run --reason "msg --timeout 7', timeout_s=1
    )
    out = _with_run_timeout(Config(), gate, 60, "T1", "ci")
    assert out.command == 'ddflow ci run --timeout 60 --reason "msg --timeout 7'
