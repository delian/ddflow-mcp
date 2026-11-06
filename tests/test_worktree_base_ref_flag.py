"""`[worktree] base_ref` is a branch name, not a CLI flag (B-base-ref-holds-a-flag).

Running a `config` write that put `--local` in the ``base_ref`` knob stored a CLI flag as
a branch name, so every reader of ``worktree.base_ref`` took a flag for a ref (and
`python -m ddflow.services.roborev_branch` reported "no merge-base of '--local' and
HEAD"). The knob's check now refuses any value with a leading dash.
"""

from __future__ import annotations

import pytest

from conftest import write_config

from ddflow.config import Config, InvalidValue


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
