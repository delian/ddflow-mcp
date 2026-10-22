"""The rounds line says which budget a round spent (bugs B-fix-round-free-not-visible,
Befff7775ad): a free fix round reads as one ("not counted"), and a combined review says what
it charged to EACH gate instead of one 'rounds used n/cap' for both."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_gate_econ_review as GE
import test_review_fix_round as FR

import ddflow.api.review as api


def test_a_free_fix_round_says_it_is_not_counted(repo, tmp_path):
    FR._setup(repo, tmp_path, "[review]\nmax_rounds = 4\n")
    first = FR._review(repo)
    assert "rounds used 1/4" in first.data["text"] and "not counted" not in first.data["text"]
    FR._commit(repo, "x.py", "x = 2\ny = 2\n")
    out = FR._review(repo)
    line = next(ln for ln in out.data["text"].splitlines() if ln.startswith("rounds used"))
    assert line.startswith("rounds used 1/4") and "fix round: not counted" in line, line


def test_a_combined_review_says_what_it_charged_each_gate(repo, tmp_path):
    GE._setup(repo, tmp_path, "[review]\nmax_rounds = 4\n")
    out = api.review(repo, gate=GE.GATES, item="T1", branch="feat")
    text = out.data["text"]
    assert "combined review" in text, text
    line = next(ln for ln in text.splitlines() if ln.startswith("combined review charged"))
    assert "rubber_duck 1/4" in line and "critic 1/4" in line, line


def test_a_combined_review_that_reached_no_reviewer_charges_nothing(repo, tmp_path):
    GE._setup(repo, tmp_path, "[review]\nmax_rounds = 4\n")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text().replace('gates = ["critic", "rubber_duck"]', 'gates = ["docs"]'))
    out = api.review(repo, gate=GE.GATES, item="T1", branch="feat")
    line = next(
        ln for ln in out.data["text"].splitlines() if ln.startswith("combined review charged")
    )
    assert "no round to any gate" in line and "rubber_duck 0/4" in line, line
