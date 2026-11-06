"""The packaged `default.toml`: generated, pinned, and the lowest config layer (CE-defaults-file).

`Config()` and `DEFAULT_GATES` are still the source of the values (CE-defaults-source moves
them): this file proves the generated copy says exactly what they say, that `Config.load`
reads it first, and that a damaged copy can not stop a command, the one that repairs it included.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest
from _cli_helpers import run_cli
from _wait import slow_factor

from ddflow.api import setup as API
from ddflow.config import Config, _packaged
from ddflow.config_sections._layers import PACKAGED_DEFAULTS
from ddflow.services.gates.defs import DEFAULT_GATES, GateDef
from ddflow.views import default_toml as DT

#: Best of five, in milliseconds, for one process's read, parse and comparison. Measured at
#: about 5 ms on the reference host (R555dc3c874); the ceiling leaves room for a shared host.
LOAD_BUDGET_MS = 25.0


def _project(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".ddflow").mkdir(parents=True)
    (repo / ".ddflow" / "config.toml").write_text("[lease]\nttl_s = 1800\n", "utf-8")
    return repo


def _shipped() -> dict:
    return tomllib.loads(PACKAGED_DEFAULTS.read_text("utf-8"))


@pytest.fixture
def packaged(monkeypatch):
    """Point the layer at a file of the test's own, and forget it afterwards."""

    def use(path: Path) -> None:
        monkeypatch.setattr(_packaged, "PACKAGED_DEFAULTS", path)
        _packaged.packaged.cache_clear()
        _packaged._SAID.clear()

    yield use
    _packaged.packaged.cache_clear()
    _packaged._SAID.clear()


# -- the generated file ---------------------------------------------------------------
def test_the_shipped_file_is_what_the_declarations_render():
    """Fails when a knob or a default gate changed: `ddflow pins regenerate --only default-toml`."""
    assert PACKAGED_DEFAULTS.read_text("utf-8") == DT.render()


def test_every_knob_is_in_the_file_with_its_exact_default():
    cfg, data = Config(), _shipped()
    for sec in cfg._sections():
        want = dataclasses.asdict(getattr(cfg, sec))
        assert data[sec] == want, sec


def test_the_file_names_no_section_or_knob_the_code_lacks():
    cfg = Config()
    assert set(_shipped()) == {*cfg._sections(), "gate"}


def test_the_default_gates_rebuild_from_their_tables():
    """`required` is left out: `[gates].required` decides it (B4d206ede45), and `load_gates`
    would otherwise read a table that restates it as a project override."""
    gates = _shipped()["gate"]
    assert list(gates) == list(DEFAULT_GATES)
    for gid, gate in DEFAULT_GATES.items():
        assert "required" not in gates[gid] and "id" not in gates[gid]
        built = GateDef(id=gid, required=gate.required, **gates[gid])
        assert built == gate, gid


def test_required_gates_stay_stated_once_in_the_gates_section():
    data = _shipped()
    assert data["gates"]["required"] == Config().gates.required
    assert data["gates"]["task_pipeline"] == Config().gates.task_pipeline
    assert data["flow"]["model"] == Config().flow.model


def test_the_file_stays_under_the_size_limit():
    assert PACKAGED_DEFAULTS.stat().st_size < Config().quality.max_file_bytes


@pytest.mark.parametrize("damage", [b"\xff\xfe not utf-8", b""], ids=["bytes", "empty"])
def test_a_damaged_file_is_stale_to_the_check_and_repaired_by_the_write(
    tmp_path, monkeypatch, damage
):
    """The repair must survive what it repairs."""
    path = tmp_path / "default.toml"
    monkeypatch.setattr(DT, "PACKAGED_DEFAULTS", path)
    path.write_bytes(damage)
    assert DT.main(["--check"]) == 1
    assert DT.main([]) == 0
    assert path.read_text("utf-8") == DT.render()


def test_a_stale_file_is_reported_by_the_check_and_repaired_by_the_write(tmp_path, monkeypatch):
    path = tmp_path / "default.toml"
    monkeypatch.setattr(DT, "PACKAGED_DEFAULTS", path)
    path.write_text("[lease]\nttl_s = 1\n", "utf-8")
    assert DT.main(["--check"]) == 1
    assert DT.main([]) == 0
    assert path.read_text("utf-8") == DT.render()
    assert DT.main(["--check"]) == 0


