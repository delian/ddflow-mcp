"""One measured record path for gate outcomes (B-uni-gate-record.4-record).

`gate record` measured the item's tree and checked the pipeline order; `review`, the local
`merge` and the forge sync recorded without either, and the forge sync died on a project
whose `merge` gate is a human checkpoint (bug B279a0ebfc1). `services/gates/measured.py`
holds the order check (`check_order`) and the measured write (`record_measured`,
`record_merge`); this module pins that each family goes through them.
"""

from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_flow import _state, _work, pr_repo  # noqa: F401  (a fixture, used by name)

import ddflow.api.review as api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _reviewed_repo(repo: Path, tmp_path: Path, config_extra: str = "") -> Path:
    """A command reviewer that finds nothing, and a branch `feat` with one change."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        f'#!/bin/sh\nin=$(cat)\nprintf "%s" "$in" > "{prompts}/$$.txt"\necho "STATUS: NO FINDINGS"\n'
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\nhedge = 1\ngates = ["critic", "rubber_duck"]\n' + config_extra
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1\n")
    _git(repo, "add", "x.py")
    _git(repo, "commit", "-qm", "work")
    return prompts


def _item(repo: Path, item: str = "T1"):
    return fold(EventLog(repo).read_all(), strict=False).items[item]


def _kinds(repo: Path) -> list[str]:
    return [e.kind for e in EventLog(repo).read_all()]


# -- review ------------------------------------------------------------------------------


def test_a_review_outcome_carries_ddflows_measurement_of_the_tree(repo, tmp_path):
    _reviewed_repo(repo, tmp_path)
    api.review(repo, gate="critic", item="T1", branch="feat")

    rec = _item(repo).gates["critic"]
    assert rec.outcome == "passed", rec  # the reviewer found nothing, and that was recorded
    assert rec.evidence.get("tree_sha"), rec.evidence
    assert set(rec.evidence["diff_stat"]) >= {"files"}, rec.evidence


def test_a_caller_standing_in_another_items_tree_is_not_measured(repo):
    """`item_tree` answers (None, whose-tree-it-is) there, and `measure_tree(None)` is
    nothing: another item's tree is never attributed to this one (bug Bbc9a7ee3f2)."""
    from ddflow.services.gates import measured as GM

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    assert GM.measure_tree(repo, _item(repo), None) == {}


def test_a_review_out_of_order_is_noted_and_recorded_under_warn(repo, tmp_path):
    _reviewed_repo(repo, tmp_path)
    out = api.review(repo, gate="critic", item="T1", branch="feat")

    assert "comes after" in out.data["text"], out.data["text"]
    assert "gate.out_of_order" in _kinds(repo)


def test_a_review_out_of_order_is_refused_before_it_runs_under_block(repo, tmp_path):
    prompts = _reviewed_repo(repo, tmp_path, '\n[gates]\nenforce_order = "block"\n')
    out = api.review(repo, gate="critic", item="T1", branch="feat")

    assert out.exit != 0 and "comes after" in out.reason, out
    assert not list(prompts.iterdir()), "the reviewer was asked despite the refusal"
    assert "critic" not in _item(repo).gates


def test_a_review_in_order_is_not_noted(repo, tmp_path):
    _reviewed_repo(repo, tmp_path)
    for gate in ("research", "rules", "implement", "rubber_duck"):
        run_cli(repo, "gate", "record", "T1", gate, "--outcome", "passed", "--evidence", "x")
    out = api.review(repo, gate="critic", item="T1", branch="feat")
    assert "comes after" not in out.data["text"]


def test_a_review_of_an_id_no_item_has_is_recorded_not_a_traceback(repo, tmp_path):
    """The record path takes the id the caller gave, as it always did: an id that names no
    item is recorded unmeasured (the fold makes the item), it does not crash the review."""
    _reviewed_repo(repo, tmp_path)
    out = api.review(repo, gate="critic", item="NOPE", branch="feat", intent="add x.py")

    assert out.exit == 0, out
    assert "recorded NOPE.critic = passed" in out.data["text"]
    assert _item(repo, "NOPE").gates["critic"].outcome == "passed"


# -- merge -------------------------------------------------------------------------------


def test_a_local_merge_records_its_gate_with_the_measured_tree(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    code, out, err = run_cli(repo, "--json", "claim", "T1")
    assert code == 0, err
    tree = Path(json.loads(out)["worktree"])
    (tree / "a.py").write_text("a\n")
    _git(tree, "add", "a.py")
    _git(tree, "commit", "-qm", "feat: a")
    code, out, err = run_cli(repo, "--json", "merge", "T1")
    assert code == 0, (out, err)

    ev = _item(repo).gates["merge"].evidence
    assert ev.get("tree_sha") and "diff_stat" in ev, ev


# -- the forge sync ------------------------------------------------------------------------


def test_a_forge_merge_settles_when_the_merge_gate_is_a_human_checkpoint(pr_repo):  # noqa: F811
    """`pr sync` died with the human-gate refusal when the request had merged and `merge`
    was a person's gate: the gate is theirs to clear, and the merge is a fact."""
    repo, forge, _remote = pr_repo
    (repo / ".ddflow" / "gates.toml").write_text('[gate.merge]\nhuman = true\ntitle = "Merge"\n')
    _git(repo, "add", ".ddflow")
    _git(repo, "commit", "-qm", "chore: human merge gate")
    _git(repo, "push", "-q", "origin", "main")
    _work(repo, "T1", "a.py", "a\n")
    code, _, err = run_cli(repo, "merge", "T1", "--model", "claude-opus-5-5")
    assert code == 0, err
    forge.approve(1)
    forge.merge_as_human(1)

    code, out, err = run_cli(repo, "pr", "sync")

    assert code == 0, (out, err)
    assert "merge" not in _state(repo).items["T1"].gates, "an agent cleared a human gate"
    assert "pr.merged" in _kinds(repo)
