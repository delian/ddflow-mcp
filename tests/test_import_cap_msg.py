"""An import over the task cap says it refused, and does not also say nothing was found.

The file WAS recognised; the cap is what stopped it. Headlining "Nothing to import" and
adding "Nothing recognisable was found... not necessarily wrong" right after the refusal
tells the operator the opposite of what happened.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def _over_cap(repo: Path) -> None:
    (repo / "TODO.md").write_text(
        "# Big\n\n## Everything\n\n" + "\n".join(f"- [ ] item {n}" for n in range(300)) + "\n"
    )


def test_over_cap_import_does_not_claim_nothing_was_found(repo):
    _over_cap(repo)
    run_cli(repo, "init")
    _code, out, err = run_cli(repo, "import", "--max-tasks", "50")
    text = out + err
    assert "REFUSING" in text, text
    assert "Nothing to import" not in text, text
    assert "Nothing recognisable" not in text, text


def test_over_cap_import_still_exits_nothing_to_apply(repo):
    _over_cap(repo)
    run_cli(repo, "init")
    code, _, _ = run_cli(repo, "import", "--max-tasks", "50")
    assert code == 2


def test_empty_repo_import_still_says_nothing_found(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "import")
    assert code == 2
    assert "Nothing to import" in out + err
    assert "Nothing recognisable" in out + err
