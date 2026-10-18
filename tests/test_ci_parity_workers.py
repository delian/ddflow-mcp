"""The local CI-parity run counts its pytest workers and never overlaps another full run
(bug B26b5951f2b: `-n auto` started one worker per core, several runs at once)."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from ddflow.infra.signals import FakeSource
from ddflow.services import ci as CI
from ddflow.services import suitelock as SL
from ddflow.services import workers as WK

HOOK = """repos:
  - repo: local
    hooks:
      - id: tests
        entry: >-
          env X=1
          uv run pytest tests/ -m "not slow or scenarios" -q --timeout=420
          -n auto --dist worksteal
        language: system
"""


def cfg(wait=0.0, limit=1):
    return NS(
        ci=NS(
            workers="auto",
            min_workers=1,
            max_workers=48,
            command="",
            max_concurrent_suites=limit,
            suite_wait_s=wait,
        ),
        schedule=NS(signals={"load_per_core": {"high": 0.75}}),
    )


def test_local_runner_command_has_the_computed_count_not_auto(tmp_path: Path):
    (tmp_path / CI.PRE_COMMIT_CONFIG).write_text(HOOK)
    used: dict = {}
    src = FakeSource({"load": 0.5, "memory_free_frac": 0.9})  # 192 cores: 48 free
    with CI.localize_hooks(tmp_path, cfg(), CI.DEFAULT_COMMAND, used, source=src, cores=192) as cmd:
        assert cmd != CI.DEFAULT_COMMAND
        tmp = Path(cmd.split("-c ")[1].split()[0].strip("'"))
        text = tmp.read_text()
        assert "-n 48" in text and "-n auto" not in text
        assert used["hooks"]["n"] == 48 and used["hooks"]["loadavg"] == 96.0
        assert "-n auto" in (tmp_path / CI.PRE_COMMIT_CONFIG).read_text()  # committed file kept
    assert not tmp.exists()


def test_a_config_without_auto_is_run_as_it_is(tmp_path: Path):
    (tmp_path / CI.PRE_COMMIT_CONFIG).write_text(HOOK.replace("-n auto", "-n 4"))
    with CI.localize_hooks(tmp_path, cfg(), CI.DEFAULT_COMMAND, {}) as cmd:
        assert cmd == CI.DEFAULT_COMMAND


def test_workers_substitution_leaves_other_text_alone():
    text, w = WK.localize_auto("a -n auto b\n-n auto\nx-n autos", cfg(), source=FakeSource(
        {"load": 0.0, "memory_free_frac": 0.9}), cores=8)  # fmt: skip
    assert w is not None and text.startswith("a -n 6 b\n-n 6\n") and text.endswith("x-n autos")


def test_second_full_run_reports_the_holder(tmp_path: Path):
    with SL.hold(cfg(), "agent-a", tmp_path):
        with pytest.raises(SL.SuiteBusy) as e:
            with SL.hold(cfg(wait=0), "agent-b", tmp_path):
                raise AssertionError("two full runs at once")
    msg = str(e.value)
    assert msg.startswith("blocked on agent-a since ") and e.value.holder["holder"] == "agent-a"


def test_second_full_run_waits_for_the_first(tmp_path: Path):
    got: list[str] = []
    entered, leave = threading.Event(), threading.Event()

    def first():
        with SL.hold(cfg(), "agent-a", tmp_path):
            entered.set()
            leave.wait(10)

    t = threading.Thread(target=first)
    t.start()
    entered.wait(10)
    threading.Timer(0.3, leave.set).start()
    with SL.hold(cfg(wait=10), "agent-b", tmp_path):
        got.append("b")
    t.join()
    assert got == ["b"]


def test_a_released_lock_leaves_no_holder(tmp_path: Path):
    with SL.hold(cfg(), "agent-a", tmp_path):
        pass
    with SL.hold(cfg(wait=0), "agent-b", tmp_path):
        pass


def test_execute_runs_the_localized_config_and_logs_it(tmp_path: Path, monkeypatch):
    (tmp_path / CI.PRE_COMMIT_CONFIG).write_text(HOOK)
    seen: list[str] = []

    class Fake:
        def run(self, declared, **_):
            seen.append(declared.command if hasattr(declared, "command") else str(declared))
            conf = seen[-1].split("-c ")[1].split()[0].strip("'")
            seen.append(Path(conf).read_text())
            return NS(ran=True, code=0, output="", elapsed_s=0, kind="", reason="")

    monkeypatch.setattr(CI, "CommandRunner", Fake)
    monkeypatch.setattr(CI, "child_env", lambda *a, **k: {})
    monkeypatch.setattr(WK, "compute", lambda c, f=None, **k: WK.Workers(7, {"loadavg": 1.0}))
    used: dict = {}
    CI._execute(cfg(), tmp_path, CI.DEFAULT_COMMAND, None, 60, local=True, used=used)
    assert "-n 7" in seen[1] and "-n auto" not in seen[1] and "-n auto" not in seen[0]
    assert used["hooks"]["n"] == 7


def test_a_crashed_holders_note_is_not_named(tmp_path: Path):
    """The dead holder's note sits at a FREE slot (1) that the live hold (slot 0) never reuses,
    so the unfixed oldest-note read would name it."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    SL._note(tmp_path, 1).write_text('{"holder": "dead", "at": 1, "at_iso": "00:00:01"}')
    with SL.hold(cfg(limit=2), "agent-b", tmp_path):
        assert SL._holders(tmp_path, 2)["holder"] == "agent-b"
