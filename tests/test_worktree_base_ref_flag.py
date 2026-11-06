"""`[worktree] base_ref` is a branch name, not a CLI flag (B-base-ref-holds-a-flag).

Running a `config` write that put `--local` in the ``base_ref`` knob stored a CLI flag as
a branch name, so every reader of ``worktree.base_ref`` took a flag for a ref (and
`python -m ddflow.services.roborev_branch` reported "no merge-base of '--local' and
HEAD"). The knob's check now refuses any value with a leading dash.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from conftest import write_config

from ddflow.config import Config, InvalidValue

# The ddflow SOURCE tree this test file lives in; its committed `.ddflow/config.toml` is
# part of the tree and is what `roborev_branch` reads.
_SRC = Path(__file__).resolve().parents[1]


def test_base_ref_refuses_a_leading_dash_value():
    with pytest.raises(InvalidValue, match=r"worktree\.base_ref"):
        Config.check({"worktree": {"base_ref": "--local"}}, written={"worktree.base_ref"})


def test_base_ref_accepts_empty_and_branch_names():
    for value in ("", "main", "origin/main", "master"):
        Config.check({"worktree": {"base_ref": value}}, written={"worktree.base_ref"})


def test_base_ref_setting_a_flag_value_is_refused_when_written(repo):
    err, _ = write_config(repo, [("worktree.base_ref", "--local")], check_workflow=False)
    assert err, "setting worktree.base_ref to a flag value was not refused"
    assert "worktree.base_ref" in str(err)


def test_committed_base_ref_is_not_a_flag():
    # B-base-ref-holds-a-flag: the committed value was `--local` (a flag, not a ref), which
    # made roborev_branch report "no merge-base of '--local' and HEAD". A flag value stored
    # here again must fail this test even if the knob check is somehow lost.
    cfg = tomllib.loads((_SRC / ".ddflow" / "config.toml").read_text())
    base_ref = (cfg.get("worktree") or {}).get("base_ref", "")
    assert not str(base_ref).startswith("-"), base_ref
