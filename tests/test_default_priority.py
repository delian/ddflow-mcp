"""A DX or bug-fix task filed without --priority lands in its band, not behind every feature
at 100 (DX-default-priority-for-dx)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def _priorities(repo: Path) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    for f in (repo / ".ddflow" / "events").rglob("*.jsonl"):
        for line in f.read_text().splitlines():
            e = json.loads(line)
            if e["kind"] == "task.added":
                out[e["subject"]] = e["data"].get("priority")
    return out


def _filed(repo: Path):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "p")
    for args in (
        ("T-plain",),
        ("DX-by-id",),
        ("T-tagged", "--tags", "dx"),
        ("T-bug", "--tags", "bugfix"),
        ("T-explicit", "--tags", "dx", "--priority", "100"),
        ("T-set", "--priority", "30"),
    ):
        code, _o, err = run_cli(
            repo, "task", "add", *args, "--phase", "P1", "--title", "t", "--new"
        )
        assert code == 0, err
    return _priorities(repo)


def test_dx_and_fix_tasks_default_to_their_band(repo: Path):
    got = _filed(repo)
    assert got["T-plain"] is None  # the generic default: nothing written
    assert got["DX-by-id"] == 8
    assert got["T-tagged"] == 8
    assert got["T-bug"] == 10
    assert got["T-explicit"] == 100, "an explicit --priority 100 must not be changed"
    assert got["T-set"] == 30


def test_the_band_is_a_config_knob(repo: Path):
    run_cli(repo, "init")
    assert run_cli(repo, "config", "--set", "schedule.dx_priority", "3")[0] == 0
    run_cli(repo, "phase", "add", "P1", "--title", "p")
    run_cli(repo, "task", "add", "DX-x", "--phase", "P1", "--title", "t", "--new")
    assert _priorities(repo)["DX-x"] == 3


def test_a_bug_fix_task_takes_the_bug_band(repo: Path):
    run_cli(repo, "init")
    code, _o, err = run_cli(repo, "bug", "found", "--summary", "it breaks")
    assert code == 0, err
    fixes = {k: v for k, v in _priorities(repo).items() if k.startswith("fix-")}
    assert fixes and all(v == 10 for v in fixes.values()), fixes


def test_plan_check_warns_about_a_dx_task_left_at_100(repo: Path, tmp_path: Path):
    run_cli(repo, "init")
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "items": [
                    {"id": "DX-a", "title": "a", "priority": 100},
                    {"id": "DX-b", "title": "b"},
                    {"id": "feat", "title": "c", "priority": 100},
                ]
            }
        )
    )
    _code, out, _err = run_cli(repo, "--json", "plan", "check", str(plan))
    warned = [w["id"] for w in json.loads(out)["warnings"] if "generic priority" in w["detail"]]
    assert warned == ["DX-a"]


def test_the_band_is_a_fallback_not_a_clamp_and_dx_wins_over_bug(repo: Path):
    from ddflow.config import Config
    from ddflow.services import priority as PRI

    cfg = Config()
    assert PRI.band(cfg, "x", ["dx", "bugfix"]) == cfg.schedule.dx_priority
    cfg.schedule.dx_priority = 100
    assert PRI.left_generic(cfg, "DX-a", [], 100) == "", "the band is the generic default"
    cfg.schedule.dx_id_prefix = ""
    assert PRI.kind_of(cfg, "DX-a", []) == ""


def test_a_fix_task_keeps_a_priority_its_item_set(repo: Path):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "p")
    for tid, extra in (("T-late", ["--priority", "200"]), ("T-generic", [])):
        code, _o, err = run_cli(
            repo, "task", "add", tid, "--phase", "P1", "--title", "t", "--new", *extra
        )
        assert code == 0, err
        code, _o, err = run_cli(repo, "bug", "found", "--summary", f"bug in {tid}", "--item", tid)
        assert code == 0, err
    fixes = [v for k, v in _priorities(repo).items() if k.startswith("fix-")]
    assert sorted(fixes) == [10, 200], fixes
