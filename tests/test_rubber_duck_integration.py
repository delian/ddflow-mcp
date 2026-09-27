"""Defects an adversarial rubber-duck pass (a different model) found in the integration
features -- import fidelity, memory, hooks, jobs, resources, external dependencies.

Each arrived with a reproduced probe; each is the regression test here. Two further
findings are deliberate behaviour, pinned at the bottom so the decision is on record:
an archive file's IN PROGRESS section stays held (the source picker drives a legacy
section only when named), and a sub-heading under a DEFERRED heading inherits it unless
it carries its own STATUS line.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api.operations import _calendar_due
from ddflow.config import Config
from ddflow.core.model import State, fold
from ddflow.core.progress import epoch
from ddflow.infra.log import EventLog
from ddflow.services import importer as IM

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _tasks(repo: Path, **kw) -> dict[str, IM.Found]:
    plan = IM.plan_import(repo, None, max_tasks=10**6, **kw)
    return {f.ident: f for f in plan.found if f.kind == "task"}


def test_an_unclosed_fence_does_not_swallow_the_rest_of_the_file(repo):
    _write(
        repo,
        "docs/todo.md",
        "## Phase A\n\n- [ ] **A.1** — one\n\n## Phase B\n\n```\n- [ ] **B.1** — two\n",
    )
    assert {"A.1", "B.1"} <= set(_tasks(repo)), "a stray fence hid everything after it"


def test_a_lesson_one_heading_level_off_is_not_dropped(repo):
    _write(
        repo,
        "docs/lessons.md",
        "# L\n\n## 2026-01-01 — day\n\n### L100. a\nx\n\n### L101. b\ny\n\n### L102. c\nz\n\n"
        "## L200. typo'd one level up\nw\n",
    )
    found, _ = IM.scan_lessons(repo)
    assert {f.ident for f in found} == {"L100", "L101", "L102", "L200"}


def test_a_summary_bullet_is_not_attached_to_a_lesson_whose_id_is_ambiguous(repo):
    _write(repo, "LESSONS.md", "# L\n\n## x\n\n### L1. GIL rule\nnew\n")
    _write(
        repo,
        "docs/retrospectives/old.md",
        "# Old\n\n## y\n\n### L1. an unrelated old lesson\nold\n",
    )
    _write(
        repo,
        "docs/lessons-summary.md",
        "# S\n\n- **GIL contention.** Threads share one GIL. [L1]\n",
    )
    plan = IM.plan_import(repo, None)
    lessons = [f for f in plan.found if f.kind == "lesson"]
    wrong = [f for f in lessons if "unrelated" in f.title and f.extra.get("summary")]
    assert not wrong, "the bullet landed on the wrong L1"
    assert any("summary" in f.extra.get("tags", []) and "GIL" in f.title for f in lessons), (
        "an ambiguous bullet must survive as a consolidated lesson, not vanish"
    )


def test_a_closed_status_that_also_says_in_progress_is_held_not_dropped(repo):
    _write(
        repo,
        "docs/todo.md",
        "## S\n**STATUS**: CLOSED — reopened in Phase 12, IN PROGRESS now\n\n- [ ] **ST.1** — work\n",
    )
    t = _tasks(repo)
    assert t["ST.1"].extra["disposition"] == "hold"
    assert "ask the operator" in t["ST.1"].extra["disposition_why"]


def test_a_colon_is_refused_in_a_local_id_and_a_local_item_wins_over_external(repo):
    run_cli(repo, "init")
    code, _o, err = run_cli(repo, "phase", "add", "foo:bar")
    assert code == FAIL and "colon" in err + _o
    # An item that already has one (an older log) is still local, not external.
    EventLog(repo, "old").append("task.added", "foo:bar", {"title": "legacy"})
    EventLog(repo, "old").append("item.completed", "foo:bar", {"kind": "task"})
    run_cli(repo, "task", "add", "T2", "--needs", "foo:bar", "--globs", "a.py")
    _c, out, _e = run_cli(repo, "--json", "next")
    assert "T2" in {r["id"] for r in json.loads(out)["ready"]}


def test_a_stale_MERGE_HEAD_does_not_exempt_a_commit_from_the_trailer(repo):
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text("[enforce]\nrequire_item_trailer = true\n")
    run_cli(repo, "hooks", "install")
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    (repo / ".git" / "MERGE_HEAD").write_text(head + "\n")
    (repo / "x.txt").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "x.txt"], check=True)
    r = subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "no trailer"],
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0, "a stale MERGE_HEAD exempted an ordinary commit"


def test_a_settings_group_whose_hooks_is_not_a_list_is_refused_not_a_traceback(repo):
    run_cli(repo, "init")
    (repo / ".claude").mkdir()
    bad = '{"hooks": {"SessionStart": [{"matcher": "startup", "hooks": 5}]}}'
    (repo / ".claude" / "settings.json").write_text(bad)
    for argv in (
        ("hooks", "status"),
        ("hooks", "install", "--claude"),
        ("hooks", "uninstall", "--claude"),
    ):
        _code, _out, err = run_cli(repo, *argv)
        assert "Traceback" not in err, argv
    assert (repo / ".claude" / "settings.json").read_text() == bad


def test_the_newest_calendar_run_counts_whatever_order_it_folded_in():
    cfg = Config()
    cfg.cadence.every_days = ["bug_hunt=7"]
    st = State()
    st.cadences["bug_hunt"] = [
        {"at": "2026-09-27T00:00:00Z"},
        {"at": "2026-08-01T00:00:00Z"},
    ]
    assert _calendar_due(st, cfg, now=epoch("2026-09-28T00:00:00Z")) == []


# -- deliberate, on record ----------------------------------------------------------------


def test_an_archive_files_in_progress_section_stays_held_until_named(repo):
    """The source picker drives a legacy section of its archive only when it is NAMED,
    whatever its STATUS says; 800 legacy boxes in sections still marked IN PROGRESS
    would otherwise flood the queue. `ddflow unblock <phase>` is the naming."""
    _write(repo, "ARCHIVE.md", "## Session 42\n**STATUS**: IN PROGRESS\n\n- [ ] **AR.1** — old\n")
    t = _tasks(repo, sources={"todo": ("ARCHIVE.md",)}, archive=("ARCHIVE.md",))
    assert t["AR.1"].extra["disposition"] == "hold"


def test_a_sub_heading_under_a_deferred_heading_inherits_it_unless_it_has_a_status(repo):
    _write(
        repo,
        "docs/todo.md",
        "## DEFERRED work\n\n### Reopened: urgent\n\n- [ ] **U.1** — held\n\n"
        "### Actually live\n**STATUS**: IN PROGRESS\n\n- [ ] **U.2** — live\n",
    )
    t = _tasks(repo)
    assert t["U.1"].extra["disposition"] == "hold", "held work is visible and releasable"
    assert t["U.2"].extra["disposition"] == "", "an explicit STATUS overrides the parent"


def test_fold_is_not_needed_for_these_probes_to_be_meaningful(repo):
    """Guard: the external-vs-local test relies on the fold honouring a pre-existing
    colon id; if this fails, that test's second half proves nothing."""
    run_cli(repo, "init")
    EventLog(repo, "old").append("task.added", "a:b", {"title": "x"})
    assert "a:b" in fold(EventLog(repo).read_all(), strict=False).items


