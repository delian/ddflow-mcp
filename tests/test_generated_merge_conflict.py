"""A merge conflict in a generated view or a committed config is named, not a traceback (B6b8d1f7a0f).

`docs/ddflow/QUEUE.md`, `LESSONS.md` and `LESSONS-SUMMARY.md` are tracked and derived from
the log, so two clones that each `ddflow render` and commit conflict on all of them at every
pull. They are regenerated, never hand-merged: `doctor` names each conflicted view with the
exact fix, and `ddflow render` over conflict markers rewrites the file.

A committed config layer holding conflict markers made EVERY command die with a raw
`TOMLDecodeError`. It is now a typed refusal (exit 3) naming the file; `doctor` and `help`
still run. A file that is merely malformed (no markers) behaves as before.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from helpers import git_raw as _git

from tests.conftest import run_cli

VIEWS = ("QUEUE.md", "LESSONS.md", "LESSONS-SUMMARY.md")
FIX = "git checkout --theirs docs/ddflow && ddflow render && git add docs/ddflow"


def _clone(remote: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git(dest, "config", k, v)
    return dest


def _commit(repo: Path, msg: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg, "--no-verify")


@pytest.fixture
def clones(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    a = _clone(remote, tmp_path / "a")
    (a / "README.md").write_text("x\n")
    _commit(a, "init")
    assert run_cli(a, "adopt", "--agents", "claude", "--launch", "python", agent="seed")[0] == 0
    assert run_cli(a, "render", agent="seed")[0] == 0
    _commit(a, "adopt")
    _git(a, "push", "-q", "origin", "main")
    return a, _clone(remote, tmp_path / "b")


def _diverge(a: Path, b: Path) -> None:
    """Each clone adds a task and a lesson, renders, commits; A pushes; B pulls into conflict."""
    for repo, who, t in ((a, "U1", "TA"), (b, "U2", "TB")):
        assert (
            run_cli(repo, "task", "add", t, "--title", t, "--globs", f"src/{t}.py", agent=who)[0]
            == 0
        )
        assert (
            run_cli(repo, "lesson", "add", "--id", f"L{t}", "--title", t, "--rule", t, agent=who)[0]
            == 0
        )
        assert run_cli(repo, "render", agent=who)[0] == 0
        _commit(repo, t)
    _git(a, "push", "-q", "origin", "main")
    p = subprocess.run(
        ["git", "pull", "--no-rebase", "--no-edit", "origin", "main"],
        cwd=b,
        capture_output=True,
        text=True,
    )
    assert p.returncode != 0, p.stdout


def test_doctor_names_each_conflicted_view_and_the_fix(clones):
    a, b = clones
    _diverge(a, b)
    code, out, err = run_cli(b, "doctor", agent="U2")
    text = out + err
    assert code == 1, text
    for v in VIEWS:
        assert f"docs/ddflow/{v}" in text, text
    assert FIX in text, text


def test_render_regenerates_a_conflicted_view(clones):
    a, b = clones
    _diverge(a, b)
    q = b / "docs/ddflow/QUEUE.md"
    assert "<<<<<<<" in q.read_text()
    _git(b, "checkout", "--theirs", "docs/ddflow")
    assert run_cli(b, "render", agent="U2")[0] == 0
    for v in VIEWS:
        assert "<<<<<<<" not in (b / "docs/ddflow" / v).read_text()
    # with markers still in place (nothing checked out): render rewrites them too
    q.write_text("<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> origin/main\n")
    assert run_cli(b, "render", agent="U2")[0] == 0
    assert "<<<<<<<" not in q.read_text()
    _commit(b, "merge")
    assert run_cli(b, "doctor", agent="U2")[0] == 0


MARKED = "<<<<<<< HEAD\n[lease]\nttl_s = 600\n=======\n[lease]\nttl_s = 900\n>>>>>>> origin/main\n"


@pytest.mark.parametrize(
    "rel", [".ddflow/config.toml", ".ddflow/gates.toml", ".ddflow/local/config.toml"]
)
def test_a_conflicted_config_layer_is_refused_by_name(clones, rel):
    a, _ = clones
    f = a / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(MARKED)
    for argv in (("status",), ("show", "TA"), ("gate", "status", "TA"), ("history",)):
        code, out, err = run_cli(a, *argv, agent="U1")
        text = out + err
        assert code == 3, (argv, code, text)
        assert f"resolve the merge conflict in {rel} (git status)" in text, text
        assert "Traceback" not in text and "TOMLDecodeError" not in text, text
    assert run_cli(a, "help", agent="U1")[0] == 0
    code, out, err = run_cli(a, "doctor", agent="U1")
    assert code == 1 and rel in out and "Traceback" not in out + err, out + err


def test_the_view_names_doctor_scans_are_the_ones_render_writes():
    from ddflow.services.shared_files import VIEW_NAMES
    from ddflow.views.markdown import VIEWS

    assert VIEW_NAMES == tuple(name for name, _ in VIEWS)


def test_a_merely_malformed_config_keeps_todays_behaviour(clones):
    a, _ = clones
    (a / ".ddflow/config.toml").write_text("[lease\nttl_s = = 1\n")
    code, out, err = run_cli(a, "status", agent="U1")
    assert "resolve the merge conflict" not in out + err
    assert code != 0
