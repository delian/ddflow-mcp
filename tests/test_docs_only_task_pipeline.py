"""A task whose ACTUAL change is documents only runs the doc pipeline (bug B-research-task-cannot-complete-docs-only).

A research or documentation task made through the MCP (`task_add` has no kind) is a `task`, so it
ran the code pipeline: `unit_tests` is required, a skip does not satisfy it, and `complete`
demanded a cross-family reviewer for work that changed no code. The ACTUAL diff decides, never
the declared globs (B-na-required-gate).
"""

from __future__ import annotations

from conftest import run_cli
from test_na_required_gate import _state, _worktree_with_change

from ddflow.config import Config
from ddflow.core.model import Item
from ddflow.services import completion as CO
from ddflow.services import gates as G
from ddflow.services.docsonly import diff_is_docs_only


def _task(*globs: str, **kw) -> Item:
    return Item(id="X", kind="task", title="t", globs=list(globs), **kw)


def test_a_diff_of_documents_only_is_docs_only():
    cfg = Config()
    assert diff_is_docs_only(_task("src/**"), cfg, ["docs/a.md", "README.md", "docs/x/y.md"])


def test_one_code_path_keeps_the_task_pipeline():
    cfg = Config()
    assert not diff_is_docs_only(_task("docs/**"), cfg, ["docs/a.md", "ddflow/api/review.py"])


def test_no_readable_or_empty_diff_is_never_docs_only():
    cfg = Config()
    assert not diff_is_docs_only(_task("docs/a.md"), cfg, None)
    assert not diff_is_docs_only(_task("docs/a.md"), cfg, [])


def test_the_knob_turns_it_off():
    cfg = Config()
    cfg.gates.doc_globs = []
    assert not diff_is_docs_only(_task("docs/x.md"), cfg, ["docs/x.md"])


def test_a_promotion_and_a_phase_are_never_docs_only():
    cfg = Config()
    assert not diff_is_docs_only(_task("docs/x.md", promote_to="prod"), cfg, ["docs/x.md"])
    phase = Item(id="P", kind="phase", title="p", globs=["docs/x.md"])
    assert not diff_is_docs_only(phase, cfg, ["docs/x.md"])


def test_a_docs_only_change_needs_no_independent_reviewer():
    cfg = Config()
    assert not CO.needs_independent_reviewer(_task("src/**"), cfg, ["docs/x.md"])
    assert CO.needs_independent_reviewer(_task("docs/x.md"), cfg, ["ddflow/x.py"])
    assert CO.needs_independent_reviewer(
        _task("docs/x.md"), cfg, None
    )  # declared globs never decide


def test_declared_doc_globs_alone_keep_the_task_pipeline(repo):
    """Globs are agent-editable: a task declaring docs but changing code keeps `unit_tests`."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "docs/x.md")
    _worktree_with_change(repo, "src/code.py")
    cfg = Config.load(repo)
    st = _state(repo)
    s = G.status(st, cfg, "T1", None, diff=lambda: CO.changed_paths(repo, st.items["T1"]))
    assert "unit_tests" in s.pipeline


def test_a_task_whose_diff_is_documents_runs_the_doc_pipeline(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "src/**")
    _worktree_with_change(repo, "docs/note.md")
    cfg = Config.load(repo)
    st = _state(repo)
    s = G.status(st, cfg, "T1", None, diff=lambda: CO.changed_paths(repo, st.items["T1"]))
    assert s.pipeline == list(cfg.gates.doc_pipeline), s.pipeline
    assert G.status(st, cfg, "T1", None).pipeline == list(cfg.gates.task_pipeline)


def test_complete_accepts_a_docs_only_change_with_the_doc_gates_alone(repo):
    """End to end through `complete`: the doc pipeline's gates, no unit_tests, no reviewer."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "write a note", "--globs", "docs/**")
    _worktree_with_change(repo, "docs/note.md")
    for gate in ("rules", "implement", "standards", "dedupe", "merge"):
        code, out, err = run_cli(
            repo, "gate", "record", "T1", gate, "--outcome", "passed", "--evidence", "ran it",
            agent="a1",
        )  # fmt: skip
        assert code == 0, (gate, out, err)
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-sonnet-5-5", agent="a1")
    assert code == 0, out + err


