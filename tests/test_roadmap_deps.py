"""The roadmap decides "dependency met" with the scheduler's rule (B58010c24b3).

`kind_roadmap._met` hard-coded "in review with a branch counts as met" and "unknown means
blocked", while `ddflow next` (`schedule.dep_status`) follows `[flow].stack` and
`[schedule].unknown_dep_policy`. With stacking off the two disagreed: the roadmap put a
task in Next that `next` would not offer.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_export_docs_a import _body, _ts, road_log


def _review_dep(tmp_path: Path, config: str):
    g = road_log()
    g.task("T8", "P2", "needs a task in review", needs=["T9"])
    g.task("T9", "P2", "in review")
    g.add("pr.opened", "T9", {"branch": "feat/x"}, _ts(3))
    q = g.query()
    q.state.items["T9"].branch = "feat/x"
    (tmp_path / ".ddflow").mkdir()
    (tmp_path / ".ddflow" / "config.toml").write_text(config)
    q.repo = tmp_path
    return next(ln for ln in _body("roadmap", q).splitlines() if "`T8`" in ln)


def test_without_stacking_a_dependency_in_review_blocks(tmp_path):
    t8 = _review_dep(tmp_path, "[flow]\nstack = false\n")
    assert "blocked" in t8, t8


def test_with_stacking_a_dependency_in_review_with_a_branch_is_met(tmp_path):
    t8 = _review_dep(tmp_path, "[flow]\nstack = true\n")
    assert "blocked" not in t8, t8


def test_an_unknown_dependency_follows_the_policy(tmp_path):
    q = road_log().query()
    (tmp_path / ".ddflow").mkdir()
    (tmp_path / ".ddflow" / "config.toml").write_text('[schedule]\nunknown_dep_policy = "warn"\n')
    q.repo = tmp_path
    t7 = next(ln for ln in _body("roadmap", q).splitlines() if "`T7`" in ln)
    assert "blocked" not in t7, t7  # `next` ignores the unknown id under "warn"