# -- the lowest layer -----------------------------------------------------------------
def test_a_value_only_the_packaged_file_holds_is_loaded_and_still_called_default(
    packaged, tmp_path
):
    path = tmp_path / "default.toml"
    path.write_text("[lease]\nttl_s = 4242\n", "utf-8")
    packaged(path)
    cfg = Config.load(None, env={})
    assert cfg.lease.ttl_s == 4242
    assert cfg.sources["lease.ttl_s"] == "default"


def test_project_local_and_env_layers_all_beat_the_packaged_file(packaged, tmp_path):
    path = tmp_path / "default.toml"
    path.write_text("[lease]\nttl_s = 4242\nheartbeat_s = 42\n", "utf-8")
    packaged(path)
    repo = tmp_path / "repo"
    (repo / ".ddflow" / "local").mkdir(parents=True)
    (repo / ".ddflow" / "config.toml").write_text("[lease]\nttl_s = 7000\n", "utf-8")
    cfg = Config.load(repo, env={})
    assert (cfg.lease.ttl_s, cfg.sources["lease.ttl_s"]) == (7000, "file")
    assert cfg.lease.heartbeat_s == 42
    (repo / ".ddflow" / "local" / "config.toml").write_text("[lease]\nttl_s = 7100\n", "utf-8")
    assert Config.load(repo, env={}).lease.ttl_s == 7100
    env = {"DDFLOW_LEASE_TTL_S": "7200"}
    assert Config.load(repo, env=env).lease.ttl_s == 7200


def test_two_loads_never_share_a_packaged_list(packaged, tmp_path):
    path = tmp_path / "default.toml"
    path.write_text('[gates]\nrequired = ["merge"]\n', "utf-8")
    packaged(path)
    Config.load(None, env={}).gates.required.append("oops")
    assert Config.load(None, env={}).gates.required == ["merge"]


def test_the_file_is_read_once_per_process(packaged, tmp_path, monkeypatch):
    path = tmp_path / "default.toml"
    path.write_text("[lease]\nttl_s = 4242\n", "utf-8")
    packaged(path)
    reads = []
    real = _packaged.read_toml
    monkeypatch.setattr(_packaged, "read_toml", lambda p: reads.append(p) or real(p))
    for _ in range(3):
        assert Config.load(None, env={}).lease.ttl_s == 4242
    assert reads == [path]


@pytest.mark.parametrize(
    "text",
    [
        "<<<<<<< ours\n[lease]\nttl_s = 1\n=======\nttl_s = 2\n>>>>>>> theirs\n",
        "[lease\nttl_s = ",
    ],
    ids=["merge-conflict", "malformed"],
)
def test_a_damaged_file_warns_and_leaves_the_built_in_defaults(packaged, tmp_path, capsys, text):
    """`ddflow pins regenerate` is the repair and it loads config: it must still run."""
    path = tmp_path / "default.toml"
    path.write_text(text, "utf-8")
    packaged(path)
    assert Config.load(None, env={}).lease.ttl_s == Config().lease.ttl_s
    Config.load(None, env={})
    err = capsys.readouterr().err
    assert err.count("ddflow: warning") == 1 and "pins regenerate" in err


def test_a_missing_file_warns_and_leaves_the_built_in_defaults(packaged, tmp_path, capsys):
    packaged(tmp_path / "absent.toml")
    assert Config.load(None, env={}).lease.ttl_s == Config().lease.ttl_s
    assert "absent.toml" in capsys.readouterr().err


def test_a_key_the_code_lost_is_drift_and_never_an_unknown_knob(packaged, tmp_path, capsys):
    """A branch removed a knob and has not regenerated: said like any other drift, and not as
    a project's unknown key, which `doctor` would blame on the project's own config."""
    path = tmp_path / "default.toml"
    path.write_text("[lease]\nno_such_knob = 1\n[no_such_section]\nx = 1\n", "utf-8")
    packaged(path)
    assert not Config.load(None, env={}).unknown_knobs
    err = capsys.readouterr().err
    assert "lease.no_such_knob" in err and "[no_such_section]" in err
    assert "pins regenerate" in err


