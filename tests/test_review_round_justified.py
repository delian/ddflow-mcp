"""A review round must be justified (DX-review-round-justified-check, decision D-review-rounds-4).

A plain `ddflow review <id> --gate G` after a recorded round runs a reviewer only when the item's
reviewed paths changed since that round or a finding of it is still untriaged. Otherwise it says
'nothing new to review', exits 2 and counts no round. `--full`, `--force` and
`[review].require_new_work = false` still run one.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_review_triage import _state
from test_review_triage_scoped_tree import _git, _round, _tri

import ddflow.api.review as api

NOTHING = 2


def _runs(repo: Path) -> int:
    return len(_state(repo).runs_of("critic"))


def _plain(repo: Path):
    lines: list[str] = []
    out = api.review(repo, gate="critic", item="T1", on_progress=lines.append)
    return out, "\n".join(lines) + "\n" + str(out.data.get("text", ""))


def _triaged(repo: Path, tmp_path: Path) -> Path:
    wt = _round(repo, tmp_path)
    _tri(repo, 1)
    _tri(repo, 2)
    return wt


def test_unchanged_tree_with_all_findings_triaged_runs_no_reviewer(repo, tmp_path):
    _triaged(repo, tmp_path)
    before, used = _runs(repo), api._rounds_used(_state(repo), "critic")
    out, text = _plain(repo)
    assert out.exit == NOTHING, text
    assert "nothing new to review since round 1" in text
    assert "reviewed paths unchanged, all findings triaged" in text
    assert _runs(repo) == before and api._rounds_used(_state(repo), "critic") == used


def test_a_change_inside_the_items_paths_allows_a_round(repo, tmp_path):
    wt = _triaged(repo, tmp_path)
    (wt / "c.py").write_text("x = 2  # edited after the review\n")
    _git(wt, "add", "-A")
    before = _runs(repo)
    out, text = _plain(repo)
    assert out.exit == 0, text
    assert _runs(repo) == before + 1
    assert api._rounds_used(_state(repo), "critic") == 2
    assert "rounds used 2/" in text and "justified" in text and "c.py" in text


def test_a_main_merge_outside_the_items_paths_is_not_new_work(repo, tmp_path):
    wt = _triaged(repo, tmp_path)
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "work")
    (repo / "other.txt").write_text("main moved on\n")
    _git(repo, "add", "other.txt")
    _git(repo, "commit", "-qm", "main work")
    _git(wt, "merge", "-q", "--no-edit", "main")
    before, used = _runs(repo), api._rounds_used(_state(repo), "critic")
    out, text = _plain(repo)
    assert out.exit == NOTHING, text
    assert _runs(repo) == before and api._rounds_used(_state(repo), "critic") == used


def test_an_untriaged_finding_allows_a_re_review(repo, tmp_path):
    _round(repo, tmp_path)
    _tri(repo, 1)  # finding 2 is still open
    before = _runs(repo)
    out, text = _plain(repo)
    assert out.exit == 0, text
    assert _runs(repo) == before + 1
    assert api._rounds_used(_state(repo), "critic") == 2
    assert "untriaged" in text


def test_full_still_runs_a_round(repo, tmp_path):
    _triaged(repo, tmp_path)
    before = _runs(repo)
    assert api.review(repo, gate="critic", item="T1", full=True).exit == 0
    assert _runs(repo) == before + 1
    assert api._rounds_used(_state(repo), "critic") == 2


def test_force_with_a_reason_still_runs_a_round(repo, tmp_path):
    _triaged(repo, tmp_path)
    before = _runs(repo)
    out = api.review(repo, gate="critic", item="T1", force=True, reason="operator asked")
    assert out.exit == 0 and _runs(repo) == before + 1


def test_the_knob_off_runs_a_plain_review_on_a_settled_tree(repo, tmp_path):
    _triaged(repo, tmp_path)
    assert _plain(repo)[0].exit == NOTHING  # on: nothing new
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text().replace("[review]\n", "[review]\nrequire_new_work = false\n"))
    before = _runs(repo)
    out, text = _plain(repo)
    assert out.exit == 0, text
    assert _runs(repo) == before + 1
    assert api._rounds_used(_state(repo), "critic") == 2


def test_the_report_line_names_rounds_used_and_the_cap(repo, tmp_path):
    _round(repo, tmp_path)
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text().replace("max_rounds = 0", "max_rounds = 4"))
    _tri(repo, 1)
    before = _runs(repo)
    out, text = _plain(repo)
    assert out.exit == 0 and _runs(repo) == before + 1, text
    assert api._rounds_used(_state(repo), "critic") == 2
    assert "rounds used 2/4, justified" in text


def test_a_round_the_budget_refuses_is_not_reported_as_used(repo, tmp_path):
    _triaged(repo, tmp_path)
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(
        cfg.read_text()
        .replace("max_rounds = 0", "max_rounds = 1")
        .replace("[review]\n", "[review]\nrequire_new_work = false\n")
    )
    out, text = _plain(repo)
    assert out.exit == 3, text
    assert "rounds used" not in text
