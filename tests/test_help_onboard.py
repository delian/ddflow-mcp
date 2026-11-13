"""The onboarding flow has a help page, and the README row names every stage."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api.onboard import STAGES

ROOT = Path(__file__).resolve().parents[1]


def test_help_onboard_is_a_topic_and_names_every_stage(repo):
    code, out, err = run_cli(repo, "help", "onboard")
    assert code == 0, out + err
    lines = {ln.split()[0] for ln in out.splitlines() if ln.startswith("    ") and ln.split()}
    for stage in STAGES:
        assert stage in lines, stage


def test_help_index_lists_onboard(repo):
    code, out, _ = run_cli(repo, "help")
    assert code == 0
    assert any(line.split()[:1] == ["onboard"] for line in out.splitlines()), out


def test_readme_onboard_row_names_every_stage():
    row = next(
        ln for ln in (ROOT / "README.md").read_text().splitlines() if "onboard an existing" in ln
    )
    for stage in STAGES:
        assert f"`{stage}`" in row or f"onboard {stage}`" in row, stage
