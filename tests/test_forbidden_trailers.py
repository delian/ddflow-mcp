"""The commit-msg hook refuses a configured trailer, whoever writes the commit and however.

Operator 2026-09-29 (decision D-strip-coauthor-history): commit messages never carry the
co-author attribution trailer. The Claude Code PreToolUse hook that enforced it reads only
the command text, so `git commit -F <file>`, the editor, and every non-Claude agent went
straight past it -- plain git accepted such a commit. The hook git itself runs is the one
place every route passes through.

The trailer is spelled in pieces throughout: this file is itself committed, and the
operator's own hook refuses a command that carries it literally.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.services import enforce as E

KEY = "Co-" + "Authored-By"
LINE = f"{KEY}: Some Agent <agent@example.com>"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _hooked(repo: Path, forbidden: str) -> None:
    assert run_cli(repo, "init")[0] == 0
    (repo / ".ddflow" / "config.toml").write_text(
        '[enforce]\ncommit_without_lease = "off"\ngenerated_views = "off"\n'
        f'stale_docs = "off"\nforbidden_trailers = {forbidden}\n'
    )
    code, out, err = run_cli(repo, "hooks", "install")
    assert code == 0, out + err
    (repo / "a.txt").write_text("x\n")
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-qm", "scaffold", "--no-verify").returncode == 0


def _commit_file(repo: Path, message: str) -> subprocess.CompletedProcess:
    (repo / "a.txt").write_text((repo / "a.txt").read_text() + "y\n")
    _git(repo, "add", "a.txt")
    msg = repo.parent / "msg.txt"
    msg.write_text(message)
    return _git(repo, "commit", "-F", str(msg))


# -- the pure check -----------------------------------------------------------------------


def test_a_forbidden_trailer_is_refused():
    code, msg = E.check_forbidden_trailers(f"subject\n\nbody\n\n{LINE}\n", [KEY])
    assert code == 1 and KEY in msg, msg


def test_the_key_is_matched_case_insensitively():
    assert E.check_forbidden_trailers(f"s\n\n{LINE.lower()}\n", [KEY])[0] == 1


def test_it_is_refused_outside_the_final_paragraph_too():
    """Stricter than git's trailer parser on purpose: GitHub credits a co-author from a
    line git would not call a trailer, and the preference is about the line."""
    assert E.check_forbidden_trailers(f"s\n\n{LINE}\n\nmore prose\n", [KEY])[0] == 1


def test_a_hash_prefixed_line_is_refused():
    """Rubber-duck: `git commit -F` cleans up with `whitespace`, which keeps `#` lines."""
    assert E.check_forbidden_trailers(f"s\n\n#{LINE}\n", [KEY])[0] == 1
    assert E.check_forbidden_trailers(f"s\n\n  # {LINE}\n", [KEY])[0] == 1


def test_a_mention_in_prose_is_not_a_trailer():
    msg = f"s\n\nWe no longer add a {KEY} line to messages.\n"
    assert E.check_forbidden_trailers(msg, [KEY]) == (0, "")


def test_nothing_is_forbidden_by_default():
    assert E.check_forbidden_trailers(f"s\n\n{LINE}\n", []) == (0, "")


# -- through the real hook, the routes the Claude hook could not see ------------------------


def test_git_commit_dash_F_with_the_trailer_is_refused_by_the_hook(repo):
    _hooked(repo, f'["{KEY}"]')
    r = _commit_file(repo, f"work\n\n{LINE}\n")
    assert r.returncode != 0, r.stdout + r.stderr
    assert KEY in r.stdout + r.stderr


def test_a_hash_prefixed_line_via_dash_F_is_refused_by_the_hook(repo):
    _hooked(repo, f'["{KEY}"]')
    r = _commit_file(repo, f"work\n\n#{LINE}\n")
    assert r.returncode != 0, r.stdout + r.stderr


def test_a_clean_message_commits(repo):
    _hooked(repo, f'["{KEY}"]')
    assert _commit_file(repo, "work\n\nplain body\n").returncode == 0


def test_a_merge_commit_carrying_it_is_refused_too(repo):
    """Unlike the Item trailer, a merge is NOT exempt: its message is written by whoever
    concludes the merge, and that is exactly who adds the line."""
    _hooked(repo, f'["{KEY}"]')
    _git(repo, "checkout", "-qb", "side")
    assert _commit_file(repo, "side work\n").returncode == 0
    _git(repo, "checkout", "-q", "-")
    (repo / "b.txt").write_text("b\n")
    _git(repo, "add", "b.txt")
    assert _git(repo, "commit", "-qm", "main work").returncode == 0
    msg = repo.parent / "merge.txt"
    msg.write_text(f"Merge side\n\n{LINE}\n")
    r = _git(repo, "merge", "--no-ff", "side", "-F", str(msg))
    assert r.returncode != 0, r.stdout + r.stderr


def test_not_configured_the_hook_allows_it(repo):
    _hooked(repo, "[]")
    assert _commit_file(repo, f"work\n\n{LINE}\n").returncode == 0
