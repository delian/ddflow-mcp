"""An older ddflow reading a newer checkout's reviewer config warns, not refuses (B6f757e18cf).

Adding `hedge`/`max_concurrency` to this project's reviewers.toml made the installed
(older) ddflow fail every review: "unknown field(s) ['hedge', 'max_concurrency'] in
[[reviewer]] #1". The config.toml forward-compat rule (B9cb7dd1c3b) did not reach the
[[reviewer]] loader. Same rule now: strict in the tree the code came from, where an
unknown key can only be a typo; lenient elsewhere, with a warning naming the key.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.infra import tomlcfg
from ddflow.services.review import Reviewer, load_reviewers

BLOCK = (
    '[[reviewer]]\nname = "r"\nbase_url = "http://x/v1"\nmodel = "m"\nknob_from_the_future = 3\n'
)


def test_a_newer_reviewer_knob_is_skipped_with_a_warning(tmp_path, capsys):
    (tmp_path / ".ddflow").mkdir()
    (tmp_path / ".ddflow" / "reviewers.toml").write_text(BLOCK)
    (rev,) = load_reviewers(tmp_path)  # tmp_path is not the tree this code runs from
    assert rev.name == "r" and rev.model == "m"
    err = capsys.readouterr().err
    assert "knob_from_the_future" in err and "newer than this code" in err


def test_in_the_code_tree_an_unknown_field_is_still_an_error(tmp_path):
    path = tmp_path / "reviewers.toml"
    path.write_text(BLOCK)
    with pytest.raises(ValueError, match="knob_from_the_future"):
        tomlcfg.overlay_array([path], "reviewer", Reviewer, key="name")
