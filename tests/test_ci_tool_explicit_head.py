"""`ddflow_ci` with an explicit ref=HEAD checks HEAD, as the command line does (B8f41346d9f).

The tool read an absent ref as "HEAD" and so could not tell it from a ref the caller named:
both ended up as the holder's item branch."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import run_cli
from helpers import commit as _commit
from helpers import git as _git
from test_ci_gate import _item_with_ci

from ddflow.surfaces.tools import TOOLS


def _call(repo, where, **args):
    api = TOOLS["ddflow_ci"]["api"]
    return api(repo, {"action": "run", **args}, "holder-w", called_from=where)


def test_an_explicit_head_is_head_and_no_ref_is_the_held_item(repo, monkeypatch):
    _item_with_ci(repo, monkeypatch, agent="holder-w")
    _, out, _ = run_cli(repo, "--json", "show", "T1")
    tree = Path(json.loads(out)["worktree"])
    tree = tree if tree.is_absolute() else repo / tree
    _commit(tree, {"w.txt": "w\n"}, "work")
    other = repo.parent / "elsewhere"
    other.mkdir()
    item_sha = _git(tree, "rev-parse", "HEAD").strip()
    main_sha = _git(repo, "rev-parse", "HEAD").strip()
    assert _call(repo, other).data["sha"] == item_sha  # no ref: the held item's branch
    assert _call(repo, other, ref="").data["sha"] == item_sha
    assert _call(repo, other, ref="HEAD").data["sha"] == main_sha  # named: taken at its word
