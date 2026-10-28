"""`ddflow status --by-tag KEY`: per-tag-value counts and per-wave blockers (DX-waves-status).

One plan computation answers the whole view: for each distinct tag in the KEY
namespace (``tag == KEY`` or ``tag.startswith(KEY + ":")``) a bucket with the counts
done/running/ready/review/held/blocked/abandoned/open, the blocking reasons and the open
dependencies blocking the most items; items carrying none of the namespace land in
``untagged``. The ready/running/blocked/review/held numbers are read off the ONE
``purpose="view"`` plan `status` already folds (Bdcce70d036), so the per-value view and
the top panel cannot disagree and the log is still folded once (B-gap-log-scale).
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import time
from pathlib import Path

import pytest

from ddflow.infra.log import EventLog
from ddflow.surfaces.cli import main


@pytest.fixture
def project(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    log = EventLog(tmp_path, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    log.append("task.added", "T-w0", {"parent": "P1", "title": "root", "tags": ["wave:0"]})
    log.append(
        "task.added", "T-w1", {"parent": "P1", "title": "next", "tags": ["wave:1"], "needs": ["T-w0"]}
    )
    log.append("task.added", "T-w1done", {"parent": "P1", "title": "done w1", "tags": ["wave:1"]})
    log.append("item.completed", "T-w1done", {"sha": "abc"})
    log.append("task.added", "T-w1run", {"parent": "P1", "title": "running w1", "tags": ["wave:1"]})
    log.append(
        "lease.acquired",
        "T-w1run",
        {"holder": "w", "at": time.time(), "ttl_s": 1800, "kind": "task", "globs": [], "worktree": "/wt/w"},
    )
    log.append("item.started", "T-w1run", {})
    log.append("task.added", "T-w1ab", {"parent": "P1", "title": "dropped w1", "tags": ["wave:1"]})
    log.append("item.abandoned", "T-w1ab", {"reason": "nope"})
    log.append(
        "task.added", "T-w2", {"parent": "P1", "title": "deep", "tags": ["wave:2"], "needs": ["T-w1"]}
    )
    log.append("task.added", "T-other", {"parent": "P1", "title": "untagged"})
    return tmp_path


def _run(repo: Path, *argv: str) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):
        main(["--repo", str(repo), "--agent", "tester", *argv])
    return out.getvalue()


def _json(repo: Path, *argv: str) -> dict:
    return json.loads(_run(repo, "--json", *argv))


def _bucket(body: dict, tag: str) -> dict:
    by = body["by_tag"]
    for v in by["values"]:
        if v["tag"] == tag:
            return v
    raise AssertionError(f"{tag} not in {[v['tag'] for v in by['values']]}")


def test_status_by_tag_groups_counts_per_wave(project):
    body = _json(project, "status", "--by-tag", "wave")
    by = body["by_tag"]
    assert by["key"] == "wave"
    w0 = _bucket(body, "wave:0")
    assert w0["states"]["total"] == 1
    assert w0["states"]["ready"] == 1
    assert w0["states"]["open"] == 1
    w1 = _bucket(body, "wave:1")
    assert w1["states"] == {
        "total": 4,
        "done": 1,
        "running": 1,
        "ready": 0,
        "review": 0,
        "held_by_cap": 0,
        "blocked": 1,
        "abandoned": 1,
        "open": 2,
    }
    untagged = by["untagged"]
    assert untagged["tag"] == "untagged"
    assert untagged["states"]["open"] == 2  # T-other and the still-open phase P1


def test_status_by_tag_names_top_blockers_and_reasons(project):
    body = _json(project, "status", "--by-tag", "wave")
    w2 = _bucket(body, "wave:2")
    assert w2["states"]["blocked"] == 1
    assert w2["blocked_by"] == {"deps": 1}
    assert {"id": "T-w1", "blocks": 1} in w2["top_blockers"]
    assert _bucket(body, "wave:1")["blocked_by"] == {"deps": 1}
    assert _bucket(body, "wave:0")["blocked_by"] == {}


def test_status_text_prints_the_by_tag_section(project):
    text = _run(project, "status", "--by-tag", "wave")
    assert "per wave value" in text
    # The EXACT wave:2 blocker line -- a bare "T-w1" substring would also match the
    # normal panel and the other T-w1* ids, and pass without the section.
    assert "top blockers: T-w1 (1)" in text


def test_status_without_by_tag_has_no_by_tag_key(project):
    body = _json(project, "status")
    assert "by_tag" not in body


def test_bounded_status_cuts_the_by_tag_values(project):
    # Bd6aa9ffde9: the bounded MCP answer stays bounded -- a namespace with thousands of
    # distinct values must not smuggle an uncut list past `_bound` (rubber_duck #1).
    from ddflow.api.reporting.overview import STATUS_LIST_LIMIT, status

    log = EventLog(project, "more")
    for i in range(STATUS_LIST_LIMIT + 3):
        log.append(
            "task.added", f"T-team{i}", {"parent": "P1", "title": f"t{i}", "tags": [f"team:{i}"]}
        )
    out = status(project, agent="tester", full=False, by_tag="team")
    assert len(out.data["by_tag"]["values"]) == STATUS_LIST_LIMIT
    assert out.data["truncated"]["lists"]["by_tag"] == STATUS_LIST_LIMIT + 3
    out_full = status(project, agent="tester", full=True, by_tag="team")
    assert len(out_full.data["by_tag"]["values"]) == STATUS_LIST_LIMIT + 3