def test_a_same_depth_heading_without_an_id_ends_the_lesson_above_it(repo):
    _write(repo, "docs/lessons.md", "# L\n\n### L1. rule\nbody\n\n### See also\nunrelated links\n")
    found, _ = IM.scan_lessons(repo)
    assert "unrelated links" not in found[0].body


def test_a_fence_with_an_info_string_opens_a_block_and_never_closes_one(repo):
    _write(
        repo,
        "docs/todo.md",
        "## P\n\n```\nexample:\n```py\n- [ ] **FAKE.1** — still inside the first fence\n```\n"
        "- [ ] **REAL.1** — work\n",
    )
    assert set(_tasks(repo)) == {"REAL.1"}


# -- cross-family critic on 9d72c5b ------------------------------------------------------


def test_a_non_bold_item_is_not_closed_by_a_word_in_its_title(repo):
    assert IM._disposition("Handle SKIPPED batches in the dataloader") == ("", "")
    assert IM._disposition("Kubernetes backend. Out of scope for v1.")[0] == "closed"
    assert IM._disposition("DECLINED: the old approach")[0] == "closed"
    assert IM._disposition("tidy up — DEFERRED until Q3")[0] == "hold"
    assert IM._disposition("Example configs *(Deferred to the 2B run")[0] == "hold"


def test_a_level_one_heading_ends_the_lesson_above_it(repo):
    _write(repo, "docs/lessons.md", "## L1. rule\nbody\n\n# Appendix\nunrelated\n")
    found, _ = IM.scan_lessons(repo)
    assert "unrelated" not in found[0].body


# -- roborev 835 (whole branch) -----------------------------------------------------------


def test_a_marker_inside_a_colon_phrase_is_not_a_lead(repo):
    assert IM._disposition("Retry REFUTED requests: add backoff") == ("", "")
    assert IM._disposition("Handle DECLINED offers: show a message") == ("", "")
    assert IM._disposition("DECLINED: the old approach")[0] == "closed"


def test_an_external_need_in_a_plan_is_not_called_unresolvable(repo):
    _write(repo, "docs/todo.md", "## S\n\n- [ ] **S.1** — gen\n  **Needs:** trainer:132.D\n")
    plan = IM.plan_import(repo, None)
    assert not any("did not find" in n for n in plan.notes), plan.notes


def test_a_malformed_cadence_is_said_at_session_start(repo):
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text('[cadence]\nevery_days = ["bug_hunt"]\n')
    _c, out, _e = run_cli(repo, "hooks", "session-start")
    assert "cadence check failed" in out
