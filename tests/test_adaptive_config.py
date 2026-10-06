"""[schedule] knobs for adaptive parallelism (B-af-config, D-adaptive-flow-accepted).

Every test runs in a throwaway project: no companions, no network, no host signal.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from conftest import run_cli

import ddflow.config as C
from ddflow.config import Config

OK, FAIL, REFUSED = 0, 1, 3

NEW_KEYS = (
    "schedule.parallel",
    "schedule.max_parallel_min",
    "schedule.max_parallel_max",
    "schedule.adapt_up_after_s",
    "schedule.adapt_cooldown_s",
    "schedule.signal_interval_s",
    "schedule.signals",
)


def _load(tmp_path: Path, text: str, local: str = "") -> Config:
    (tmp_path / ".ddflow").mkdir(exist_ok=True)
    (tmp_path / ".ddflow" / "config.toml").write_text(text, "utf-8")
    if local:
        (tmp_path / ".ddflow" / "local").mkdir(exist_ok=True)
        (tmp_path / ".ddflow" / "local" / "config.toml").write_text(local, "utf-8")
    return Config.load(tmp_path)


def _edit(repo: Path, header: str, replacement: str) -> None:
    """Hand-edit the committed config, as an operator of an older project would."""
    path = repo / ".ddflow" / "config.toml"
    text = path.read_text("utf-8")
    assert text.count(header) == 1, header
    path.write_text(text.replace(header, replacement), "utf-8")


def _mcp_configure(repo: Path, **arguments) -> dict:
    from ddflow.surfaces.mcp import Server

    msg = {"name": "ddflow_configure", "arguments": arguments}
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": msg}
    return Server(repo).handle(req)["result"]


# -- defaults and upgrade --------------------------------------------------------------


def test_defaults_are_auto_4_2_8() -> None:
    s = Config().schedule
    assert (s.parallel, s.max_parallel_tasks, s.max_parallel_min, s.max_parallel_max) == (
        "auto",
        4,
        2,
        8,
    )
    assert (s.adapt_up_after_s, s.adapt_cooldown_s, s.signal_interval_s) == (600, 300, 60)
    assert Config().worktree.max_parallel == 0
    assert s.signals["load_per_core"] == {"low": 0.15, "high": 0.75}
    assert set(s.signals["enabled"]) == set(C.FLOW_SIGNALS)


def test_flow_signal_names_are_the_ones_the_controller_and_the_log_produce() -> None:
    from ddflow.core import flowcontrol as FC
    from ddflow.core import flowsignals as FS

    log_names = set(FS.Signals().as_signals())
    assert set(C.FLOW_SIGNALS) == set(FC.HOST_SIGNALS) | log_names


def test_the_default_marks_are_the_controllers() -> None:
    from ddflow.core import flowcontrol as FC

    default = FC.Params().thresholds
    marks = {k: v for k, v in Config().schedule.signals.items() if k != "enabled"}
    assert marks == {k: {"low": t.low, "high": t.high} for k, t in default.items()}


def test_an_old_config_with_explicit_fours_loads_unchanged(tmp_path: Path) -> None:
    old = "[worktree]\nenabled = true\nmax_parallel = 4\n\n[schedule]\nmax_parallel_tasks = 4\n"
    cfg = _load(tmp_path, old)
    assert cfg.unknown_knobs == []
    assert cfg.schedule.parallel == "auto"  # absent = auto
    assert cfg.schedule.max_parallel_tasks == 4  # the start value
    assert cfg.worktree.max_parallel == 4  # an independent cap, as written


def test_an_old_explicit_worktree_cap_is_reported_with_its_remedy(repo: Path) -> None:
    from ddflow.services import workflow as WF
    from ddflow.services.gates import load_gates

    assert run_cli(repo, "init")[0] == OK
    _edit(repo, "[worktree]\n", "[worktree]\nmax_parallel = 4\n")
    cfg = Config.load(repo)
    found = [
        f for f in WF.check(cfg, load_gates(repo, cfg)) if f.subject == "worktree.max_parallel"
    ]
    assert len(found) == 1 and found[0].level != WF.PROBLEM
    assert "ddflow config --set worktree.max_parallel 0" in found[0].detail
    _code, out, _e = run_cli(repo, "doctor")
    assert "worktree.max_parallel" in out and "--set worktree.max_parallel 0" in out


def test_a_fresh_init_reports_no_cap_and_writes_no_explicit_four(repo: Path) -> None:
    from ddflow.services import workflow as WF
    from ddflow.services.gates import load_gates

    assert run_cli(repo, "init")[0] == OK
    data = tomllib.loads((repo / ".ddflow" / "config.toml").read_text("utf-8"))
    assert "max_parallel" not in data.get("worktree", {})
    assert "max_parallel_tasks" not in data.get("schedule", {})
    cfg = Config.load(repo)
    subjects = {f.subject for f in WF.check(cfg, load_gates(repo, cfg))}
    assert not subjects & {"worktree.max_parallel", "schedule.max_parallel_tasks"}


def test_fixed_with_four_reproduces_the_old_cap(repo: Path) -> None:
    from ddflow.core.model import fold
    from ddflow.core.schedule import plan
    from ddflow.infra.log import EventLog

    log = EventLog(repo, "a")
    for i in range(6):
        log.append("task.added", f"T{i}", {"title": "t", "kind": "task", "globs": [f"f{i}.py"]})
    st = fold(log.read_all(), strict=False)
    old = Config()
    old.worktree.max_parallel = 4
    old.schedule.parallel = "fixed"
    new = Config()
    new.schedule.parallel = "fixed"
    for cfg in (old, new):
        p = plan(st, cfg)
        assert len(p.ready) == 4
        assert "schedule.max_parallel_tasks=4" in p.cap_note


def test_a_zero_worktree_cap_follows_the_schedule_limit(repo: Path) -> None:
    from ddflow.core.model import fold
    from ddflow.core.schedule import plan
    from ddflow.infra.log import EventLog

    log = EventLog(repo, "a")
    for i in range(10):
        log.append("task.added", f"T{i}", {"title": "t", "kind": "task", "globs": [f"f{i}.py"]})
    st = fold(log.read_all(), strict=False)
    cfg = Config()
    assert cfg.worktree.max_parallel == 0
    assert len(plan(st, cfg).ready) == cfg.schedule.max_parallel_tasks
    cfg.worktree.max_parallel = 2  # a nonzero value is still an independent cap
    p = plan(st, cfg)
    assert len(p.ready) == 2 and "worktree.max_parallel=2" in p.cap_note


# -- validation ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("schedule.parallel", "sometimes"),
        ("schedule.max_parallel_min", "0"),
        ("schedule.max_parallel_max", "0"),
        ("schedule.adapt_up_after_s", "-1"),
        ("schedule.adapt_cooldown_s", "-1"),
        ("schedule.signal_interval_s", "0"),
        ("worktree.max_parallel", "-1"),
        ("schedule.max_parallel_min", "5"),  # above the start value 4
        ("schedule.max_parallel_max", "3"),  # below the start value 4
        ("schedule.max_parallel_tasks", "9"),  # above the ceiling 8
        ("schedule.max_parallel_tasks", "0"),
        ("schedule.max_parallel_min", "abc"),  # the wrong type is refused the same way
        ("schedule.signals.enabled", '["load_per_core", "moon_phase"]'),
        ("schedule.signals.moon_phase.high", "0.5"),
        ("schedule.signals.load_per_core.low", "0.9"),  # above its high mark 0.75
        ("schedule.signals.load_per_core.loud", "0.9"),
    ],
)
def test_invalid_values_are_refused_naming_the_key(repo: Path, key: str, value: str) -> None:
    assert run_cli(repo, "init")[0] == OK
    path = repo / ".ddflow" / "config.toml"
    before = path.read_text("utf-8")
    code, _out, err = run_cli(repo, "config", "--set", key, value)
    assert code == REFUSED, (code, err)
    named = key if not key.startswith("schedule.signals") else "schedule.signals"
    assert named in err, err
    assert path.read_text("utf-8") == before


def test_an_unknown_signal_is_refused_with_the_list(repo: Path) -> None:
    assert run_cli(repo, "init")[0] == OK
    code, _out, err = run_cli(repo, "config", "--set", "schedule.signals.enabled", '["moon"]')
    assert code == REFUSED
    assert "moon" in err and all(name in err for name in C.FLOW_SIGNALS), err


def test_fixed_mode_does_not_need_the_auto_range(repo: Path) -> None:
    assert run_cli(repo, "init")[0] == OK
    assert run_cli(repo, "config", "schedule.parallel", "fixed")[0] == OK
    code, _o, err = run_cli(repo, "config", "--set", "schedule.max_parallel_tasks", "12")
    assert code == OK, err
    assert Config.load(repo).schedule.max_parallel_tasks == 12


def test_an_inconsistent_file_still_loads_and_is_reported(repo: Path) -> None:
    """A project that set max_parallel_tasks above the new ceiling before auto existed
    keeps loading (upgrade), and doctor says how to make the range consistent."""
    from ddflow.services import workflow as WF
    from ddflow.services.gates import load_gates

    assert run_cli(repo, "init")[0] == OK
    _edit(repo, "[schedule]\n", "[schedule]\nmax_parallel_tasks = 10\n")
    cfg = Config.load(repo)
    assert cfg.schedule.max_parallel_tasks == 10
    found = [f for f in WF.check(cfg, load_gates(repo, cfg)) if f.subject.startswith("schedule.")]
    assert found and "max_parallel_max" in found[0].detail
    # and an unrelated edit is not refused for the problem that was already there
    assert run_cli(repo, "config", "--set", "lease.ttl_s", "900")[0] == OK


# -- every surface sets every knob ----------------------------------------------------


def test_config_short_form_and_local_layer(repo: Path) -> None:
    assert run_cli(repo, "init")[0] == OK
    assert run_cli(repo, "config", "schedule.parallel", "fixed")[0] == OK
    code, _o, err = run_cli(repo, "config", "--local", "--set", "schedule.max_parallel_max", "6")
    assert code == OK, err
    committed = tomllib.loads((repo / ".ddflow" / "config.toml").read_text("utf-8"))
    assert "max_parallel_max" not in committed["schedule"]
    cfg = Config.load(repo)
    assert cfg.schedule.parallel == "fixed"
    assert cfg.schedule.max_parallel_max == 6
    assert cfg.sources["schedule.max_parallel_max"] == "local"


def test_the_local_layer_wins_over_a_shared_ceiling(tmp_path: Path) -> None:
    cfg = _load(
        tmp_path,
        "[schedule]\nmax_parallel_max = 12\n",
        local="[schedule]\nmax_parallel_max = 6\n",
    )
    assert cfg.schedule.max_parallel_max == 6


def test_a_signal_mark_merges_into_the_defaults(tmp_path: Path) -> None:
    cfg = _load(
        tmp_path,
        "[schedule.signals.memory_pressure]\nlow = 0.7\nhigh = 0.9\ncritical = 0.97\n",
        local="[schedule.signals.load_per_core]\nhigh = 0.9\n",
    )
    sig = cfg.schedule.signals
    assert sig["memory_pressure"] == {"low": 0.7, "high": 0.9, "critical": 0.97}
    assert sig["load_per_core"] == {"low": 0.15, "high": 0.9}  # low kept, high from local
    assert set(sig["enabled"]) == set(C.FLOW_SIGNALS)


def test_a_signal_mark_is_settable_from_the_cli(repo: Path) -> None:
    assert run_cli(repo, "init")[0] == OK
    code, _o, err = run_cli(repo, "config", "--set", "schedule.signals.load_per_core.high", "0.8")
    assert code == OK, err
    code, _o, err = run_cli(
        repo, "config", "--set", "schedule.signals.enabled", '["load_per_core", "disk_pressure"]'
    )
    assert code == OK, err
    sig = Config.load(repo).schedule.signals
    assert sig["load_per_core"] == {"low": 0.15, "high": 0.8}
    assert sig["enabled"] == ["load_per_core", "disk_pressure"]


@pytest.mark.parametrize(
    ("key", "value", "expect"),
    [
        ("schedule.parallel", "fixed", "fixed"),
        ("schedule.max_parallel_min", "3", 3),
        ("schedule.max_parallel_max", "7", 7),
        ("schedule.adapt_up_after_s", "900", 900),
        ("schedule.adapt_cooldown_s", "120", 120),
        ("schedule.signal_interval_s", "30", 30),
        ("worktree.max_parallel", "5", 5),
    ],
)
def test_ddflow_configure_round_trips_each_key(repo: Path, key: str, value: str, expect) -> None:
    assert run_cli(repo, "init")[0] == OK
    r = _mcp_configure(repo, set=key, value=value)
    assert not r.get("isError"), r
    sec, knob = key.split(".")
    assert getattr(getattr(Config.load(repo), sec), knob) == expect
    r = _mcp_configure(repo, set=key, value=value, local=True)
    assert not r.get("isError"), r
    loaded = Config.load(repo)
    assert loaded.sources[key] == "local"


def test_ddflow_configure_refuses_an_invalid_value(repo: Path) -> None:
    assert run_cli(repo, "init")[0] == OK
    r = _mcp_configure(repo, set="schedule.parallel", value="sometimes")
    assert r["_meta"]["exit"] == REFUSED, r
    assert "schedule.parallel" in json.dumps(r)


def test_config_explain_documents_every_new_key(repo: Path) -> None:
    assert run_cli(repo, "init")[0] == OK
    code, out, _e = run_cli(repo, "--json", "config", "--explain")
    assert code == OK
    rows = {r["key"]: r for r in json.loads(out)}
    for key in NEW_KEYS:
        assert key in rows, key
        assert len(rows[key]["doc"]) > 40, key
    assert "follow" in rows["worktree.max_parallel"]["doc"].lower()
    assert "not unlimited" in rows["worktree.max_parallel"]["doc"]


def test_schedule_parallel_is_an_enum_with_a_strictest_value() -> None:
    assert C.KNOB_CHOICES["schedule.parallel"] == ("auto", "fixed")
    assert C.strictest("schedule.parallel") == "fixed"
    assert C.KNOB_OUTWARD["schedule.parallel"] == frozenset()


def test_the_workflow_view_names_the_mode() -> None:
    from ddflow.services import workflow as WF

    assert "schedule.parallel" in WF.RULE_KEYS


# -- the controller reads the knobs ---------------------------------------------------


def test_the_controller_params_come_from_the_knobs() -> None:
    from ddflow.core import flowcontrol as FC
    from ddflow.core import flowparams as FP

    assert FP.params(Config()) == FC.Params()  # the shipped defaults agree
    cfg = Config()
    s = cfg.schedule
    s.max_parallel_tasks, s.max_parallel_min, s.max_parallel_max = 5, 3, 9
    s.adapt_up_after_s, s.adapt_cooldown_s, s.signal_interval_s = 900, 120, 30
    s.signals = C.merge_signals(s.signals, {"memory_pressure": {"low": 0.7, "high": 0.9}})
    p = FP.params(cfg)
    assert p.bounds() == (3, 5, 9)
    assert (p.adapt_up_after_s, p.cooldown_s, p.sample_every_s) == (900, 120, 30)
    assert p.quiet_after_decrease_s == 1800
    assert p.thresholds["memory_pressure"] == FC.Threshold(0.7, 0.9)


def test_a_disabled_signal_has_no_say() -> None:
    from ddflow.core import flowparams as FP

    cfg = Config()
    cfg.schedule.signals = C.merge_signals(cfg.schedule.signals, {"enabled": ["disk_pressure"]})
    p = FP.params(cfg)
    assert p.thresholds == {}  # load_per_core has marks but is not enabled
    assert p.host_signals == ("disk_pressure",)


def test_a_local_cap_is_given_a_local_remedy(repo: Path) -> None:
    from ddflow.services import workflow as WF
    from ddflow.services.gates import load_gates

    assert run_cli(repo, "init")[0] == OK
    assert run_cli(repo, "config", "--local", "--set", "worktree.max_parallel", "4")[0] == OK
    cfg = Config.load(repo)
    [f] = [f for f in WF.check(cfg, load_gates(repo, cfg)) if f.subject == "worktree.max_parallel"]
    assert "ddflow config --local --set worktree.max_parallel 0" in f.detail


def test_fixed_params_cannot_move() -> None:
    from ddflow.core import flowparams as FP

    cfg = Config()
    cfg.schedule.parallel = "fixed"
    cfg.schedule.max_parallel_tasks = 5
    assert FP.params(cfg).bounds() == (5, 5, 5)


def test_an_edit_over_an_unloadable_file_owns_the_result_s_problems(repo: Path) -> None:
    """When the text before the edit does not load, the result is judged on its own."""
    from ddflow.services.configwrite import _new_range_problems

    before = "[schedule]\nmax_parallel_min = 'x'\n"  # does not load
    after = "[schedule]\nmax_parallel_tasks = 10\n"
    assert _new_range_problems(repo, before, after, False)  # the result's problems are the edit's
    assert _new_range_problems(repo, before, "[schedule]\n", False) == set()  # a repair passes


def test_a_local_auto_choice_silences_the_start_value_note(repo: Path) -> None:
    from ddflow.services import workflow as WF
    from ddflow.services.gates import load_gates

    assert run_cli(repo, "init")[0] == OK
    _edit(repo, "[schedule]\n", "[schedule]\nmax_parallel_tasks = 4\n")
    (repo / ".ddflow" / "local").mkdir(exist_ok=True)
    (repo / ".ddflow" / "local" / "config.toml").write_text('[schedule]\nparallel = "auto"\n')
    cfg = Config.load(repo)
    subjects = [f.subject for f in WF.check(cfg, load_gates(repo, cfg))]
    assert "schedule.max_parallel_tasks" not in subjects  # the operator chose auto


def test_an_unreadable_sibling_layer_never_switches_the_range_guard_off(repo: Path) -> None:
    from ddflow.services.configwrite import _new_range_problems

    (repo / ".ddflow").mkdir()
    (repo / ".ddflow" / "config.toml").write_text("[schedule\n")  # the committed layer: broken
    after = "[schedule]\nmax_parallel_tasks = 10\n"
    found = _new_range_problems(repo, "", after, True)  # judged over the shipped defaults
    assert [k for k, _ in found] == ["schedule.max_parallel_tasks"]
    assert _new_range_problems(repo, after, after, True) == set()  # not introduced


def test_a_local_start_value_is_given_a_local_mode_remedy(repo: Path) -> None:
    from ddflow.services import workflow as WF
    from ddflow.services.gates import load_gates

    assert run_cli(repo, "init")[0] == OK
    (repo / ".ddflow" / "local").mkdir(exist_ok=True)
    (repo / ".ddflow" / "local" / "config.toml").write_text("[schedule]\nmax_parallel_tasks = 4\n")
    cfg = Config.load(repo)
    [f] = [
        f
        for f in WF.check(cfg, load_gates(repo, cfg))
        if f.subject == "schedule.max_parallel_tasks"
    ]
    assert "ddflow config --local --set schedule.parallel fixed" in f.detail
    assert ".ddflow/local/config.toml" in f.detail


def test_a_repair_over_an_unloadable_local_file_is_not_blamed_for_the_committed_range(
    repo: Path,
) -> None:
    from ddflow.services.configwrite import _new_range_problems

    (repo / ".ddflow").mkdir()
    (repo / ".ddflow" / "config.toml").write_text("[schedule]\nmax_parallel_min = 5\n")
    before = '[schedule]\nadapt_up_after_s = "x"\n'  # the local file does not load
    after = "[schedule]\nadapt_up_after_s = 30\n"
    assert _new_range_problems(repo, before, after, True) == set()
    worse = "[schedule]\nadapt_up_after_s = 30\nmax_parallel_max = 3\n"
    assert {k for k, _ in _new_range_problems(repo, before, worse, True)} == {
        "schedule.max_parallel_tasks"
    }
