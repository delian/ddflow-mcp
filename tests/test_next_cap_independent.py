"""`next` shows ready-but-cap-held items as their own section (NV-next-cap-independent).

With the parallelism cap full every free task used to land in `blocked` as 'held by the cap'
and `next` said 'Nothing actionable': a caller could not see what was otherwise unblocked.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import run_cli


def _full_cap(repo, monkeypatch):
    run_cli(repo, "init")
    (Path(repo) / ".ddflow" / "config.toml").write_text(
        '[schedule]\nmax_parallel_tasks = 1\nmax_parallel_min = 1\nparallel = "fixed"\n'
    )
    run_cli(repo, "phase", "add", "M", "--title", "m")
    for tid, g in (("M.R", "r.py"), ("M.A", "a.py"), ("M.B", "b.py")):
        run_cli(repo, "task", "add", tid, "--title", tid, "--phase", "M", "--globs", g)
    run_cli(repo, "task", "add", "M.D", "--title", "d", "--phase", "M", "--needs", "M.A")
    run_cli(repo, "claim", "M.R", "--no-worktree", agent="lane1")


def _next(repo, *argv):
    code, out, err = run_cli(repo, "--json", "next", *argv, agent="lane2")
    assert code in (0, 2), (code, out, err)
    return code, json.loads(out)


def test_cap_held_items_are_a_section_with_their_globs(repo, monkeypatch):
    _full_cap(repo, monkeypatch)
    code, d = _next(repo)
    assert code == 2 and d["ready"] == [], d
    held = {r["id"]: r for r in d["held_by_cap"]}
    assert set(held) == {"M.A", "M.B"}, held
    assert held["M.A"]["globs"] == ["a.py"], held["M.A"]
    assert "M.A" in {b["item"] for b in d["blocked"]}, "blocked keeps its rows (additive)"


def test_text_names_the_held_items_not_just_nothing_actionable(repo, monkeypatch):
    _full_cap(repo, monkeypatch)
    code, out, _ = run_cli(repo, "next", agent="lane2")
    assert code == 2, out
    assert "ready but held by the parallelism cap" in out.lower() and "M.A" in out and "a.py" in out, out
    assert "named `ddflow claim`" in out, out


def test_ignore_cap_offers_them_as_ready(repo, monkeypatch):
    _full_cap(repo, monkeypatch)
    code, d = _next(repo, "--ignore-cap")
    assert code == 0, d
    assert {r["id"] for r in d["ready"]} == {"M.A", "M.B"}, d["ready"]
    assert d["held_by_cap"] == [], d


def test_merged_uncompleted_items_have_their_own_section(repo, monkeypatch):
    _full_cap(repo, monkeypatch)
    from ddflow.api._base import _load

    log, _cfg, _st = _load(Path(repo), "lane2")
    log.append("worktree.merged", "M.B", {"sha": "0123456789abcdef0123"})
    _, d = _next(repo)
    assert d["needs_complete"] == ["M.B"], d
    assert "M.B" not in {r["id"] for r in d["held_by_cap"]}, d
    _, out, _ = run_cli(repo, "next", agent="lane2")
    assert "ddflow complete" in out and "M.B" in out, out


def test_dependency_blocked_rows_are_a_count_unless_asked(repo, monkeypatch):
    _full_cap(repo, monkeypatch)
    _, out, _ = run_cli(repo, "next", agent="lane2")
    assert "M.D" not in out and "blocked on dependencies" in out, out
    _, out, _ = run_cli(repo, "next", "--blocked", agent="lane2")
    assert "M.D" in out, out
