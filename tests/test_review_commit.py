"""Reviewing ONE landed commit: the after-merge review both source projects run.

`roborev review <sha>` after every commit is how they work, and the branch is merged and
gone by then. `ddflow review` could only diff an item's branch or the working tree, so
the review of a landed commit reviewed whatever happened to be checked out.
"""

from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

from ddflow.core.model import fold
from ddflow.infra.log import EventLog

OK, FAIL, NOTHING = 0, 1, 2


def _commit(repo: Path, name: str, text: str, msg: str) -> str:
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-qm", msg)
    return _git(repo, "rev-parse", "HEAD")


def _setup(repo: Path, tmp_path: Path) -> None:
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        "#!/bin/sh\nprompt=$(cat)\n"
        'if echo "$prompt" | grep -q divide; then\n'
        "  printf 'FINDING HIGH calc.py:2\\nDivision by zero.\\n\\nSTATUS: FINDINGS 1\\n'\n"
        "else printf 'STATUS: NO FINDINGS\\n'; fi\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add division", "--globs", "calc.py")


def _findings(out: str) -> int:
    return out.count("Division by zero")


def test_the_named_commit_is_what_gets_reviewed(repo, tmp_path):
    _setup(repo, tmp_path)
    risky = _commit(repo, "calc.py", "def divide(a, b):\n    return a / b\n", "add divide")
    harmless = _commit(repo, "NOTES.md", "notes\n", "notes")
    code, out, err = run_cli(repo, "review", "T1", "--commit", risky, "--intent", "add division")
    assert code in (OK, FAIL), err
    assert _findings(out) == 1, "the risky commit's diff never reached the reviewer"
    _code, out, _err = run_cli(repo, "review", "T1", "--commit", harmless, "--intent", "notes")
    assert _findings(out) == 0, "a later commit's review saw an earlier commit's code"


def test_a_merge_is_reviewed_as_what_it_brought_in(repo, tmp_path):
    _setup(repo, tmp_path)
    _git(repo, "checkout", "-qb", "feat")
    _commit(repo, "calc.py", "def divide(a, b):\n    return a / b\n", "add divide")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "NOTES.md", "main moved\n", "main moves")
    _git(repo, "merge", "--no-ff", "-qm", "Merge feat", "feat")
    merge = _git(repo, "rev-parse", "HEAD")
    _code, out, _err = run_cli(repo, "review", "T1", "--commit", merge, "--intent", "merge")
    assert _findings(out) == 1, "a merge commit's review was empty (a merge's own diff is empty)"


def test_an_unknown_commit_is_recorded_unavailable_not_passed(repo, tmp_path):
    _setup(repo, tmp_path)
    code, out, err = run_cli(repo, "review", "T1", "--commit", "deadbeef", "--intent", "x")
    assert code == NOTHING
    assert "commit 'deadbeef' not found" in out + err, "the reason must name what is missing"
    st = fold(EventLog(repo).read_all(), strict=False)
    assert st.items["T1"].gates["critic"].outcome == "unavailable"


def test_over_mcp_too(repo, tmp_path):
    from ddflow.surfaces.mcp import Server

    _setup(repo, tmp_path)
    risky = _commit(repo, "calc.py", "def divide(a, b):\n    return a / b\n", "add divide")
    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ddflow_review",
                "arguments": {"id": "T1", "commit": risky, "intent": "add division"},
            },
        }
    )
    text = json.dumps(reply["result"])
    assert "Division by zero" in text


def test_a_shallow_clone_is_not_reviewed_against_the_empty_tree(repo, tmp_path):
    """roborev 830: a missing parent was taken for NO parent, and the whole tree was
    handed to the reviewer as the commit's change."""
    _setup(repo, tmp_path)
    _commit(repo, "calc.py", "def divide(a, b):\n    return a / b\n", "add divide")
    top = _commit(repo, "NOTES.md", "notes\n", "notes")
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"file://{repo}", str(shallow)], check=True
    )
    from ddflow.api.review import commit_diff

    diff, how = commit_diff(shallow, top)
    assert diff == "" and "not in this clone" in how, how
    diff, _how = commit_diff(repo, top)
    assert "NOTES.md" in diff and "calc.py" not in diff
