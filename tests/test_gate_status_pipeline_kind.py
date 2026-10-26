"""B4d97c00907: gate status and complete disagree on the pipeline of a kind=research task.

ATF-research-pass2 was added with `task add --kind research`: `ddflow gate status` resolved the
doc pipeline from the ACTUAL docs-only diff (rules, implement, standards, dedupe, merge) and
printed COMPLETE after merge, while `ddflow complete` still demanded the kind's task pipeline
(research, rubber_duck, critic, ci, unit_tests, bug_hunt never run; unit_tests required even
after a skip). Only `update --kind doc` made completion possible.

Expected: ONE pipeline per kind, the same in both commands — the steps `gate status` walks and
the gates `complete` requires are the same list, from the one shared resolver. The earlier
fixes pinned this for `task` (B-complete-ignores-diff-doc-pipeline); this pins it for a
RESEARCH item in every diff state: docs-only unmerged, docs-only after merge, and code.
"""

from __future__ import annotations

import json
import subprocess

from conftest import run_cli
from test_na_required_gate import _state

from ddflow.config import Config
from ddflow.services import completion as CO
from ddflow.services import gates as G


def _research(repo, *, globs: str = "docs/**") -> None:
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "R1", "--title", "r", "--kind", "research", "--globs", globs)


def _research_change(repo, *, globs: str, path: str) -> None:
    """A claimed R1 whose worktree commits ``path`` — the item itself, for ITS diff."""
    _research(repo, globs=globs)
    code, out, err = run_cli(repo, "claim", "R1", agent="a1")
    assert code == 0, out + err
    tree = next(l.split("worktree:", 1)[1].strip() for l in out.splitlines() if "worktree:" in l)
    target = __import__("pathlib").Path(tree) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("change\n")
    subprocess.run(["git", "-C", tree, "add", path], check=True)
    subprocess.run(["git", "-C", tree, "commit", "-qm", "change"], check=True)


def _dwelling(repo) -> tuple[Config, object, list[str]]:
    """The pipeline GATE STATUS walks, and the gates COMPLETE cites, computed side by side.

    `verdict` derives both of its gate refusals ("never run and never skipped",
    "required gate(s) not passed") from the `GateStatus` object `gate status` renders, so
    the two names of the property stay ONE computation here; the disagreement this bug
    recorded came from the two surfaces once not sharing it. `cited` is the set
    `s.silent` list a completion would refuse over, in pipeline order.
    """
    cfg = Config.load(repo)
    st = _state(repo)
    it = st.items["R1"]
    s = G.status(st, cfg, "R1", G.load_gates(repo, cfg), diff=lambda: CO.changed_paths(repo, it))
    return cfg, s, list(s.silent)


def test_research_docs_only_status_steps_equal_the_gates_complete_requires(repo):
    """Unmerged, docs-only: both sides run the DOC pipeline; complete requires its gates."""
    _research_change(repo, globs="docs/**", path="docs/note.md")
    cfg, s, cited = _dwelling(repo)
    assert s.pipeline == list(cfg.gates.doc_pipeline), s.pipeline
    assert cited == list(cfg.gates.doc_pipeline), cited

    # The exact gates the bug says complete demanded are required of nothing here.
    code, out, err = run_cli(repo, "gate", "status", "R1", agent="a1")
    assert code == 0, out + err
    code, out, err = run_cli(repo, "complete", "R1", "--model", "claude-sonnet-5-5", agent="a1")
    for stray in ("research", "rubber_duck", "critic", "ci", "unit_tests", "bug_hunt"):
        assert stray not in err, (stray, err)
    assert "unit_tests" not in (out + err), out + err


def test_research_docs_only_after_merge_status_and_complete_agree(repo):
    """The bug's own journey: pass the pipeline `gate status` names, merge, complete."""
    _research_change(repo, globs="docs/**", path="docs/note.md")
    code, out, err = run_cli(repo, "--json", "gate", "status", "R1", agent="a1")
    cfg = Config.load(repo)
    steps = json.loads(out)["pipeline"]
    assert steps == list(cfg.gates.doc_pipeline), steps
    for g in steps:
        code, out, err = run_cli(
            repo, "gate", "record", "R1", g, "--outcome", "passed", "--evidence", "ran it",
            agent="a1",
        )  # fmt: skip
        assert code == 0, (g, out, err)
    code, out, err = run_cli(repo, "merge", "R1", agent="a1")
    assert code == 0, out + err
    code, out, err = run_cli(repo, "gate", "status", "R1", agent="a1")
    assert code == 0 and "COMPLETE" in out and "unit_tests" not in out, out + err
    code, out, err = run_cli(repo, "complete", "R1", "--model", "claude-sonnet-5-5", agent="a1")
    assert code == 0, out + err


def test_research_code_diff_status_steps_equal_the_gates_complete_requires(repo):
    """A research item that changed code keeps the FULL task pipeline on both sides."""
    _research_change(repo, globs="src/**", path="src/code.py")
    cfg, s, cited = _dwelling(repo)
    assert s.pipeline == list(cfg.gates.task_pipeline), s.pipeline
    assert cited == list(cfg.gates.task_pipeline), cited


def test_research_unreadable_diff_keeps_one_pipeline_on_both_sides(repo):
    """No diff read, nothing claimed: both sides hold the declared pipeline, never a guess."""
    _research(repo)
    cfg, s, cited = _dwelling(repo)
    assert s.pipeline == list(cfg.gates.task_pipeline), s.pipeline
    assert cited == list(cfg.gates.task_pipeline), cited
