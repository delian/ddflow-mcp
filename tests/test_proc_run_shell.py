"""proc.run_shell / ShellResult / TIMEOUTS (B-uni-proc.2-shell)."""

from __future__ import annotations

import os

import pytest

from ddflow.infra import git as G
from ddflow.infra import proc as P

pytestmark = pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")


def test_ticks_fire_while_the_command_runs(tmp_path):
    ticks: list[int] = []
    r = P.run_shell(
        "sleep 1", cwd=tmp_path, timeout_s=10, on_tick=lambda: ticks.append(1), tick_s=0.2
    )
    assert r.ok and len(ticks) >= 2


def test_a_tick_without_an_interval_is_the_callers_bug():
    with pytest.raises(ValueError):
        P.run_shell("true", timeout_s=1, on_tick=lambda: None)


def test_a_timed_out_result_carries_the_elapsed_time(tmp_path):
    r = P.run_shell("sleep 30", cwd=tmp_path, timeout_s=0.5)
    assert r.timed_out and 0.4 <= r.elapsed_s < 20


def test_output_that_is_not_utf8_is_replaced_not_raised(tmp_path):
    r = P.run_shell("printf 'caf\\351'", cwd=tmp_path, timeout_s=10)
    assert r.ok and r.out == "caf�"


def test_the_environment_is_the_callers(tmp_path):
    r = P.run_shell(
        "echo $U6_PROBE", cwd=tmp_path, timeout_s=10, env={**os.environ, "U6_PROBE": "x"}
    )
    assert r.out == "x\n"


def test_git_timeouts_come_from_the_one_table():
    assert G.GIT_TIMEOUT == P.TIMEOUTS["git"] and G.LISTING_TIMEOUT == P.TIMEOUTS["git_listing"]
    assert all(v > 0 for v in P.TIMEOUTS.values())
