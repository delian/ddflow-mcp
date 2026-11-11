"""`remote.Knobs.from_config` reads the `[swarm] remote_*` knobs (SW-remote-config)."""

from __future__ import annotations

import dataclasses

import pytest

from ddflow.config import Config
from ddflow.services.swarm import remote as R

FIELDS = [f.name for f in dataclasses.fields(R.Knobs)]

TUPLE_FIELDS = ("exclude", "ssh_argv")


def test_defaults_match_dataclass_defaults():
    assert R.Knobs.from_config(Config()) == R.Knobs()


@pytest.mark.parametrize("field", FIELDS)
def test_every_field_has_a_remote_knob(field):
    cfg = Config()
    assert hasattr(cfg.swarm, "remote_" + field)
    setattr(cfg.swarm, "remote_" + field, "x" if field not in TUPLE_FIELDS else ["x"])
    assert getattr(R.Knobs.from_config(cfg), field) == ("x",) if field in TUPLE_FIELDS else "x"


def test_scan_batch_files_reached_the_settled_field_name():
    # scan_batch_files replaced scan_batch_bytes at landing: from_config must read the knob
    # under the settled name, never the retired one.
    cfg = Config()
    cfg.swarm.remote_scan_batch_files = 5
    assert R.Knobs.from_config(cfg).scan_batch_files == 5