def test_complete_still_demands_unit_tests_for_a_code_change(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "docs/**")
    _worktree_with_change(repo, "src/code.py")
    for gate in ("rules", "implement", "standards", "dedupe", "merge"):
        run_cli(
            repo, "gate", "record", "T1", gate, "--outcome", "passed", "--evidence", "x", agent="a1"
        )
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-sonnet-5-5", agent="a1")
    assert code != 0 and "unit_tests" in out + err, out + err


def test_a_research_item_with_a_docs_diff_runs_the_doc_pipeline_and_completes(repo):
    """Bug B75d2b12ff1: a `research`-kind item whose diff is a note kept `unit_tests` required,
    so `complete` refused it even though the gate was skipped with a reason."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "r", "--kind", "research", "--globs", "docs/**")
    _worktree_with_change(repo, "docs/note.md")
    cfg = Config.load(repo)
    st = _state(repo)
    s = G.status(st, cfg, "T1", None, diff=lambda: CO.changed_paths(repo, st.items["T1"]))
    assert s.pipeline == list(cfg.gates.doc_pipeline), s.pipeline
    for gate in ("rules", "implement", "standards", "dedupe", "merge"):
        code, out, err = run_cli(
            repo, "gate", "record", "T1", gate, "--outcome", "passed", "--evidence", "ran it",
            agent="a1",
        )  # fmt: skip
        assert code == 0, (gate, out, err)
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-sonnet-5-5", agent="a1")
    assert code == 0, out + err


def test_a_research_item_that_changed_code_keeps_the_task_pipeline(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "r", "--kind", "research", "--globs", "docs/**")
    _worktree_with_change(repo, "src/code.py")
    cfg = Config.load(repo)
    st = _state(repo)
    s = G.status(st, cfg, "T1", None, diff=lambda: CO.changed_paths(repo, st.items["T1"]))
    assert "unit_tests" in s.pipeline


def test_a_project_written_research_pipeline_is_respected():
    """`[gates].kind_pipelines.research` is the project's own choice; the diff does not override it."""
    cfg = Config()
    cfg.gates.kind_pipelines = {"research": ["implement", "unit_tests", "merge"]}
    it = Item(id="X", kind="research", title="t", globs=["docs/x.md"])
    assert not diff_is_docs_only(it, cfg, ["docs/x.md"])


# --- B9eb92adafe / B4da2fc502b: work landed outside `ddflow merge` -------------------------


def _git(repo, *a):
    import subprocess

    return subprocess.run(
        ["git", "-C", str(repo), *a], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo, path: str, msg: str) -> str:
    f = repo / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(msg + "\n")
    _git(repo, "add", path)
    _git(repo, "commit", "-qm", msg)
    return _git(repo, "rev-parse", "HEAD")


def _unmerged_task(repo, *globs: str) -> None:
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", ",".join(globs))


def _pipeline_of(repo):
    cfg = Config.load(repo)
    st = _state(repo)
    return cfg, G.status(st, cfg, "T1", None, diff=lambda: CO.changed_paths(repo, st.items["T1"]))


def test_a_plain_git_merge_cited_by_the_item_gives_it_a_landed_range(repo):
    """B9eb92adafe: no landed range and no branch left `changed_paths` None, so a doc-only
    item whose work was merged by plain git kept the whole task pipeline."""
    _unmerged_task(repo, "docs/note.md")
    _commit(repo, "docs/note.md", "merge T1: the note")
    cfg, s = _pipeline_of(repo)
    assert s.pipeline == list(cfg.gates.doc_pipeline), s.pipeline


