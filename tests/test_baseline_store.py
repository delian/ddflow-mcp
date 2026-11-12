"""The baseline store: one measurement per base sha, by test id (GM-baseline-store)."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

from ddflow.services import baseline_store as BS

SHA = "a" * 40
OTHER = "b" * 40
CMD = "pytest -q"
DAY = 86400.0


def _measure(calls: list, failing=None, complete=True, worktree_only=()):
    def run():
        calls.append(1)
        return BS.Measured(
            failures=dict(failing if failing is not None else {"t.py::a": "boom"}),
            complete=complete,
            worktree_only=frozenset(worktree_only),
        )

    return run


def test_two_runs_on_one_sha_measure_the_base_once(tmp_path: Path) -> None:
    calls: list = []
    first = BS.baseline_for(tmp_path, SHA, CMD, _measure(calls), keep_days=14)
    second = BS.baseline_for(tmp_path, SHA, CMD, _measure(calls), keep_days=14)
    assert len(calls) == 1
    assert first.failing == second.failing == {"t.py::a": BS.digest_of("boom")}
    assert second.cached is True and first.cached is False


def test_a_different_sha_measures_again(tmp_path: Path) -> None:
    calls: list = []
    BS.baseline_for(tmp_path, SHA, CMD, _measure(calls), keep_days=14)
    BS.baseline_for(tmp_path, OTHER, CMD, _measure(calls), keep_days=14)
    assert len(calls) == 2


def test_a_different_command_measures_again(tmp_path: Path) -> None:
    calls: list = []
    BS.baseline_for(tmp_path, SHA, CMD, _measure(calls), keep_days=14)
    BS.baseline_for(tmp_path, SHA, "pytest -q tests/x", _measure(calls), keep_days=14)
    assert len(calls) == 2


def test_a_worktree_only_failure_is_not_a_baseline_failure(tmp_path: Path) -> None:
    calls: list = []
    got = BS.baseline_for(
        tmp_path,
        SHA,
        CMD,
        _measure(calls, {"t.py::a": "x", "t.py::wt": "y"}, worktree_only={"t.py::wt"}),
        keep_days=14,
    )
    assert set(got.failing) == {"t.py::a"}
    again = BS.load(tmp_path, SHA, CMD, keep_days=14)
    assert again is not None and set(again.failing) == {"t.py::a"}


def test_a_partial_entry_is_never_complete_and_is_measured_again(tmp_path: Path) -> None:
    calls: list = []
    first = BS.baseline_for(tmp_path, SHA, CMD, _measure(calls, complete=False), keep_days=14)
    assert first.complete is False
    BS.baseline_for(tmp_path, SHA, CMD, _measure(calls), keep_days=14)
    assert len(calls) == 2
    assert BS.load(tmp_path, SHA, CMD, keep_days=14).complete is True


def test_a_torn_or_foreign_file_reads_as_missing(tmp_path: Path) -> None:
    BS.baseline_for(tmp_path, SHA, CMD, _measure([]), keep_days=14)
    (path,) = list(BS.store_dir(tmp_path).glob("*.json"))
    path.write_text("{not json", encoding="utf-8")
    assert BS.load(tmp_path, SHA, CMD, keep_days=14) is None
    path.write_text(json.dumps({"sha": OTHER, "complete": True, "tests": {}}), encoding="utf-8")
    assert BS.load(tmp_path, SHA, CMD, keep_days=14) is None


def test_entries_older_than_keep_days_are_pruned(tmp_path: Path) -> None:
    BS.baseline_for(tmp_path, SHA, CMD, _measure([]), keep_days=14, now=0.0)
    BS.baseline_for(tmp_path, OTHER, CMD, _measure([]), keep_days=14, now=10 * DAY)
    assert BS.load(tmp_path, SHA, CMD, keep_days=14, now=10 * DAY) is not None
    BS.baseline_for(tmp_path, "c" * 40, CMD, _measure([]), keep_days=14, now=20 * DAY)
    assert BS.load(tmp_path, SHA, CMD, keep_days=14, now=20 * DAY) is None  # 20 d old
    assert BS.load(tmp_path, OTHER, CMD, keep_days=14, now=20 * DAY) is not None  # 10 d old


def test_keep_zero_keeps_nothing(tmp_path: Path) -> None:
    calls: list = []
    BS.baseline_for(tmp_path, SHA, CMD, _measure(calls), keep_days=0)
    BS.baseline_for(tmp_path, SHA, CMD, _measure(calls), keep_days=0)
    assert len(calls) == 2


def test_the_measure_runs_while_the_lock_is_held(tmp_path: Path) -> None:
    inside: list = []
    state = {"held": False}

    @contextlib.contextmanager
    def lock():
        state["held"] = True
        try:
            yield
        finally:
            state["held"] = False

    def run():
        inside.append(state["held"])
        return BS.Measured(failures={"t.py::a": "boom"})

    BS.baseline_for(tmp_path, SHA, CMD, run, keep_days=14, lock=lock)
    assert inside == [True]


def test_a_run_that_waited_for_the_lock_finds_what_the_holder_stored(tmp_path: Path) -> None:
    calls: list = []
    held: list = []

    @contextlib.contextmanager
    def lock():
        held.append("in")
        BS.baseline_for(tmp_path, SHA, CMD, _measure(calls), keep_days=14)  # the other run
        yield

    got = BS.baseline_for(tmp_path, SHA, CMD, _measure(calls), keep_days=14, lock=lock)
    assert len(calls) == 1 and got.cached is True and held == ["in"]


def test_keep_zero_prunes_what_an_earlier_keep_stored(tmp_path: Path) -> None:
    BS.baseline_for(tmp_path, SHA, CMD, _measure([]), keep_days=14)
    assert any(BS.store_dir(tmp_path).glob("*.json"))
    BS.baseline_for(tmp_path, OTHER, CMD, _measure([]), keep_days=0)
    assert not any(BS.store_dir(tmp_path).glob("*.json"))


def test_the_store_directory_is_git_ignored_local(tmp_path: Path) -> None:
    BS.baseline_for(tmp_path, SHA, CMD, _measure([]), keep_days=14)
    assert BS.store_dir(tmp_path).parts[-3:] == (".ddflow", "local", "baseline-store")
    assert any(BS.store_dir(tmp_path).glob("*.json"))


def test_the_config_entry_reads_the_keep_knob_and_takes_the_suite_lock(
    tmp_path: Path, monkeypatch
) -> None:
    from ddflow.config import Config
    from ddflow.services import suitelock

    cfg = Config()
    assert cfg.baseline.keep == 14
    taken: list = []

    @contextlib.contextmanager
    def hold(c, holder, *a, **k):
        taken.append(holder)
        yield

    monkeypatch.setattr(suitelock, "hold", hold)
    calls: list = []
    BS.baseline_for_cfg(tmp_path, cfg, SHA, CMD, _measure(calls))
    BS.baseline_for_cfg(tmp_path, cfg, SHA, CMD, _measure(calls))
    assert len(calls) == 1 and taken == ["baseline-store"]
    cfg.baseline.keep = 0
    BS.baseline_for_cfg(tmp_path, cfg, OTHER, CMD, _measure(calls))
    BS.baseline_for_cfg(tmp_path, cfg, OTHER, CMD, _measure(calls))
    assert len(calls) == 3