def test_a_known_section_that_is_not_a_table_warns(packaged, tmp_path, capsys):
    path = tmp_path / "default.toml"
    path.write_text('review = "not-a-table"\n', "utf-8")
    packaged(path)
    assert Config.load(None, env={}).review == Config().review
    err = capsys.readouterr().err
    assert "[review]" in err and "pins regenerate" in err


def test_the_shipped_file_loads_without_a_word(capsys):
    """`[gate.*]` is no section of `Config`, and that is not drift."""
    _packaged.packaged.cache_clear()
    _packaged._SAID.clear()
    Config.load(None, env={})
    assert capsys.readouterr().err == ""


def test_importing_config_does_not_import_the_generator():
    """The deterministic half of the budget (r-perf-budget): the hook path pays for the file,
    never for the renderer or the gate definitions behind it."""
    code = (
        "import sys, ddflow.config; ddflow.config.Config.load(None, env={}); "
        "print([m for m in ('ddflow.views.default_toml', 'ddflow.services.gates.defs') "
        "if m in sys.modules])"
    )
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True
    )
    assert done.stdout.strip() == "[]"


def test_a_dunder_key_is_not_taken_for_a_knob(packaged, tmp_path, capsys):
    """Every object has `__class__`: only a declared field is a knob."""
    path = tmp_path / "default.toml"
    path.write_text('[lease]\n__class__ = "broken"\n__module__ = "broken"\n', "utf-8")
    packaged(path)
    assert Config.load(None, env={}).lease.ttl_s == Config().lease.ttl_s
    assert "lease.__class__" in capsys.readouterr().err


def test_a_stale_value_applies_but_never_silently(packaged, tmp_path, capsys):
    """The layer wins over the dataclass, as it must once CE-defaults-source moves the values;
    until then any difference is drift, so it is said, once, with the repair."""
    path = tmp_path / "default.toml"
    path.write_text("[lease]\nttl_s = 4242\nheartbeat_s = 42\n", "utf-8")
    packaged(path)
    assert Config.load(None, env={}).lease.ttl_s == 4242
    Config.load(None, env={})
    err = capsys.readouterr().err
    assert err.count("ddflow: warning") == 1
    assert "lease.ttl_s" in err and "lease.heartbeat_s" in err and "pins regenerate" in err
    assert "built-in defaults apply" not in err


def test_the_stale_warning_names_a_bounded_number_of_keys(packaged, tmp_path, capsys):
    knobs = [f.name for f in dataclasses.fields(Config().lease) if f.type == "int"]
    path = tmp_path / "default.toml"
    path.write_text("[lease]\n" + "".join(f"{k} = 987654\n" for k in knobs), "utf-8")
    packaged(path)
    Config.load(None, env={})
    err = capsys.readouterr().err
    assert len(knobs) > _packaged.DRIFT_SHOWN
    assert err.count("lease.") == _packaged.DRIFT_SHOWN
    assert f"and {len(knobs) - _packaged.DRIFT_SHOWN} more" in err


def test_a_bad_value_warns_and_keeps_the_built_in_default(packaged, tmp_path, capsys):
    path = tmp_path / "default.toml"
    path.write_text('[flow]\nmodel = "no-such-model"\n[lease]\nttl_s = 4242\n', "utf-8")
    packaged(path)
    cfg = Config.load(None, env={})
    assert cfg.flow.model == Config().flow.model and cfg.lease.ttl_s == 4242
    assert "flow.model" in capsys.readouterr().err


def test_load_adds_no_work_when_the_file_matches(monkeypatch):
    """The pinned state: nothing differs, so no knob is coerced or re-sourced."""
    calls = []
    real = Config._apply_knob
    monkeypatch.setattr(Config, "_apply_knob", lambda self, *a: calls.append(a) or real(self, *a))
    Config.load(None, env={})
    assert calls == []


def test_the_load_cost_stays_inside_its_budget():
    """CONFORMANCE: the hook path must not get slower than this. Best of five and scaled by
    `slow_factor`, so a busy host reads as the cost and not as the load on it; the counters
    above are what gate hard (r-perf-budget)."""
    best = float("inf")
    for _ in range(5):
        _packaged.packaged.cache_clear()
        start = time.perf_counter()
        _packaged.apply_packaged(Config())
        best = min(best, time.perf_counter() - start)
    assert best * 1000 < LOAD_BUDGET_MS * slow_factor()


