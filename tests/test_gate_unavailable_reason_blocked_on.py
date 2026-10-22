"""An unavailable verdict caused by the host suite lock records the real "blocked on" line
(B-ci-suite-lock-wait-reason-generic), not only 'exit 2 is declared UNAVAILABLE'."""

from __future__ import annotations

from ddflow.core.records import GateOutcome
from ddflow.services.gates import classify_exit
from ddflow.services.gates.defs import GateDef

GATE = GateDef(id="ci", title="ci", command="ddflow ci run", unavailable_exits=[2])
LINE = "blocked on pid 4242 / fix-B1 of /repo since 10:11:12, you are #3 in the queue"


def test_the_blocked_on_line_is_the_recorded_reason():
    out = classify_exit(GATE, 2, f"ci: running\n{LINE}\nci: UNAVAILABLE\n")
    assert out.outcome == GateOutcome.UNAVAILABLE
    assert LINE in out.reason
    assert "[ci].suite_wait_s" in out.reason


def test_the_last_blocked_on_line_wins():
    first = "blocked on pid 1 / a since 09:00:00, you are #5 in the queue"
    out = classify_exit(GATE, 2, f"{first}\n{LINE}\n")
    assert LINE in out.reason and first not in out.reason


def test_an_exit_2_without_a_lock_line_keeps_the_generic_reason():
    out = classify_exit(GATE, 2, "pre-commit is not installed\n")
    assert out.outcome == GateOutcome.UNAVAILABLE
    assert "unavailable_exits" in out.reason and "blocked on" not in out.reason


def test_a_line_that_merely_contains_the_words_is_not_taken():
    out = classify_exit(
        GATE, 2, "not blocked on pid 1 / x since 10:00:00, you are #3 in the queue\n"
    )
    assert "unavailable_exits" in out.reason and "suite_wait_s" not in out.reason


def test_only_the_ci_gate_reads_the_suite_lock_line():
    lint = GateDef(id="lint", title="lint", command="x", unavailable_exits=[2])
    out = classify_exit(lint, 2, f"{LINE}\n")
    assert "unavailable_exits" in out.reason and "suite_wait_s" not in out.reason


def test_a_prefixed_or_other_tools_line_is_not_taken():
    out = classify_exit(GATE, 2, "pre-commit: blocked on python3.11 since 10:00:00\n")
    assert "unavailable_exits" in out.reason and "suite_wait_s" not in out.reason
    out = classify_exit(GATE, 2, "blocked on the network since yesterday\n")
    assert "suite_wait_s" not in out.reason
