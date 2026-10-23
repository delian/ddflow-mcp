"""B1401457afd: a SELECTED ci run started pytest with `-n auto` (one worker per core, 170+ on
this host) beside the full suite holding the lock. The hook entry the selection is cut from
says `-n auto`; only the full-suite pre-commit copy was localized. Every stage's command now
gets the count `[ci].workers` computes (at most `max_workers`, never more than the test files)."""

from __future__ import annotations

from types import SimpleNamespace as NS

from ddflow.config import Config
from ddflow.services import ci as CI
from ddflow.services import workers as WK

SELECTED = (
    "env DDFLOW_DEMO_NO_REVIEWER=1 uv run pytest -m 'not slow or scenarios' -q --timeout=420 "
    "-n auto --dist worksteal {files}"
)


def _idle(monkeypatch, cores=192):
    monkeypatch.setattr(
        WK.S.HostSignals, "sample", lambda self: {"load": 0.0, "memory_free_frac": 0.9}
    )
    monkeypatch.setattr(WK.os, "cpu_count", lambda: cores)


def test_a_selected_stage_never_runs_with_n_auto(monkeypatch):
    _idle(monkeypatch)
    cfg = Config()
    cfg.ci.max_workers = 4
    files = " ".join(f"tests/test_{i}.py" for i in range(10))
    used: dict = {}
    cmd = CI._with_workers(SELECTED.format(files=files), cfg, used, "selected")
    assert "-n auto" not in cmd and "-n 4 " in cmd, cmd
    assert used["selected"]["n"] == 4


def test_the_count_is_never_above_the_selected_files(monkeypatch):
    _idle(monkeypatch)
    cmd = CI._with_workers(
        SELECTED.format(files="tests/test_a.py tests/test_b.py"), Config(), {}, "selected"
    )
    assert "-n 2 " in cmd, cmd


def test_a_command_without_n_auto_or_a_placeholder_is_left_alone(monkeypatch):
    _idle(monkeypatch)
    plain = "uv run pytest -q -n 3 tests/test_a.py"
    assert CI._with_workers(plain, Config(), {}, "selected") == plain


def test_a_fixed_worker_count_applies_to_the_selected_run_too(monkeypatch):
    _idle(monkeypatch)
    cfg = Config()
    cfg.ci.workers = "6"  # fixed
    files = " ".join(f"tests/test_{i}.py" for i in range(20))
    cmd = CI._with_workers(SELECTED.format(files=files), cfg, {}, "selected")
    assert "-n 6 " in cmd, cmd
    _ = NS


def _scope(n_tests: int, always: int = 0):
    return NS(scope="selected", tests=[f"t{i}" for i in range(n_tests)], always=["a"] * always)


def test_a_long_selection_queues_for_the_suite_slot_a_short_one_does_not():
    cfg = Config()
    assert cfg.ci.selected_lock_tests == 20
    hooks = "SKIP=tests pre-commit run --hook-stage pre-push --all-files"
    assert CI._needs_suite_slot(cfg, hooks, _scope(5, always=3)) is False
    assert CI._needs_suite_slot(cfg, hooks, _scope(18, always=3)) is True


def test_the_knob_zero_never_queues_a_selection_and_the_whole_suite_always_does():
    cfg = Config()
    cfg.ci.selected_lock_tests = 0
    hooks = "SKIP=tests pre-commit run --hook-stage pre-push --all-files"
    assert CI._needs_suite_slot(cfg, hooks, _scope(500)) is False
    assert CI._needs_suite_slot(cfg, CI.DEFAULT_COMMAND, None) is True


def test_the_boundary_is_more_than_the_limit_not_the_limit():
    cfg = Config()
    hooks = "SKIP=tests pre-commit run --hook-stage pre-push --all-files"
    assert CI._needs_suite_slot(cfg, hooks, _scope(20)) is False
    assert CI._needs_suite_slot(cfg, hooks, _scope(21)) is True
    assert CI._needs_suite_slot(cfg, hooks, _scope(15, always=5)) is False
    assert CI._needs_suite_slot(cfg, hooks, _scope(15, always=6)) is True


def test_a_placeholder_beside_n_auto_leaves_no_n_auto(monkeypatch):
    _idle(monkeypatch)
    cmd = CI._with_workers(
        "pytest -n {workers} tests/a.py && pytest -n auto tests/b.py", Config(), {}, "selected"
    )
    assert "-n auto" not in cmd and "{workers}" not in cmd, cmd


def test_the_slot_holders_floor_reaches_the_selected_stages(monkeypatch):
    monkeypatch.setattr(
        WK.S.HostSignals, "sample", lambda self: {"load": 9.0, "memory_free_frac": 0.9}
    )  # far over the load mark
    monkeypatch.setattr(WK.os, "cpu_count", lambda: 192)
    files = " ".join(f"tests/test_{i}.py" for i in range(30))
    cmd = SELECTED.format(files=files)
    assert "-n 1 " in CI._with_workers(cmd, Config(), {}, "selected")
    assert "-n 8 " in CI._with_workers(cmd, Config(), {}, "selected", holding_suite=True)
