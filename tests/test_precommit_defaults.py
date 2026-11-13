"""The language-agnostic default pre-commit set (GR-precommit-defaults).

Every project gets, whatever its stacks: secret scan, private-key headers, large files,
merge-conflict markers, case conflicts, trailing whitespace and ddflow's forbidden-trailer
check on the commit message; a detected stack adds its own formatter or linter on top."""

from __future__ import annotations

import pytest
from helpers import git_quiet as _git

from ddflow.services import precommit as PC

PRE = "https://github.com/pre-commit/pre-commit-hooks"
DEFAULTS = (
    "trailing-whitespace",
    "check-merge-conflict",
    "check-added-large-files",
    "detect-private-key",
    "check-case-conflict",
)


def _plain(repo, files):
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "files")
    return repo


def _by_repo(p):
    return {r.url: [h.id for h in r.hooks] for r in p.repos}


@pytest.mark.parametrize(
    "files",
    [
        {"notes.txt": "hello\n"},
        {"main.go": "package main\n", "go.mod": "module x\n"},
        {"Cargo.toml": "[package]\nname = 'x'\n", "src/lib.rs": "\n"},
        {"app.py": "x = 1\n"},
    ],
    ids=["no-stack", "go", "rust", "python"],
)
def test_every_project_gets_the_language_agnostic_set(repo, files):
    hooks = _by_repo(PC.propose(_plain(repo, files)))
    for want in DEFAULTS:
        assert want in hooks[PRE], want
    assert "gitleaks" in hooks["https://github.com/gitleaks/gitleaks"]
    assert "ddflow-check-msg" in hooks["local"], "forbidden trailers ride the commit-msg hook"


def test_a_detected_stack_adds_its_tools_on_top(repo):
    hooks = _by_repo(PC.propose(_plain(repo, {"app.py": "x = 1\n", "go.mod": "module x\n"})))
    assert "ruff-check" in hooks["https://github.com/astral-sh/ruff-pre-commit"]
    assert {"gofmt", "go-vet"} <= set(hooks["local"])


def test_a_stackless_project_gets_no_stack_tools(repo):
    hooks = _by_repo(PC.propose(_plain(repo, {"notes.txt": "hello\n"})))
    assert "https://github.com/astral-sh/ruff-pre-commit" not in hooks
    assert not {"gofmt", "go-vet", "cargo-fmt", "cargo-clippy"} & set(hooks["local"])


def test_the_rendered_config_carries_the_case_conflict_check(repo):
    assert "- id: check-case-conflict" in PC.propose(_plain(repo, {"a.txt": "a\n"})).text
