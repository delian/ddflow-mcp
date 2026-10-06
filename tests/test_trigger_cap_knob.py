"""D-trigger-cap-knob: the global trigger firing cap is `[triggers].max_fires_per_hour`.

It was the constant `GLOBAL_MAX_PER_HOUR = 10` in services/triggers.py: an operator could
not lower it on a noisy project or stop every trigger at once. Now it is a knob: default
10, an integer from 0 (no trigger may fire) up to the fire tail the fold keeps, settable
on every surface, and a bad value in a config FILE fails closed to 0, the strictest
(D-enum-fallback-strict), rather than to the default.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_trigger_model import _file, proj  # noqa: F401 -- the fixture

from ddflow import config as C
from ddflow.api import schedule as A
from ddflow.core.model import TRIGGER_FIRES_KEPT, fold
from ddflow.infra.log import EventLog

KEY = "triggers.max_fires_per_hour"


def _cfg(repo: Path, text: str) -> C.Config:
    (repo / ".ddflow" / "config.toml").write_text(text)
    return C.Config.load(repo)


def test_the_default_is_ten(repo):
    assert C.Config().triggers.max_fires_per_hour == 10


def test_the_cap_is_read_from_the_config_file(proj):  # noqa: F811
    cfg = _cfg(proj, "[triggers]\nmax_fires_per_hour = 3\n")
    assert cfg.triggers.max_fires_per_hour == 3


@pytest.mark.parametrize("bad", ["-1", '"ten"', str(TRIGGER_FIRES_KEPT + 1), "true"])
def test_a_bad_file_value_fails_closed_to_zero(proj, bad):  # noqa: F811
    cfg = _cfg(proj, f"[triggers]\nmax_fires_per_hour = {bad}\n")
    assert cfg.triggers.max_fires_per_hour == 0
    assert "strictest" in cfg.sources[KEY]
    assert any(KEY in u for u in cfg.unknown_knobs)


@pytest.mark.parametrize("bad", ["-1", "ten", str(TRIGGER_FIRES_KEPT + 1)])
def test_the_write_path_refuses_a_bad_value(proj, bad):  # noqa: F811
    code, out, err = run_cli(proj, "config", "--set", KEY, bad)
    assert code != 0, (code, out, err)
    assert C.Config.load(proj).triggers.max_fires_per_hour == 10


def test_set_and_env_are_surfaces_too(proj, monkeypatch):  # noqa: F811
    code, out, err = run_cli(proj, "config", "--set", KEY, "4")
    assert code == 0, (code, out, err)
    assert C.Config.load(proj).triggers.max_fires_per_hour == 4
    monkeypatch.setenv("DDFLOW_TRIGGERS_MAX_FIRES_PER_HOUR", "2")
    assert C.Config.load(proj).triggers.max_fires_per_hour == 2


def test_the_knob_is_documented_with_its_fallback():
    doc = C.KNOB_DOCS[KEY]
    assert "0" in doc and "strictest" in doc


def _fail_gate(repo: Path) -> str:
    _file(
        repo,
        "red.toml",
        'event = "gate.failed"\nkey = "{subject}"\naction = { job = "fix" }\nenabled = true\n',
    )
    assert run_cli(repo, "task", "add", "T1", "--globs", "a.py")[0] == 0
    code, out, err = run_cli(
        repo, "gate", "record", "T1", "unit_tests", "--outcome", "failed", "--reason", "red"
    )
    assert code == 0, (code, out, err)
    return (datetime.now(UTC) + timedelta(minutes=1)).isoformat()


def test_the_evaluator_reads_the_cap_from_config(proj):  # noqa: F811
    now = _fail_gate(proj)
    _cfg(proj, "[triggers]\nmax_fires_per_hour = 0\n")
    out = A.trigger_evaluate(proj, now=now)
    [d] = out.data["decisions"]
    assert not d["fire"] and d["reason"] == "global_cap", d
    st = fold(EventLog(proj, "t").read_all())
    assert not [i for i in st.items if i.startswith("T-red-")]


def test_a_bad_file_value_stops_every_trigger(proj):  # noqa: F811
    now = _fail_gate(proj)
    _cfg(proj, "[triggers]\nmax_fires_per_hour = -5\n")
    [d] = A.trigger_evaluate(proj, now=now).data["decisions"]
    assert d["reason"] == "global_cap"


def test_the_config_bound_is_the_fold_tail():
    """The cap is counted from each trigger's fire tail, so it may not exceed it; config
    cannot import the fold, so the two numbers are held together here."""
    assert C.TRIGGERS_MAX_FIRES_LIMIT == TRIGGER_FIRES_KEPT
