"""Bug Ba11a054309: doctor said Healthy while an adopted project's driver docs lagged the
templates the running ddflow ships; the only refresh was a full `adopt`, which also
rewrites MCP launches, hooks and command files."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ddflow.services import adopt as AD

ROOT = Path(__file__).resolve().parents[1]
DRIVER = "docs/ddflow/drivers/implement-phase.md"
DELTA = "docs/ddflow/drivers/deltas/claude-code.md"


def _ddflow(cwd: Path, *argv: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    env.pop("DDFLOW_AGENT", None)
    return subprocess.run(
        [sys.executable, "-m", "ddflow", *argv],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )


def _adopted(repo: Path) -> None:
    p = _ddflow(repo, "adopt", "--agents", "claude", "--launch", "python")
    assert p.returncode == 0, p.stderr


def _lag(repo: Path) -> None:
    for rel in (DRIVER, DELTA):
        path = repo / rel
        path.write_text(
            "\n".join(ln for ln in path.read_text().splitlines() if "wait" not in ln) + "\n"
        )


def test_doctor_names_lagging_driver_docs(repo):
    _adopted(repo)
    clean = _ddflow(repo, "doctor")
    assert "driver" not in clean.stdout.lower(), clean.stdout
    _lag(repo)
    p = _ddflow(repo, "doctor")
    assert DRIVER in p.stdout and DELTA in p.stdout, p.stdout
    assert "adopt --refresh-docs" in p.stdout


def test_refresh_docs_rewrites_only_docs_and_rules(repo):
    _adopted(repo)
    keep = {
        rel: (repo / rel).read_bytes() for rel in (".mcp.json", ".claude/commands/implement.md")
    }
    hooks = sorted(p for p in (repo / ".git" / "hooks").glob("*") if p.is_file())
    keep.update({str(h): h.read_bytes() for h in hooks})
    _lag(repo)
    agents = repo / "AGENTS.md"
    agents.write_text(agents.read_text().replace("Claim before you edit", "Claim sometime"))
    p = _ddflow(repo, "adopt", "--refresh-docs")
    assert p.returncode == 0, p.stderr + p.stdout
    assert DRIVER in p.stdout and DELTA in p.stdout
    templates = ROOT / "ddflow" / "templates" / "drivers"
    assert (repo / DRIVER).read_bytes() == (templates / "implement-phase.md").read_bytes()
    assert (repo / DELTA).read_bytes() == (templates / "deltas" / "claude-code.md").read_bytes()
    assert "Claim before you edit" in agents.read_text()
    for rel, data in keep.items():
        path = Path(rel) if os.path.isabs(rel) else repo / rel
        assert path.read_bytes() == data, f"{rel} must not be touched by --refresh-docs"
    assert AD.driver_drift(repo) == []
    assert "driver" not in _ddflow(repo, "doctor").stdout.lower()


def test_refresh_docs_does_not_adopt_an_unadopted_project(repo):
    p = _ddflow(repo, "adopt", "--refresh-docs")
    assert p.returncode != 0
    assert "has not been adopted" in p.stderr + p.stdout
    assert not (repo / "docs" / "ddflow").exists()
    assert not (repo / ".mcp.json").exists()


def test_refresh_docs_from_a_linked_worktree_writes_there(repo):
    (repo / "README.md").write_text("x\n")
    _adopted(repo)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "adopt"], check=True)
    tree = repo.parent / "harness"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", "-b", "h", str(tree)], check=True
    )
    _lag(tree)
    p = _ddflow(tree, "adopt", "--refresh-docs")
    assert p.returncode == 0, p.stderr
    templates = ROOT / "ddflow" / "templates" / "drivers"
    assert (tree / DRIVER).read_bytes() == (templates / "implement-phase.md").read_bytes()
    assert (repo / DRIVER).read_bytes() != b""
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--", "docs", "AGENTS.md"],
        capture_output=True,
        text=True,
    ).stdout
    assert status == "", "the primary must stay clean"
