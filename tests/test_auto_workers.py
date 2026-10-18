"""The pytest worker count is computed at run time (D-agent-test-etiquette)."""

from __future__ import annotations

from types import SimpleNamespace as NS

from ddflow.infra.signals import FakeSource
from ddflow.services import workers as WK


def cfg(workers="auto", lo=1, hi=48, marks=None):
    return NS(
        ci=NS(workers=workers, min_workers=lo, max_workers=hi),
        schedule=NS(signals=marks or {"load_per_core": {"high": 0.75}}),
    )


def run(c, load, free=0.9, files=None, cores=192):
    return WK.compute(c, files, source=FakeSource({"load": load, "memory_free_frac": free}),
                      cores=cores)  # fmt: skip


def test_quiet_host_uses_the_ceiling():
    assert run(cfg(), 0.0).n == 48


def test_busy_host_is_clamped_up_to_the_floor():
    assert run(cfg(lo=2), 0.9).n == 2


def test_formula_is_cores_times_high_minus_loadavg():
    w = run(cfg(hi=500), 0.5)  # 192*0.75 - 96 = 48
    assert w.n == 48 and w.inputs["loadavg"] == 96.0 and w.inputs["cores"] == 192


def test_never_above_the_selected_files():
    assert run(cfg(), 0.0, files=3).n == 3


def test_memory_pressure_halves_and_critical_floors():
    assert run(cfg(), 0.0, free=0.10).n == 24
    assert run(cfg(lo=2), 0.0, free=0.02).n == 2


def test_fixed_setting_wins():
    w = run(cfg(workers="7"), 0.9)
    assert w.n == 7 and w.inputs["setting"] == "7"


def test_unknown_load_is_bounded():
    assert WK.compute(cfg(), None, source=FakeSource({}), cores=192).n == 16


def test_resolve_substitutes_every_form_and_logs_inputs():
    src = FakeSource({"load": 0.0, "memory_free_frac": 0.9})
    cmd, w = WK.resolve(
        "pytest -n '{workers}' tests/test_a.py tests/test_b.py", cfg(), source=src, cores=192
    )
    assert cmd == "pytest -n 2 tests/test_a.py tests/test_b.py" and w.inputs["files"] == 2


def test_command_without_placeholder_is_untouched():
    assert WK.resolve("pytest -n 4", cfg()) == ("pytest -n 4", None)


def test_ignored_and_deselected_paths_are_not_a_selection():
    c = "pytest -n {workers} --ignore=tests/test_slow.py --deselect tests/test_a.py::t tests/"
    assert WK.count_test_files(c) is None
    assert WK.count_test_files("pytest tests/test_a.py::x tests/test_a.py tests/test_b.py") == 2


def test_unknown_load_respects_the_floor():
    assert WK.compute(cfg(lo=24), None, source=FakeSource({}), cores=192).n == 24


def test_fixed_setting_is_still_capped_by_the_files_and_none_means_auto():
    assert run(cfg(workers="16"), 0.9, files=1).n == 1
    assert run(cfg(workers=None), 0.0).n == 48


def test_the_gate_runner_substitutes_the_workers(tmp_path):
    """Every command that runs goes through run_command_gate: the placeholder never
    reaches the shell, and the inputs are in the evidence."""
    from ddflow.config import Config
    from ddflow.services.gates import GateDef, run_command_gate

    c = Config.load(tmp_path)
    c.ci.workers = "3"
    g = GateDef(id="probe", command="echo n={workers}", cwd="repo")
    outcome, ev = run_command_gate(g, tmp_path, cfg=c)
    assert outcome == "passed" and "n=3" in ev["tail"]
    assert ev["command"] == "echo n={workers}" and ev["command_run"] == "echo n=3"
    assert ev["workers"]["n"] == 3 and ev["workers"]["setting"] == "3"
