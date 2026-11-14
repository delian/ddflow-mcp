"""Declared reviewer priority: highest-priority (lowest number) first within a tier.

Cross-family independence and the degraded shed each exist already: `_shed_degraded`
drops reviewers a quota/429 wall marked, and `_order_cross_family_first` ranks a reviewer
OUTSIDE the author's family ahead of one that establishes nothing. What was missing is a
DECLARED priority per `[[reviewer]]`: today priority is only the implicit config order, so
the gate would pay a slow cross-family reviewer before a fast one that happened to be
listed later.

The field is `priority` (default 100, LOWER is served first -- the same convention as an
item's task priority). It sorts WITHIN the cross-family tier: a reviewer outside the
author's family still serves before a same-family one whatever their priorities, and
priority only orders reviewers of the same tier, config order breaking ties. Re-probing a
higher-priority reviewer after `[review].degraded_cooldown_s` is the existing shed/cooldown
path, unchanged.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

import ddflow.api.review as api
import ddflow.api.review.run as run_mod
from ddflow.config import Config
from ddflow.services.review import Reviewer


def _rev(name: str, model: str, family: str, priority: int) -> Reviewer:
    return Reviewer(name=name, model=model, family=family, priority=priority)


def _order(revs, author: str = "deepseek-ai/DeepSeek-V4-Pro-0813-Godzila", cfg=None) -> list[str]:
    cfg = cfg if cfg is not None else Config()
    return [r.name for r in run_mod._order_cross_family_first(revs, author, cfg, lambda _: None)]


# -- the field ------------------------------------------------------------------------


def test_priority_defaults_to_100():
    assert Reviewer(name="r").priority == 100


# -- ordering -------------------------------------------------------------------------


def test_priority_is_lower_first_within_the_cross_family_tier():
    revs = [
        _rev("later", "grok-4.7", "xai", 90),
        _rev("middle", "kimi-for-coding-highspeed", "moonshot", 50),
        _rev("first", "gemini-3.5-flash-lite", "google", 10),
    ]
    assert _order(revs) == ["first", "middle", "later"]


def test_cross_family_still_dominates_priority():
    revs = [
        _rev("same-family-urgent", "deepseek-ai/DeepSeek-V4.1-Flash", "deepseek", 1),
        _rev("cross-family-late", "grok-4.7", "xai", 100),
    ]
    assert _order(revs) == ["cross-family-late", "same-family-urgent"]


def test_priority_ties_keep_config_order():
    revs = [_rev("a", "grok-4.7", "xai", 50), _rev("b", "gemini-3.5-flash-lite", "google", 50)]
    assert _order(revs) == ["a", "b"]


# -- end to end -----------------------------------------------------------------------


def _diary_reviewer(tmp_path: Path, name: str, diary: Path, stdout: str, exit_code: int) -> Path:
    """A `kind = "command"` reviewer appending its NAME to the shared ``diary`` so the
    review is counted and ORDERED by when each reviewer was invoked; `hedge = 1` keeps
    one diary entry per reviewer."""
    cli = tmp_path / f"{name}.sh"
    cli.write_text(
        "#!/bin/sh\n"
        "cat > /dev/null\n"
        f'printf "%s\\n" "{name}" >> "{diary}"\n'
        f'printf "%s\\n" "{stdout}"\n'
        f"exit {exit_code}\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    return cli


def _config(a: Path, b: Path) -> str:
    # Config order is the bug: the SLOW cross-family reviewer FIRST, then the FAST one.
    return (
        "[worktree]\nenabled = false\n"
        '[agent]\nfamily = "deepseek"\n'
        f'[[reviewer]]\nname = "slow-cross"\nkind = "command"\ncommand = "{a}"\n'
        'model = "grok-4.7"\nfamily = "xai"\nhedge = 1\npriority = 90\n'
        'gates = ["critic", "rubber_duck"]\n'
        f'[[reviewer]]\nname = "fast-cross"\nkind = "command"\ncommand = "{b}"\n'
        'model = "gemini-3.5-flash-lite"\nfamily = "google"\nhedge = 1\npriority = 10\n'
        'gates = ["critic", "rubber_duck"]\n'
    )


def test_the_gate_pays_the_fast_cross_family_reviewer_first(repo, tmp_path):
    diary = tmp_path / "diary.log"
    diary.write_text("")
    slow = _diary_reviewer(tmp_path, "slow-cross", diary, "STATUS: NO FINDINGS", 0)
    fast = _diary_reviewer(tmp_path, "fast-cross", diary, "STATUS: NO FINDINGS", 0)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(_config(slow, fast))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add x", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1\n")
    _git(repo, "add", "x.py")
    _git(repo, "commit", "-qm", "work")

    out = api.review(repo, gate="rubber_duck", item="T1", branch="feat")
    assert out.data["outcome"] == "passed", out.data["text"]
    invited = diary.read_text().split()
    assert invited == ["fast-cross", "slow-cross"], invited
