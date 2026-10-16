"""A review round that only re-checks fixes (bug Be1818cd552): findings whose cited lines
a later commit rewrote are marked superseded, not re-raised, and such a round is free."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

import ddflow.api.review as api
from ddflow.api import review_supersede as SS
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _setup(repo: Path, tmp_path: Path, extra: str = "") -> Path:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        "#!/bin/sh\nin=$(cat)\n"
        f'printf "%s" "$in" > "{prompts}/$$.txt"\n'
        'case "$in" in *"+x = 1  # FINDME"*) printf \'FINDING HIGH x.py:1\\nbad line\\n\\n'
        "STATUS: FINDINGS 1\\n';; *) echo 'STATUS: NO FINDINGS';; esac\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n" + extra
        + f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic", "rubber_duck"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1  # FINDME\ny = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")
    return prompts


def _review(repo: Path, **kw):
    return api.review(repo, gate="critic", item="T1", branch="feat", **kw)


def _commit(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-qm", "c")


def _prompts_of(prompts: Path, call) -> str:
    before = set(prompts.iterdir())
    call()
    return "\n".join(p.read_text() for p in sorted(set(prompts.iterdir()) - before))


def _ev(repo: Path) -> dict:
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"].evidence


def test_old_side_ranges_and_locations():
    diff = "--- a/x.py\n+++ b/x.py\n@@ -2,4 +2,3 @@\n k\n-a\n-b\n+c\n z\n"
    ch = SS.changed_old_lines(diff)
    assert ch == {"x.py": [(3, 3), (4, 4)]}
    assert SS.is_superseded("x.py:4", ch) and not SS.is_superseded("x.py:2", ch)
    assert not SS.is_superseded("x.py:5", ch)
    assert SS.is_superseded("x.py", ch) and not SS.is_superseded("y.py:3", ch)


def test_a_finding_on_rewritten_lines_is_superseded_not_reraised(repo, tmp_path):
    prompts = _setup(repo, tmp_path)
    _review(repo)
    _commit(repo, "x.py", "x = 2\ny = 2\n")
    text = _prompts_of(prompts, lambda: _review(repo))
    assert "SUPERSEDED" in text
    assert "triage: untriaged" not in text
    ev = _ev(repo)
    assert ev["superseded"] and ev["fix_round"] is True


def test_a_finding_on_untouched_lines_is_still_reraised(repo, tmp_path):
    prompts = _setup(repo, tmp_path)
    _review(repo)
    _commit(repo, "x.py", "x = 1  # FINDME\ny = 3\n")  # line 2 only
    text = _prompts_of(prompts, lambda: _review(repo, full=True))
    assert "SUPERSEDED" not in text and "triage: untriaged" in text
    assert "superseded" not in _ev(repo)


def test_a_fix_round_does_not_spend_the_budget(repo, tmp_path):
    _setup(repo, tmp_path, "[review]\nmax_rounds = 1\n")
    _review(repo)
    _commit(repo, "x.py", "x = 2\ny = 2\n")
    out = _review(repo)
    assert out.exit == 0, out.reason
    assert _ev(repo)["fix_round"] is True
    # still one full round used, so a plain full re-review stays refused
    _commit(repo, "z.py", "z = 1\n")
    assert _review(repo).exit == 3