def test_a_cited_commit_that_touched_code_keeps_the_task_pipeline(repo):
    _unmerged_task(repo, "docs/note.md")
    f = repo / "docs" / "note.md"
    f.parent.mkdir(parents=True)
    f.write_text("n\n")
    (repo / "src").mkdir()
    (repo / "src" / "c.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "merge T1: mixed")
    _cfg, s = _pipeline_of(repo)
    assert "unit_tests" in s.pipeline


def test_a_cited_commit_outside_the_declared_globs_is_not_evidence(repo):
    """Fail closed: the citation must touch what the item declared."""
    _unmerged_task(repo, "docs/other.md")
    _commit(repo, "docs/note.md", "merge T1: unrelated")
    _cfg, s = _pipeline_of(repo)
    assert "unit_tests" in s.pipeline


def test_an_uncited_item_stays_on_the_task_pipeline(repo):
    _unmerged_task(repo, "docs/note.md")
    _commit(repo, "docs/note.md", "just a doc")
    _cfg, s = _pipeline_of(repo)
    assert "unit_tests" in s.pipeline


def test_update_landed_records_a_verified_range(repo):
    _unmerged_task(repo, "docs/note.md")
    sha = _commit(repo, "docs/note.md", "some doc commit")
    code, out, err = run_cli(repo, "update", "T1", "--landed", sha)
    assert code == 0, out + err
    it = _state(repo).items["T1"]
    assert it.landed_after == sha and it.landed_before == _git(repo, "rev-parse", sha + "^")
    cfg, s = _pipeline_of(repo)
    assert s.pipeline == list(cfg.gates.doc_pipeline)


def test_update_landed_refuses_what_it_cannot_verify(repo):
    _unmerged_task(repo, "docs/note.md")
    other = _commit(repo, "docs/else.md", "touches nothing declared")
    code, out, err = run_cli(repo, "update", "T1", "--landed", other)
    assert code != 0 and "declared globs" in out + err, out + err
    code, out, err = run_cli(repo, "update", "T1", "--landed", "deadbeef")
    assert code != 0, out + err
    _git(repo, "checkout", "-q", "-b", "side")
    off = _commit(repo, "docs/note.md", "not on main")
    _git(repo, "checkout", "-q", "-")
    code, out, err = run_cli(repo, "update", "T1", "--landed", off)
    assert code != 0 and "ancestor" in out + err, out + err
    assert _state(repo).items["T1"].landed_after == ""


def test_diffless_callers_share_the_pipeline_from_the_diff(repo):
    """B4da2fc502b: reports, the merge queue and test scope asked `pipeline_for` with no diff."""
    from ddflow.services.pipeline_diff import effective_pipeline

    _unmerged_task(repo, "docs/note.md")
    _commit(repo, "docs/note.md", "merge T1: the note")
    cfg = Config.load(repo)
    it = _state(repo).items["T1"]
    assert G.pipeline_for(it, cfg) == list(cfg.gates.task_pipeline)
    assert effective_pipeline(repo, cfg, it) == list(cfg.gates.doc_pipeline)


def test_a_cited_commit_on_a_side_branch_merged_by_plain_git_counts(repo):
    """Roborev/rubber_duck: `--first-parent` hid the commits a plain `git merge` brought in."""
    _unmerged_task(repo, "docs/note.md")
    _git(repo, "checkout", "-q", "-b", "feature")
    _commit(repo, "docs/note.md", "T1: add the note")
    _git(repo, "checkout", "-q", "-")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge branch 'feature'", "feature")
    cfg, s = _pipeline_of(repo)
    assert s.pipeline == list(cfg.gates.doc_pipeline), s.pipeline


def test_an_empty_worktree_diff_falls_back_to_the_cited_commits(repo):
    """A claimed worktree whose branch was merged by plain git diffs to nothing, not 'unknown'."""
    _unmerged_task(repo, "docs/note.md")
    code, out, err = run_cli(repo, "claim", "T1", agent="a1")
    assert code == 0, out + err
    _commit(repo, "docs/note.md", "merge T1: the note")
    cfg, s = _pipeline_of(repo)
    assert s.pipeline == list(cfg.gates.doc_pipeline), s.pipeline


def test_a_cited_commit_outside_the_globs_still_counts_toward_the_change(repo):
    """rubber_duck: a split landing (docs commit + code commit, both citing the item) is not docs-only."""
    _unmerged_task(repo, "docs/note.md")
    _commit(repo, "docs/note.md", "T1: the note")
    _commit(repo, "src/code.py", "T1: the code")
    _cfg, s = _pipeline_of(repo)
    assert "unit_tests" in s.pipeline


def test_a_cited_root_commit_is_read_not_dropped(repo):
    """rubber_duck: a root commit has no parent, and used to vanish from the citations."""
    from ddflow.services import landed as LD

    _unmerged_task(repo, "docs/note.md")
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD").splitlines()[-1]
    main = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _git(repo, "checkout", "-q", "--orphan", "orph")
    _git(repo, "rm", "-rfq", ".")
    _commit(repo, "src/root.py", "T1: orphan root")
    _git(repo, "checkout", "-q", main)
    _git(repo, "merge", "-q", "--allow-unrelated-histories", "--no-ff", "-m", "join", "orph")
    _commit(repo, "docs/note.md", "T1: the note")
    it = _state(repo).items["T1"]
    assert root and LD.cited_paths(repo, it, "HEAD") is not None
    assert "src/root.py" in LD.cited_paths(repo, it, "HEAD")


def test_a_research_item_with_a_cited_docs_commit_runs_the_doc_pipeline(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "R1", "--title", "r", "--kind", "research", "--globs", "docs/**")
    _commit(repo, "docs/note.md", "merge R1: the note")
    cfg = Config.load(repo)
    st = _state(repo)
    s = G.status(st, cfg, "R1", None, diff=lambda: CO.changed_paths(repo, st.items["R1"]))
    assert s.pipeline == list(cfg.gates.doc_pipeline), s.pipeline


def test_update_landed_with_new_globs_checks_the_new_globs(repo):
    """critic: `--globs docs/** --landed c` is judged against the globs the edit sets."""
    _unmerged_task(repo, "src/**")
    sha = _commit(repo, "docs/note.md", "some doc commit")
    code, out, err = run_cli(repo, "update", "T1", "--landed", sha)
    assert code != 0, out + err
    code, out, err = run_cli(repo, "update", "T1", "--globs", "docs/**", "--landed", sha)
    assert code == 0, out + err
    assert _state(repo).items["T1"].landed_after == sha


def test_update_landed_accepts_a_root_commit_and_only_a_root_commit(repo):
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD").splitlines()[-1]
    held = _git(repo, "show", "--name-only", "--format=", root).splitlines()[0]
    _unmerged_task(repo, held)
    code, out, err = run_cli(repo, "update", "T1", "--landed", root)
    assert code == 0, out + err
    it = _state(repo).items["T1"]
    from ddflow.services import landed as LD

    assert it.landed_after == root and it.landed_before == LD.empty_tree(repo)
    # the empty tree starts only a root commit's range
    sha = _commit(repo, held, "touch it again")
    run_cli(repo, "task", "add", "T2", "--title", "t", "--globs", held)
    code, out, err = run_cli(repo, "update", "T2", "--landed", f"{LD.empty_tree(repo)}..{sha}")
    assert code != 0 and "root commit" in out + err, out + err


def test_an_unreadable_claimed_worktree_never_lets_citations_stand_in(repo):
    from ddflow.core.model import Item
    from ddflow.services.completion import _unlanded_paths

    _unmerged_task(repo, "docs/note.md")
    it = Item(id="T1", kind="task", title="t", globs=["docs/note.md"], worktree="gone")
    assert _unlanded_paths(repo, it, "HEAD") == (None, False)
    it = Item(id="T1", kind="task", title="t", globs=["docs/note.md"], branch="typo")
    assert _unlanded_paths(repo, it, "HEAD") == (None, False)
    assert _unlanded_paths(repo, Item(id="T1", kind="task", title="t"), "HEAD") == (None, True)


def test_the_real_diffless_callers_use_the_diff_pipeline(repo):
    """critic: the merge queue's checks and test scope's ci question see the doc pipeline."""
    from ddflow.api import mergequeue_run as MR
    from ddflow.services import testscope as TSC

    _unmerged_task(repo, "docs/note.md")
    _commit(repo, "docs/note.md", "merge T1: the note")
    cfg = Config.load(repo)
    it = _state(repo).items["T1"]
    # no ci/unit_tests in a docs-only pipeline: nothing is run (a task pipeline would run ci)
    assert MR._run_checks(repo, cfg, it, None, {}, unit_tests=True, timeout_s=1, agent="a") == ""
    assert "ci" not in TSC._pipeline(cfg, it, repo)