# -- the surfaces ---------------------------------------------------------------------
def test_configure_prints_the_packaged_file(tmp_path):
    out = API.configure(tmp_path, API.ConfigEdit(defaults=True))
    assert out.exit == 0
    assert out.data["text"] == PACKAGED_DEFAULTS.read_text("utf-8")


def test_write_is_refused_outside_the_tree_the_code_came_from(tmp_path):
    """Run from a worktree the `ddflow` on PATH is the primary's code: a write there would
    land in another agent's tree."""
    before = PACKAGED_DEFAULTS.read_bytes()
    out = API.configure(tmp_path, API.ConfigEdit(defaults=True, write=True))
    assert out.exit == 3
    assert "ddflow.views.default_toml" in out.reason
    assert PACKAGED_DEFAULTS.read_bytes() == before


def test_write_is_refused_for_an_install_inside_the_repository(tmp_path, monkeypatch):
    """A `.venv` under the repository passes a path test and is still not the source."""
    target = tmp_path / ".venv/lib/python3/site-packages/ddflow/templates/default.toml"
    target.parent.mkdir(parents=True)
    target.write_text("installed\n", "utf-8")
    monkeypatch.setattr(DT, "PACKAGED_DEFAULTS", target)
    out = API.configure(tmp_path, API.ConfigEdit(defaults=True, write=True))
    assert out.exit == 3
    assert target.read_text("utf-8") == "installed\n"


@pytest.mark.parametrize(
    "argv",
    [
        ("config", "defaults", "--append-toml", "[lease]\nttl_s = 9\n"),
        ("config", "defaults", "--local"),
        ("config", "defaults", "--filter", "lease"),
        ("config", "defaults", "--explain"),
    ],
    ids=["append", "local", "filter", "explain"],
)
def test_a_flag_defaults_can_not_honour_is_refused_not_dropped(tmp_path, argv):
    code, out, err = run_cli(_project(tmp_path), *argv)
    assert code == 1 and out == ""
    assert "--write" in err and argv[2] in err


@pytest.mark.parametrize(
    "argv",
    [("config", "--write"), ("config", "--set", "lease.ttl_s", "9", "--write")],
    ids=["bare", "with-set"],
)
def test_write_without_defaults_changes_nothing(tmp_path, argv):
    repo = _project(tmp_path)
    before = (repo / ".ddflow" / "config.toml").read_bytes()
    code, _out, err = run_cli(repo, *argv)
    assert code == 1 and "ddflow config defaults" in err
    assert (repo / ".ddflow" / "config.toml").read_bytes() == before


def test_set_beside_write_is_named_as_the_problem(tmp_path):
    """The message blames `--set`, not a missing `defaults`, and nothing is written."""
    before = PACKAGED_DEFAULTS.read_bytes()
    code, out, err = run_cli(_project(tmp_path), "config", "--set", "lease.ttl_s", "9", "--write")
    assert code == 1 and out == ""
    assert "--set" in err and "goes with `defaults`" not in err
    assert PACKAGED_DEFAULTS.read_bytes() == before


def test_setting_a_key_to_the_word_defaults_is_still_a_plain_write(tmp_path):
    """Not the packaged file: `--set KEY defaults` goes to the writer with the string."""
    code, out, err = run_cli(_project(tmp_path), "config", "--set", "lease.ttl_s", "defaults")
    assert code != 0 and out == ""
    assert "lease.ttl_s" in err and "goes with" not in err and "does not go with" not in err


@pytest.mark.parametrize("how", ["denied", "loop"])
def test_an_unstatable_path_reads_as_missing(tmp_path, monkeypatch, how):
    """`Path.is_file()` raises for a permission error or a symlink loop: never out of `current`."""
    path = tmp_path / "default.toml"
    monkeypatch.setattr(DT, "PACKAGED_DEFAULTS", path)
    if how == "loop":
        path.symlink_to(path)
    else:

        def deny(self):
            raise PermissionError(13, "denied")

        monkeypatch.setattr(Path, "is_file", deny)
    assert DT.current() == ""
    assert DT.main(["--check"]) == 1


