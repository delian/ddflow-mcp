"""An older ddflow reading a newer config keeps working.

Several checkouts of one repository run different ddflow versions: a worktree whose
branch predates a knob runs its own, older code against the primary's newer
.ddflow/config.toml. Refusing the unknown key refused every command and every commit
until the branch merged main (bugs Bcfc0d22a09, B9cb7dd1c3b: after forbidden_trailers,
then after local_files, which main had to disable). The key is now recorded and skipped;
`doctor` reports it, and writes still refuse it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config


def test_a_knob_this_code_does_not_know_is_skipped_and_the_rest_applies(tmp_path):
    (tmp_path / ".ddflow").mkdir()
    (tmp_path / ".ddflow" / "config.toml").write_text(
        "[lease]\nttl_s = 77\nfrom_the_future = true\n\n[newsection]\nx = 1\n"
    )
    cfg = Config.load(tmp_path)
    assert cfg.lease.ttl_s == 77, "the knobs this code knows still apply"
    assert cfg.unknown_knobs == ["lease.from_the_future", "[newsection]"]


def test_writing_an_unknown_knob_is_still_refused():
    with pytest.raises(ValueError, match=r"unknown knob 'lease\.from_the_future'"):
        Config.check({"lease": {"from_the_future": True}})


def test_an_unknown_env_knob_source_is_not_made_lenient():
    """Only a FILE is lenient: `check` and `env` keep raising."""
    with pytest.raises(ValueError):
        Config()._apply({"lease": {"nope": 1}}, "env")


def test_every_command_still_runs_and_doctor_names_the_key(repo):
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    text = cfg.read_text()
    assert "\n[enforce]\n" in text, "init writes [enforce]; the key goes into that table"
    cfg.write_text(text.replace("\n[enforce]\n", '\n[enforce]\nfrom_the_future = ["x"]\n', 1))
    code, out, err = run_cli(repo, "status")
    assert code == 0, err
    code, out, _ = run_cli(repo, "doctor")
    assert code == 1
    assert "unknown config key enforce.from_the_future" in out and "merge main" in out
