import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchard.config import Config
from orchard.infra.log import EventLog


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


#: Reviewer gates need a model from a family other than the author's, or
#: `agent.reviewer_family_must_differ` refuses the completion.
_CROSS_FAMILY_REVIEWER = "gemini-2.5-pro"


def pass_pipeline(
    repo: Path,
    item: str,
    *,
    reviewer: str = _CROSS_FAMILY_REVIEWER,
    omit: tuple[str, ...] = (),
) -> None:
    """Record a passing outcome for every gate in ``item``'s CONFIGURED pipeline.

    Derived from `gate status`, not from a hardcoded list, so a project that trims or
    reorders its pipeline does not silently stop being exercised here — the same
    mistake `render.board` once made by re-typing the ten default gate ids.

    Exists because `gates.require_outcome` makes silence block completion: a test that
    only wants a finished item, as a fixture for something else, should not have to
    restate the whole quality pipeline to get one.
    """
    import json as _json

    code, out, err = run_cli(repo, "--json", "gate", "status", item)
    assert code == 0, f"gate status {item}: {out}{err}"
    for gate in _json.loads(out)["pipeline"]:
        if gate in omit:
            continue
        extra = ["--evidence", f"{gate} evidence"]
        if gate in ("rubber_duck", "critic"):
            extra += ["--model", reviewer]
        run_cli(repo, "gate", "record", item, gate, "--outcome", "passed", *extra)


def finish(repo: Path, item: str, *args: str, model: str = "claude-opus-5") -> tuple[int, str, str]:
    """Pass the whole pipeline and complete the item. Returns complete's result."""
    pass_pipeline(repo, item)
    return run_cli(repo, "complete", item, "--model", model, *args)
