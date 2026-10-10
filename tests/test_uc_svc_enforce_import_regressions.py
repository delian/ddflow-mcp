"""Regressions found while splitting services.enforce (B-uc-svc-enforce-import)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ddflow.api import rules as ARULES
from ddflow.config import Config
from ddflow.services import enforce as E
from ddflow.services.rules import Rule


def test_an_empty_key_list_still_accepts_the_default_item_trailer() -> None:
    # B-ff2ba2bb56: the message named `Item: <id>` but the matching set was empty, so
    # writing it was refused again forever.
    got = E.check_item_trailer("x\n\nItem: P1.T3\n", [], ids=lambda: {"P1.T3"})
    assert got.exit == 0, got.message


def test_an_empty_key_list_still_refuses_an_unknown_item() -> None:
    got = E.check_item_trailer("x\n\nItem: NOPE\n", [], ids=lambda: {"P1.T3"})
    assert got.exit == 1 and "no item in the queue has this id" in got.message


def test_a_staged_rule_file_is_named_in_the_git_add_remedy(repo: Path) -> None:
    # B7e87324fea: the remedy ended in a bare `git add ` when only a rule file was staged.
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    ARULES.rule_add(
        repo, Rule(id="r-a", title="Alpha naming", content="Body.", priority=60), agent="t"
    )
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base", "--no-verify")
    # the second rule moves the committed shard; only a rule file is staged
    ARULES.rule_add(
        repo,
        Rule(
            id="r-b",
            title="Zebra crossing policy",
            content="Quite unrelated words here.",
            priority=60,
        ),
        agent="t",
    )
    path = repo / ".ddflow" / "rules" / "r-a.toml"
    path.write_text(path.read_text().replace("Body.", "Edited."))
    git("add", ".ddflow/rules/r-a.toml")
    got = E.check_views(repo, Config.load(repo))
    assert got.exit == 1 and "event log" in got.message, got.message
    assert "    git add .ddflow/rules/r-a.toml" in got.message, got.message
