"""Physical resources: what work RUNS on, beside the files it writes.

Globs keep two agents out of one file. Nothing kept two agents from both starting an
8-GPU run on an 8-GPU box -- the one collision that costs hours rather than a merge, on
both projects ddflow is meant to take over (an 8xH200 trainer and an 8-replica vLLM
fleet generating data). The rule there was prose: "check nvidia-smi, ask first".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import Lease
from ddflow.core.schedule import parse_resources, resource_shortfall

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _setup(repo: Path, caps: str = '["gpu=8"]') -> None:
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(f"[schedule]\nresources = {caps}\n")
    for tid, res, glob in (
        ("TRAIN", "gpu:6", "a.py"),
        ("EVAL", "gpu:4", "b.py"),
        ("DOC", "", "c.md"),
    ):
        run_cli(repo, "task", "add", tid, "--globs", glob)
        if res:
            run_cli(repo, "update", tid, "--resources", res)


def test_parsing_counts_and_refuses_a_count_that_is_not_one():
    assert parse_resources(["gpu:4", "vllm-fleet", "gpu:2"]) == {"gpu": 6, "vllm-fleet": 1}
    for bad in (["gpu:0"], ["gpu:-1"], ["gpu:four"]):
        try:
            parse_resources(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad} was accepted")


def test_every_holder_counts_including_the_claimant():
    """Two items held by ONE agent still want two sets of GPUs -- unlike two items
    writing one file, where one author cannot collide with itself."""
    live = {"A": Lease(holder="me", acquired_at=0, renewed_at=0, ttl_s=1, resources=["gpu:6"])}
    assert "6 of 8 in use by A (6)" in resource_shortfall(["gpu:4"], live, {"gpu": 8})
    assert resource_shortfall(["gpu:2"], live, {"gpu": 8}) == ""
    assert resource_shortfall(["gpu:4"], live, {"gpu": 8}, exclude="A") == ""


def test_an_undeclared_resource_is_exclusive():
    live = {"A": Lease(holder="x", acquired_at=0, renewed_at=0, ttl_s=1, resources=["vllm-fleet"])}
    assert resource_shortfall(["vllm-fleet"], live, {}) != ""
    assert "capacity is 1" in resource_shortfall(["vllm-fleet:2"], {}, {})


def test_a_claim_that_would_overcommit_is_refused_by_ANOTHER_agent_too(repo):
    _setup(repo)
    code, _out, err = run_cli(repo, "claim", "TRAIN", "--no-worktree", agent="alice")
    assert code == OK, err
    code, _out, err = run_cli(repo, "claim", "EVAL", "--no-worktree", agent="bob")
    assert code == REFUSED, "two runs were granted 10 of 8 GPUs"
    assert "6 of 8 in use by TRAIN" in err
    assert "DOC" in err, "a refusal must name what could be taken instead"


def test_next_withholds_work_whose_resources_are_in_use_and_says_why(repo):
    _setup(repo)
    run_cli(repo, "claim", "TRAIN", "--no-worktree", agent="alice")
    _code, out, _ = run_cli(repo, "--json", "next", agent="bob")
    data = json.loads(out)
    assert "EVAL" not in {r["id"] for r in data["ready"]}
    blocked = {b["item"]: b for b in data["blocked"]}
    assert blocked["EVAL"]["reason"] == "resources"
    run_cli(repo, "release", "TRAIN", agent="alice")
    _c, out, _ = run_cli(repo, "--json", "next", agent="bob")
    assert "EVAL" in {r["id"] for r in json.loads(out)["ready"]}


def test_a_claim_can_declare_less_than_the_item_and_a_new_claim_fits(repo):
    _setup(repo)
    code, _o, err = run_cli(
        repo, "claim", "TRAIN", "--no-worktree", "--resources", "gpu:2", agent="alice"
    )
    assert code == OK, err
    code, _o, err = run_cli(repo, "claim", "EVAL", "--no-worktree", agent="bob")
    assert code == OK, err


def test_resources_are_read_from_the_plan_file(repo):
    (repo / "docs").mkdir()
    (repo / "docs" / "todo.md").write_text(
        "## Train\n\n- [ ] **T.1** — the run\n  **Globs:** runs/**\n  **Resources:** gpu:8\n"
    )
    code, _o, err = run_cli(repo, "import", "--apply")
    assert code == OK, err
    _c, out, _e = run_cli(repo, "--json", "show", "T.1")
    assert json.loads(out)["resources"] == ["gpu:8"]


# -- roborev 828 ------------------------------------------------------------------------


def test_re_claiming_with_different_resources_updates_the_reservation(repo):
    """The renewal path carried globs and dropped resources: `--resources gpu:8` on a
    held item exited 0 while the lease kept its old reservation."""
    _setup(repo)
    run_cli(repo, "claim", "TRAIN", "--no-worktree", "--resources", "gpu:2", agent="alice")
    code, _o, err = run_cli(
        repo, "claim", "TRAIN", "--no-worktree", "--resources", "gpu:8", agent="alice"
    )
    assert code == OK, err
    code, _o, err = run_cli(repo, "claim", "EVAL", "--no-worktree", agent="bob")
    assert code == REFUSED, "the old 2-GPU reservation was still the one counted"


def test_a_malformed_capacity_is_named_as_a_config_error_not_a_conflict(repo):
    _setup(repo, caps='["gpu:8"]')
    code, _o, err = run_cli(repo, "claim", "TRAIN", "--no-worktree")
    assert code == REFUSED
    assert "[schedule] resources entry 'gpu:8'" in err
    assert "Wait for one to be released" not in err
