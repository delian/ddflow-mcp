"""`seedpack.Knobs.from_config` reads the `[swarm] seed_*` knobs (SW-seed-pack-config)."""

from __future__ import annotations

import dataclasses

import pytest

from ddflow.config import Config
from ddflow.services.swarm import seedpack as S

FIELDS = [f.name for f in dataclasses.fields(S.Knobs)]


def test_defaults_match_dataclass_defaults():
    assert S.Knobs.from_config(Config()) == S.Knobs()


@pytest.mark.parametrize("field", FIELDS)
def test_every_field_has_a_seed_knob(field):
    cfg = Config()
    assert hasattr(cfg.swarm, "seed_" + field)
    setattr(cfg.swarm, "seed_" + field, 77)
    assert getattr(S.Knobs.from_config(cfg), field) == 77


def test_config_default_equals_knob_default():
    cfg = Config()
    for f in dataclasses.fields(S.Knobs):
        assert getattr(cfg.swarm, "seed_" + f.name) == f.default