@pytest.mark.parametrize(
    ("section", "knob", "raw"),
    [("lease", "ttl_s", "true"), ("lease", "heartbeat_s", "false")],
    ids=["true-for-int", "false-for-int"],
)
def test_a_bool_for_an_int_knob_is_not_called_a_non_boolean(
    packaged, tmp_path, capsys, section, knob, raw
):
    path = tmp_path / "default.toml"
    path.write_text(f"[{section}]\n{knob} = {raw}\n", "utf-8")
    packaged(path)
    cfg = Config.load(None, env={})
    assert getattr(getattr(cfg, section), knob) == getattr(getattr(Config(), section), knob)
    err = capsys.readouterr().err
    assert f"{section}.{knob}" in err and "not a boolean" not in err


def test_write_without_defaults_leaves_the_packaged_file_alone(tmp_path):
    before = PACKAGED_DEFAULTS.read_bytes()
    repo = _project(tmp_path)
    for argv in (("config", "--write"), ("config", "--set", "lease.ttl_s", "9", "--write")):
        assert run_cli(repo, *argv)[0] == 1
    assert PACKAGED_DEFAULTS.read_bytes() == before


def test_a_file_that_is_not_a_regular_file_reads_as_missing_and_never_blocks(tmp_path, monkeypatch):
    """A FIFO at the path would block a read forever."""
    fifo = tmp_path / "default.toml"
    os.mkfifo(fifo)
    monkeypatch.setattr(DT, "PACKAGED_DEFAULTS", fifo)
    assert DT.current() == ""


def test_printing_a_missing_or_undecodable_file_fails_cleanly(tmp_path, monkeypatch):
    path = tmp_path / "default.toml"
    monkeypatch.setattr(DT, "PACKAGED_DEFAULTS", path)
    assert API.configure(tmp_path, API.ConfigEdit(defaults=True)).exit == 1  # missing
    path.write_bytes(b"\xff")
    assert API.configure(tmp_path, API.ConfigEdit(defaults=True)).exit == 0  # replaced, printed


def test_an_int_for_a_bool_knob_is_a_bad_value_not_an_equal_one(packaged, tmp_path, capsys):
    """`False == 0` in Python, and TOML has no such equality."""
    knob = next(f.name for f in dataclasses.fields(Config().lease) if f.type == "bool")
    path = tmp_path / "default.toml"
    path.write_text(f"[lease]\n{knob} = {int(getattr(Config().lease, knob))}\n", "utf-8")
    packaged(path)
    cfg = Config.load(None, env={})
    assert isinstance(getattr(cfg.lease, knob), bool)
    assert f"lease.{knob}" in capsys.readouterr().err


def test_the_foreign_gate_tables_are_passed_over_by_design():
    """`[gate.*]` is `Config._FOREIGN_TABLES`: another reader owns it, so it is not drift."""
    assert "gate" in Config._FOREIGN_TABLES and "gate" in _shipped()


def test_key_drift_points_at_the_repair(packaged, tmp_path, capsys):
    path = tmp_path / "default.toml"
    path.write_text("[lease]\nno_such_knob = 1\n", "utf-8")
    packaged(path)
    Config.load(None, env={})
    assert "pins regenerate --only default-toml" in capsys.readouterr().err


def test_the_cli_prints_the_file_whole(tmp_path):
    code, out, _err = run_cli(_project(tmp_path), "config", "defaults")
    assert code == 0
    assert out.rstrip("\n") == PACKAGED_DEFAULTS.read_text("utf-8").rstrip("\n")


def test_write_regenerates_inside_the_tree(tmp_path, monkeypatch):
    target = tmp_path / "ddflow" / "templates" / "default.toml"
    target.parent.mkdir(parents=True)
    target.write_text("stale\n", "utf-8")
    monkeypatch.setattr(DT, "PACKAGED_DEFAULTS", target)
    out = API.configure(tmp_path, API.ConfigEdit(defaults=True, write=True))
    assert out.exit == 0 and out.data["changed"] is True
    assert target.read_text("utf-8") == DT.render()
    again = API.configure(tmp_path, API.ConfigEdit(defaults=True, write=True))
    assert again.data["changed"] is False
