"""update's coordination checks are decided from a fold taken INSIDE the log lock (B518cb79124).

`items.update` planned its edit from the state folded at `_load` time. A concurrent claim or
move between that fold and the append was not caught: the plan's `--line` / `--parent` /
`--needs` checks (line frozen, claimed parent, dependency cycle) judged the world as it
WAS, and `_record_update` appended the stale decision under the lock.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import item_of as _item

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _lined(repo: Path) -> None:
    (repo / "lib.py").write_text("def f():\n    return 1\n")
    run_cli(repo, "init")
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write('\n[flow]\ncurrent_line = "2"\n[flow.lines]\n"1" = "maint/1.x"\n')


def test_a_line_move_is_refused_when_another_agent_claims_between_the_fold_and_the_append(
    repo, monkeypatch
):
    from ddflow.api import items as I

    _lined(repo)
    run_cli(repo, "task", "add", "T1")
    real_load = I._load

    def load_then_someone_claims(r, agent):
        snap = real_load(r, agent)  # T1 unclaimed: moving its line is fine
        assert run_cli(repo, "claim", "T1", "--no-worktree", agent="intruder")[0] == OK
        return snap

    monkeypatch.setattr(I, "_load", load_then_someone_claims)
    out = I.update(repo, "T1", I.ItemEdit(line="1"), agent="impl")
    assert out.exit == REFUSED, out
    assert _item(repo).line != "1", _item(repo)


def test_a_parent_move_under_a_task_claimed_since_the_fold_is_refused(repo, monkeypatch):
    from ddflow.api import items as I

    _lined(repo)
    run_cli(repo, "task", "add", "A")
    run_cli(repo, "task", "add", "T1")
    real_load = I._load

    def load_then_someone_claims(r, agent):
        # A holds nothing: a task moved under it may turn it into an umbrella.
        snap = real_load(r, agent)
        assert run_cli(repo, "claim", "A", "--no-worktree", agent="intruder")[0] == OK
        return snap

    monkeypatch.setattr(I, "_load", load_then_someone_claims)
    out = I.update(repo, "T1", I.ItemEdit(parent="A"), agent="impl")
    assert out.exit == REFUSED, out
    assert _item(repo).parent is None, _item(repo)
