"""A shared glob's files are probed with `-z` (B9c56de9d58).

`_probe_paths` ran `git ls-files` without `-z`, so git C-quoted `dir/café.md` as
`"dir/caf\\303\\251.md"`, the glob never matched it and the probe fell back to the bare
glob; `drivers` then asked `check-attr` about the literal pattern, also without `-z`.
A tracked non-ASCII file under an append-only glob was invisible to the merge-driver
check.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.infra import proc as P
from ddflow.services import shared_files as SF


def _track(repo: Path, rel: str, text: str = "x\n") -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "--", rel], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", f"add {rel}"], check=True)


def test_probe_finds_a_tracked_non_ascii_path(repo):
    _track(repo, "dir/café.md")
    assert SF._probe_paths(repo, "dir/*.md") == ["dir/café.md"]


def test_drivers_reports_the_driver_of_a_non_ascii_path(repo):
    _track(repo, ".gitattributes", "dir/*.md merge=union\n")
    _track(repo, "dir/café.md")
    assert SF.drivers(repo, "dir/*.md") == {"dir/café.md": "union"}
    assert SF.driver(repo, "dir/*.md") == "union"


def test_a_git_that_cannot_run_gives_the_glob_itself(repo, monkeypatch):
    _track(repo, "dir/café.md")

    def boom(*_a, **_k):
        raise OSError("git vanished")

    monkeypatch.setattr(P, "run", boom)
    assert SF._probe_paths(repo, "dir/*.md") == ["dir/*.md"]
    assert SF.drivers(repo, "dir/*.md") == {"dir/*.md": ""}


def test_a_git_that_times_out_gives_the_glob_itself(repo, monkeypatch):
    _track(repo, "dir/café.md")

    def slow(*a, **_k):
        raise P.TimeoutExpired(a[0] if a else "git", 1)

    monkeypatch.setattr(P, "run", slow)
    assert SF._probe_paths(repo, "dir/*.md") == ["dir/*.md"]
    assert SF.drivers(repo, "dir/*.md") == {"dir/*.md": ""}
