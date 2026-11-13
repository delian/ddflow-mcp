"""A gate or CI command does not read the operator's secret-shaped variables (SEC-gate-env-secrets).

Reviewer keys are exported into the shell before `ddflow review`, and a gate command is project
code run through a shell: it could read and print them, and its output is kept in the committed
log. `[gates].scrub_secret_names` is `off | warn | on` (default `warn`): `warn` records the NAMES
of the matching variables in the gate's evidence (`env_secret_names`, never a value), `on` removes
them from the child's environment. `[gates].secret_name_patterns` says which names match, case-blind;
`[gates].keep_env` still preserves one.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from ddflow.config import Config
from ddflow.services import gates as G
from ddflow.services.gateenv import child_env, doctor_notes, secret_names


@pytest.fixture(autouse=True)
def _no_ambient_secrets(monkeypatch):
    """The shell that runs the suite may export its own keys and tokens: none is the test's."""
    for name in secret_names(os.environ, Config().gates):
        monkeypatch.delenv(name, raising=False)


VALUE = "plainvalue-not-a-real-key"
SHOW = 'printf "[%s]" "${MY_SERVICE_API_KEY-unset}"'


def _gate(tmp_path, monkeypatch, mode=None, cfg=None, command=SHOW, **env):
    monkeypatch.setenv("MY_SERVICE_API_KEY", VALUE)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    cfg = cfg or Config()
    if mode is not None:
        cfg.gates.scrub_secret_names = mode
    outcome, ev = G.run_command_gate(G.GateDef(id="t", command=command), tmp_path, cfg=cfg)
    assert outcome == "passed", ev
    return ev


def test_the_default_is_warn(tmp_path, monkeypatch):
    assert Config().gates.scrub_secret_names == "warn"
    ev = _gate(tmp_path, monkeypatch)
    assert ev["env_secret_mode"] == "warn"
    assert "MY_SERVICE_API_KEY" in ev["env_secret_names"]


def test_warn_records_the_names_and_never_a_value(tmp_path, monkeypatch):
    ev = _gate(tmp_path, monkeypatch, "warn")
    assert ev["env_secret_names"] == ["MY_SERVICE_API_KEY"]
    assert VALUE not in json.dumps(ev["env_secret_names"]) + ev["env_secret_mode"]


def test_on_removes_the_variable_from_the_child(tmp_path, monkeypatch):
    ev = _gate(tmp_path, monkeypatch, "on")
    assert "[unset]" in ev["tail"]
    assert VALUE not in ev["tail"]
    assert ev["env_secret_mode"] == "on"
    assert ev["env_secret_names"] == ["MY_SERVICE_API_KEY"]  # what was removed, by name


def test_off_neither_scrubs_nor_names(tmp_path, monkeypatch):
    ev = _gate(tmp_path, monkeypatch, "off")
    assert f"[{VALUE}]" in ev["tail"]
    assert ev["env_secret_mode"] == "off"
    assert "env_secret_names" not in ev


def test_keep_env_preserves_a_secret_shaped_variable(tmp_path, monkeypatch):
    cfg = Config()
    cfg.gates.keep_env = ["MY_SERVICE_API_KEY"]
    ev = _gate(tmp_path, monkeypatch, "on", cfg)
    assert f"[{VALUE}]" in ev["tail"]
    assert "env_secret_names" not in ev or ev["env_secret_names"] == []


def test_the_names_are_matched_case_blind_and_by_the_configured_patterns():
    gates = Config().gates
    base = {
        "my_token": "x",
        "Db_Password": "x",
        "PATH": "/bin",
        "SOME_SECRET": "x",
        "A_PASSWD": "x",
    }
    assert secret_names(base, gates) == ["A_PASSWD", "Db_Password", "SOME_SECRET", "my_token"]
    gates.secret_name_patterns = ["_PATH"]
    assert secret_names({"X_PATH": "1", "MY_TOKEN": "2"}, gates) == ["X_PATH"]
    gates.secret_name_patterns = []
    assert secret_names(base, gates) == []


def test_child_env_scrubs_only_in_on_mode_and_ci_keep_env_applies():
    base = {"A_API_KEY": "1", "B_TOKEN": "2", "PATH": "/bin"}
    cfg = Config()
    assert child_env(cfg, base) == base  # warn: nothing removed
    cfg.gates.scrub_secret_names = "on"
    assert child_env(cfg, base) == {"PATH": "/bin"}
    cfg.ci.keep_env = ["B_TOKEN"]
    assert child_env(cfg, base) == {"PATH": "/bin"}  # a gate: [ci].keep_env is not its
    assert child_env(cfg, base, ci=True) == {"B_TOKEN": "2", "PATH": "/bin"}


def test_a_knobs_own_variable_is_never_secret_shaped_but_another_ddflow_name_is():
    base = {"DDFLOW_GATES_SCRUB_SECRET_NAMES": "on", "DDFLOW_MY_API_KEY": "t", "MY_TOKEN": "x"}
    assert secret_names(base, Config().gates) == ["DDFLOW_MY_API_KEY", "MY_TOKEN"]
    cfg = Config()
    cfg.gates.scrub_secret_names = "on"
    assert child_env(cfg, base) == {
        "DDFLOW_GATES_SCRUB_SECRET_NAMES": "on",
    }


def test_the_doctor_note_names_the_ci_exception():
    seen = SimpleNamespace(evidence={"env_secret_mode": "warn", "env_secret_names": ["A_TOKEN"]})
    state = SimpleNamespace(items={"i": SimpleNamespace(gates={"a": seen})})
    assert "[ci].keep_env" in doctor_notes(state, Config())[0]


def test_a_name_in_scrub_env_is_not_reported_as_visible():
    cfg = Config()
    cfg.gates.scrub_env = ["X_TOKEN"]
    assert secret_names({"X_TOKEN": "1", "Y_TOKEN": "2"}, cfg.gates) == ["Y_TOKEN"]


def test_doctor_notes_the_names_a_gate_saw_in_warn_mode_only():
    def gate(ev):
        return SimpleNamespace(evidence=ev)

    seen = gate({"env_secret_mode": "warn", "env_secret_names": ["MY_SERVICE_API_KEY"]})
    scrubbed = gate({"env_secret_mode": "on", "env_secret_names": ["OTHER_TOKEN"]})
    state = SimpleNamespace(items={"i": SimpleNamespace(gates={"a": seen, "b": scrubbed})})

    notes = doctor_notes(state, Config())
    assert len(notes) == 1
    assert "MY_SERVICE_API_KEY" in notes[0]
    assert "OTHER_TOKEN" not in notes[0]
    assert "scrub_secret_names" in notes[0]
    cfg = Config()
    cfg.gates.scrub_secret_names = "on"
    assert doctor_notes(state, cfg) == []


def test_the_knob_is_settable_by_config(repo):
    from conftest import run_cli

    run_cli(repo, "init")
    run_cli(repo, "config", "--set", "gates.scrub_secret_names", "on")
    assert Config.load(repo).gates.scrub_secret_names == "on"
