"""The packaged `default.toml`: generated, pinned, and the lowest config layer (CE-defaults-file).

`Config()` and `DEFAULT_GATES` are still the source of the values (CE-defaults-source moves
them): this file proves the generated copy says exactly what they say, that `Config.load`
reads it first, and that a damaged copy can not stop a command, the one that repairs it included.
"""

from __future__ import annotations

import dataclasses
import time
import tomllib
from pathlib import Path

import pytest

from ddflow.api import setup as API
from ddflow.config import Config, _packaged
from ddflow.config_sections._layers import PACKAGED_DEFAULTS
from ddflow.services.gates.defs import DEFAULT_GATES, GateDef
from ddflow.views import default_toml as DT

#: Best of five, in milliseconds, for one process's read, parse and comparison. Measured at
#: about 5 ms on the reference host (R555dc3c874); the ceiling leaves room for a shared host.
LOAD_BUDGET_MS = 25.0


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
    assert err.count("default.toml") == 1 and "pins regenerate" in err


def test_a_missing_file_warns_and_leaves_the_built_in_defaults(packaged, tmp_path, capsys):
    packaged(tmp_path / "absent.toml")
    assert Config.load(None, env={}).lease.ttl_s == Config().lease.ttl_s
    assert "absent.toml" in capsys.readouterr().err


def test_a_key_the_code_lost_is_skipped_silently(packaged, tmp_path, capsys):
    """A branch removed a knob and has not regenerated: the pin says so, a command need not."""
    path = tmp_path / "default.toml"
    path.write_text("[lease]\nno_such_knob = 1\nttl_s = 4242\n[no_such_section]\nx = 1\n", "utf-8")
    packaged(path)
    cfg = Config.load(None, env={})
    assert cfg.lease.ttl_s == 4242 and not cfg.unknown_knobs
    assert capsys.readouterr().err == ""


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
    monkeypatch.setattr(
        Config, "_apply_knob", lambda self, *a: calls.append(a) or real(self, *a)
    )
    Config.load(None, env={})
    assert calls == []


def test_the_load_cost_stays_inside_its_budget():
    """CONFORMANCE: the hook path must not get slower than this. Best of five, so a busy
    host reads as the cost and not as the load on it."""
    best = float("inf")
    for _ in range(5):
        _packaged.packaged.cache_clear()
        start = time.perf_counter()
        _packaged.apply_packaged(Config())
        best = min(best, time.perf_counter() - start)
    assert best * 1000 < LOAD_BUDGET_MS


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
