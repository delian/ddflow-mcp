"""The starter config.toml tells the truth about where reviewer endpoints live."""

from __future__ import annotations

import tomllib

from ddflow.services.adopt import starter_config


def test_template_points_reviewers_at_local_layer():
    text = starter_config()
    assert ".ddflow/local/reviewers.toml" in text
    assert "ddflow config --local" in text


def test_template_no_longer_claims_detect_fills_this_file():
    text = starter_config()
    assert "fills this in" not in text


def test_template_still_parses():
    tomllib.loads(starter_config())
