"""pre-commit as a companion: ddflow proposes a configuration for THIS repository.

Operator, 2026-09-28: "propose to the agent as a companion pre-commit with good
configurations that are relevant for the project". The proposal is read from the stacks
the repository actually has, pinned, and carries ddflow's own checks as local hooks so
the framework and ddflow do not fight over `.git/hooks/` (research Rb5e33fdbf9).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ddflow.api import operations as OPS
from ddflow.services import companions as C
from ddflow.services import precommit as PC

REFUSED = 3


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def mixed(repo):
    files = {
        "pyproject.toml": '[project]\nname = "x"\n',
        "src/app.py": "def f():\n    return 1\n",
        "scripts/run.sh": "#!/bin/sh\necho hi\n",
        "Dockerfile": "FROM python:3.13-slim\n",
        "web/package.json": "{}\n",
        "package.json": json.dumps({"devDependencies": {"eslint": "^9"}}),
        "config.yaml": "a: 1\n",
    }
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "mixed")
    return repo


def _hooks(p: PC.Proposal) -> dict[str, list[str]]:
    return {r.url: [h.id for h in r.hooks] for r in p.repos}


def test_the_stacks_are_read_from_the_repository_not_assumed():
    got = PC.detect_stacks(["a/b.py", "Dockerfile", "x/run.sh", "go.mod", "README.md"])
    assert set(got) == {"python", "docker", "shell", "go"}
    assert PC.detect_stacks(["README.md", "docs/a.md"]) == {}


def test_a_mixed_repository_gets_the_hooks_for_each_of_its_stacks(mixed):
    p = PC.propose(mixed)
    hooks = _hooks(p)
    assert {"python", "shell", "docker", "javascript"} <= set(p.stacks)
    assert hooks["https://github.com/astral-sh/ruff-pre-commit"] == ["ruff-check", "ruff-format"]
    assert hooks["https://github.com/shellcheck-py/shellcheck-py"] == ["shellcheck"]
    assert hooks["https://github.com/hadolint/hadolint"] == ["hadolint-docker"]
    assert "check-yaml" in hooks["https://github.com/pre-commit/pre-commit-hooks"]
    assert "check-json" in hooks["https://github.com/pre-commit/pre-commit-hooks"]
    assert hooks["https://github.com/gitleaks/gitleaks"] == ["gitleaks"]
    local = hooks["local"]
    assert "eslint" in local, "package.json declares it"
    assert "prettier" not in local and any("prettier" in s for s in p.skipped), (
        "a tool the project does not declare is not invented -- and the gap is said"
    )
    assert local[-2:] == ["ddflow-check-commit", "ddflow-check-msg"]


def test_a_python_only_repository_gets_no_other_stacks_tools(repo):
    (repo / "m.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "py")
    hooks = _hooks(PC.propose(repo))
    assert "https://github.com/hadolint/hadolint" not in hooks
    assert "https://github.com/shellcheck-py/shellcheck-py" not in hooks
    assert "check-json" not in hooks["https://github.com/pre-commit/pre-commit-hooks"]


def test_every_remote_hook_is_pinned(mixed):
    for repo in PC.propose(mixed).repos:
        if repo.url != "local":
            assert repo.rev.startswith("v"), f"{repo.url} is unpinned"
            assert f"rev: {repo.rev}" in PC.render(PC.propose(mixed))


def test_ddflows_own_checks_run_inside_the_framework(mixed):
    text = PC.propose(mixed, ddflow_cmd="uvx --from ddflow-mcp ddflow").text
    assert "default_install_hook_types: [pre-commit, commit-msg]" in text
    assert 'entry: "uvx --from ddflow-mcp ddflow hooks check-commit"' in text
    assert 'stages: ["commit-msg"]' in text


@pytest.mark.skipif(shutil.which("pre-commit") is None, reason="pre-commit is not installed")
def test_the_proposal_is_a_config_pre_commit_itself_accepts(mixed, tmp_path):
    cfg = tmp_path / "proposed.yaml"
    cfg.write_text(PC.propose(mixed).text)
    r = subprocess.run(["pre-commit", "validate-config", str(cfg)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_write_creates_the_file_and_never_replaces_one(mixed):
    out = OPS.precommit(mixed, where=mixed, write=True)
    assert out.exit == 0 and out.data["written"] is True
    path = mixed / ".pre-commit-config.yaml"
    assert path.read_text() == out.data["text"]
    path.write_text("repos: []  # the operator's own\n")
    again = OPS.precommit(mixed, where=mixed, write=True)
    assert again.exit == REFUSED and again.data["written"] is False
    assert path.read_text() == "repos: []  # the operator's own\n"


def test_the_proposal_alone_writes_nothing(mixed):
    out = OPS.precommit(mixed, where=mixed)
    assert out.exit == 0 and out.data["exists"] is False
    assert not (mixed / ".pre-commit-config.yaml").exists()


def test_the_catalogue_recommends_it():
    comp = {c.id: c for c in C.load(Path(__file__).resolve().parents[1])}["pre-commit"]
    assert comp.kind == "cli" and "standards" in comp.gates and comp.default is True
    assert "ddflow precommit" in comp.note, "the recommendation names how to get the config"
