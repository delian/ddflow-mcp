import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchard.config import Config
from orchard.events import EventLog


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repository. Real, not mocked: every worktree, lease-recovery and
    merge rule in this system is a statement about git's actual behaviour, and a mock
    would let those statements be wrong while the tests stayed green."""
    r = tmp_path / "proj"
    r.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    for k, v in (
        ("user.email", "t@example.com"),
        ("user.name", "Test"),
        ("commit.gpgsign", "false"),
    ):
        subprocess.run(["git", "-C", str(r), "config", k, v], check=True)
    (r / "README.md").write_text("# proj\n")
    subprocess.run(["git", "-C", str(r), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(r), "commit", "-qm", "init"], check=True)
    return r


@pytest.fixture
def cfg() -> Config:
    return Config.load()


@pytest.fixture
def log(repo: Path) -> EventLog:
    return EventLog(repo, "agent-test")


def run_cli(repo: Path, *argv: str, agent: str = "") -> tuple[int, str, str]:
    """Invoke the CLI as a real subprocess, as an agent would."""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    args = [sys.executable, "-m", "orchard", "--repo", str(repo)]
    if agent:
        args += ["--agent", agent]
    p = subprocess.run([*args, *argv], capture_output=True, text=True, env=env, timeout=300)
    return p.returncode, p.stdout, p.stderr
