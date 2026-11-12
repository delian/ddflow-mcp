"""`next --tag TAG` (repeatable) and `--wave N` restrict the offer to tagged items (NV-next-tag-filter)."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import run_cli

from ddflow.api import next_item


def _queue(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "M", "--title", "m")
    run_cli(repo, "phase", "add", "N", "--title", "n")
    run_cli(
        repo,
        "task",
        "add",
        "M.A",
        "--title",
        "a",
        "--phase",
        "M",
        "--globs",
        "a.py",
        "--tags",
        "wave:0,x",
    )
    run_cli(
        repo,
        "task",
        "add",
        "M.B",
        "--title",
        "b",
        "--phase",
        "M",
        "--globs",
        "b.py",
        "--tags",
        "wave:1,x",
    )
    run_cli(
        repo,
        "task",
        "add",
        "N.C",
        "--title",
        "c",
        "--phase",
        "N",
        "--globs",
        "c.py",
        "--tags",
        "wave:0",
    )
    run_cli(repo, "task", "add", "N.D", "--title", "d", "--phase", "N", "--globs", "d.py")


def _ids(repo, *argv):
    code, out, err = run_cli(repo, "--json", "next", *argv)
    assert code in (0, 2), f"exit {code}\n{out}\n{err}"
    d = json.loads(out)
    return d, [r["id"] for r in d["ready"]]


def test_tag_limits_ready_and_blocked(repo):
    _queue(repo)
    d, ready = _ids(repo, "--tag", "x")
    assert sorted(ready) == ["M.A", "M.B"], d
    assert all(b["item"] in {"M.A", "M.B"} for b in d["blocked"]), d["blocked"]


def test_blocked_rows_are_limited_to_the_tag_too(repo):
    _queue(repo)
    run_cli(
        repo, "task", "add", "M.E", "--title", "e", "--phase", "M", "--needs", "M.A", "--tags", "x"
    )
    run_cli(
        repo,
        "task",
        "add",
        "N.F",
        "--title",
        "f",
        "--phase",
        "N",
        "--needs",
        "N.C",
        "--tags",
        "wave:1",
    )
    d, _ = _ids(repo, "--tag", "x")
    blocked = {b["item"] for b in d["blocked"]}
    assert "M.E" in blocked and "N.F" not in blocked, blocked


def test_lane_mode_honours_the_tag(repo, monkeypatch):
    _queue(repo)
    code, out, err = run_cli(repo, "next", "--lane", "l1", "--tag", "nosuchtag")
    assert code == 1, (out, err)
    from ddflow.api.lifecycle import pull

    seen: dict = {}
    real = pull.plan_for_offer

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(pull, "plan_for_offer", spy)
    from ddflow.api import next_lane

    next_lane(Path(repo), lane="l1", tags=("wave:1",))
    assert seen.get("tags") == ("wave:1",), seen


def test_wave_is_shorthand_for_the_wave_tag(repo):
    _queue(repo)
    _, by_wave = _ids(repo, "--wave", "0")
    _, by_tag = _ids(repo, "--tag", "wave:0")
    assert sorted(by_wave) == sorted(by_tag) == ["M.A", "N.C"]


def test_repeated_tags_intersect_and_phase_combines(repo):
    _queue(repo)
    _, ready = _ids(repo, "--tag", "x", "--tag", "wave:1")
    assert ready == ["M.B"]
    _, ready = _ids(repo, "--tag", "wave:0", "--phase", "N")
    assert ready == ["N.C"]


def test_unknown_tag_is_refused_not_empty(repo):
    _queue(repo)
    code, out, err = run_cli(repo, "next", "--tag", "wvae:0")
    assert code == 1, (out, err)
    assert "wvae:0" in out + err and "wave:0" in out + err, (out, err)


def test_api_filter_reported_and_default_unchanged(repo):
    _queue(repo)
    out = next_item(Path(repo), tags=("x",))
    assert out.data["filter"]["tag"] == ["x"], out.data
    base = next_item(Path(repo))
    assert {r["id"] for r in base.data["ready"]} == {"M.A", "M.B", "N.C", "N.D"}
    assert "filter" not in base.data or not base.data["filter"].get("tag")


def test_mcp_schema_has_tag_and_wave():
    from ddflow.surfaces.declared.lifecycle.navigate import COMMANDS

    nxt = next(c for c in COMMANDS if c.path == ("next",))
    names = {p.name: p for p in nxt.params}
    assert names["tag"].repeat and names["wave"].type == "integer"
