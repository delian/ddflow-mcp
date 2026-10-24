"""B738d7bbeab: a doc that landed inside a MIXED code+doc commit keeps the full task
pipeline until the landing records the item's own paths (`update --landed-paths`) --
then the item's ACTUAL diff is the doc, and the doc pipeline runs.

`update --landed <sha>` records the WHOLE commit as the item's landing, so its changed
paths include code the commit also carried and the docs-only pipeline can never apply,
although the item's own output is only the doc. `--landed-paths <globs>` records the
item's own paths, each verified inside the item's declared globs and inside the range's
changed files, and `changed_paths` reports those -- so `gate status` and `complete`
resolve the doc pipeline, exactly as after `ddflow merge`.
"""

from __future__ import annotations

import subprocess

from conftest import run_cli
from test_na_required_gate import _state

from ddflow.config import Config
from ddflow.services import completion as CO
from ddflow.services import gates as G


def _commit(repo, msg: str) -> str:
    subprocess.run(
        ["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", msg],
        check=True,
        capture_output=True,
        text=True,
    )
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _mixed_landing(repo) -> str:
    """A commit on the base that changes BOTH a doc and code: the doc is what the item
    claims, the code is someone else's output inside the same commit."""
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "decision.md").write_text("decided\n")
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "code.py").write_text("x = 1\n")
    return _commit(repo, "mixed code+doc commit")


def test_a_mixed_landing_keeps_the_task_pipeline_until_the_paths_are_recorded(repo):
    """The bug: --landed alone records the whole commit, code included."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "decision doc", "--globs", "docs/**")
    sha = _mixed_landing(repo)
    code, out, err = run_cli(repo, "update", "T1", "--landed", sha, agent="a1")
    assert code == 0, out + err
    cfg = Config.load(repo)
    st = _state(repo)
    it = st.items["T1"]
    changed = CO.changed_paths(repo, it)
    # The whole commit, which also carries ddflow's own state files from `init`.
    assert "docs/decision.md" in changed and "src/code.py" in changed, changed
    s = G.status(st, cfg, "T1", None, diff=lambda: CO.changed_paths(repo, it))
    assert "unit_tests" in s.pipeline


def test_a_landing_restricted_to_the_items_own_paths_runs_the_doc_pipeline(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "decision doc", "--globs", "docs/**")
    sha = _mixed_landing(repo)
    code, out, err = run_cli(
        repo, "update", "T1", "--landed", sha, "--landed-paths", "docs/**", agent="a1"
    )
    assert code == 0, out + err
    cfg = Config.load(repo)
    st = _state(repo)
    it = st.items["T1"]
    assert it.landed_paths == ["docs/**"]
    changed = CO.changed_paths(repo, it)
    assert changed == ["docs/decision.md"], changed
    s = G.status(st, cfg, "T1", None, diff=lambda: CO.changed_paths(repo, it))
    assert s.pipeline == list(cfg.gates.doc_pipeline), s.pipeline


def test_the_paths_can_be_recorded_after_the_landing(repo):
    """The two decision items already have their landed range: the restriction must be
    recordable alone, verified against the recorded range, not only in the same command."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "decision doc", "--globs", "docs/**")
    sha = _mixed_landing(repo)
    code, out, err = run_cli(repo, "update", "T1", "--landed", sha, agent="a1")
    assert code == 0, out + err
    code, out, err = run_cli(repo, "update", "T1", "--landed-paths", "docs/**", agent="a1")
    assert code == 0, out + err
    st = _state(repo)
    assert CO.changed_paths(repo, st.items["T1"]) == ["docs/decision.md"]


def test_a_wrong_restriction_can_be_corrected_and_cleared(repo):
    """A mis-recorded restriction is not a one-way door (standards/roborev finding): the
    operator can re-record a corrected one and clear it again."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "decision doc", "--globs", "docs/**")
    sha = _mixed_landing(repo)
    code, out, err = run_cli(
        repo, "update", "T1", "--landed", sha, "--landed-paths", "docs/*.md", agent="a1"
    )
    assert code == 0, out + err
    code, out, err = run_cli(repo, "update", "T1", "--landed-paths", "docs/**", agent="a1")
    assert code == 0, out + err
    st = _state(repo)
    assert st.items["T1"].landed_paths == ["docs/**"]
    code, out, err = run_cli(repo, "update", "T1", "--landed-paths", "", agent="a1")
    assert code == 0, out + err
    st = _state(repo)
    assert st.items["T1"].landed_paths == []


def test_a_restriction_outside_the_declared_globs_is_refused(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "decision doc", "--globs", "docs/**")
    sha = _mixed_landing(repo)
    code, out, err = run_cli(
        repo, "update", "T1", "--landed", sha, "--landed-paths", "src/**", agent="a1"
    )
    assert code != 0
    assert "globs" in out + err


def test_a_restriction_without_a_landing_is_refused(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "decision doc", "--globs", "docs/**")
    code, out, err = run_cli(repo, "update", "T1", "--landed-paths", "docs/**", agent="a1")
    assert code != 0
    # NOT the argparse "unrecognized arguments" text: the unfixed code satisfies that too,
    # so the assertion names the actual validation (review finding, gate rubber_duck).
    assert "needs a landing" in out + err


def test_a_restriction_wider_than_the_declared_globs_is_refused(repo):
    """A wider restriction must not be accepted just because its pattern STRING sits inside
    a declared glob: `docs/foo/**` names files `docs/foo/*` does not (review finding)."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "note", "--globs", "docs/foo/*")
    (repo / "docs" / "foo").mkdir(parents=True)
    (repo / "docs" / "foo" / "note.md").write_text("n\n")
    sha = _commit(repo, "landing")
    code, out, err = run_cli(
        repo, "update", "T1", "--landed", sha, "--landed-paths", "docs/foo/**", agent="a1"
    )
    assert code != 0
    assert "escapes the declared globs" in out + err


def test_a_restriction_the_range_does_not_touch_is_refused(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "decision doc", "--globs", "docs/**")
    sha = _mixed_landing(repo)
    code, out, err = run_cli(
        repo,
        "update",
        "T1",
        "--landed",
        sha,
        "--landed-paths",
        "docs/absent.md",
        agent="a1",
    )  # fmt: skip
    assert code != 0
    assert "nothing inside" in out + err
