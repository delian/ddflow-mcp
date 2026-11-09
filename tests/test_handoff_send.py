"""`ddflow suggest`'s input half: `resolve_patch` turns --commit/--diff/--blob into a patch
and a base, and `suggest` refuses a wrong shape before `events.propose` runs."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ddflow.services.handoff.send import SuggestError, resolve_patch


def _commit(repo: Path, path: str, text: str, msg: str) -> str:
    p = repo / path
    p.write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", path], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", msg], check=True)
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


_DIFF = """diff --git a/README.md b/README.md
index 1234567..89abcde 100644
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-# proj
+# proj, suggested
"""


def test_resolve_patch_requires_exactly_one_input():
    with pytest.raises(SuggestError, match="exactly one"):
        resolve_patch(Path("."))
    with pytest.raises(SuggestError, match="exactly one"):
        resolve_patch(Path("."), diff="a", blob="b")


def test_resolve_patch_reads_a_diff_file(tmp_path):
    f = tmp_path / "change.diff"
    f.write_text(_DIFF)
    patch, base = resolve_patch(Path("."), diff=str(f))
    assert patch == _DIFF and base == ""


def test_resolve_patch_reads_a_blob(tmp_path):
    f = tmp_path / "change.patch"
    f.write_text(_DIFF)
    patch, base = resolve_patch(Path("."), blob=str(f))
    assert patch == _DIFF and base == ""


def test_resolve_patch_from_a_commit(repo):
    sha = _commit(repo, "a.txt", "one\n", "add a")
    patch, base = resolve_patch(repo, commit=sha)
    assert patch.startswith("diff --git a/a.txt b/a.txt")
    assert (
        base
        == subprocess.run(
            ["git", "-C", str(repo), "rev-parse", f"{sha}^"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )


def test_resolve_patch_refuses_a_non_patch_file(tmp_path):
    f = tmp_path / "not-a-diff.txt"
    f.write_text("just some text, not a git diff")
    with pytest.raises(SuggestError, match="diff --git"):
        resolve_patch(Path("."), diff=str(f))
