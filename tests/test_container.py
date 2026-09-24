"""Container behaviour. The failures here are silent and one of them loses work."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from orchard import container as CT
from orchard import worktree as W
from orchard.config import Config

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def as_container(monkeypatch):
    monkeypatch.setenv("ORCHARD_IN_CONTAINER", "1")
    return True


def test_detection_requires_positive_evidence(monkeypatch):
    """A false positive relocates worktrees on a host, so absence of evidence is not
    evidence of a container."""
    monkeypatch.delenv("ORCHARD_IN_CONTAINER", raising=False)
    monkeypatch.setattr(CT.Path, "exists", lambda self: False)
    monkeypatch.setattr(CT.Path, "read_text", lambda self, *a, **k: "0::/user.slice")
    assert CT.in_container() is False


def test_a_sibling_worktree_root_is_relocated_inside_the_repo(as_container):
    """THE data-loss case: only the repo is bind-mounted, so `../x` lands on the
    container's ephemeral layer and is destroyed on exit with the work inside it."""
    assert CT.default_worktree_root("../.orchard-worktrees") == ".orchard-worktrees"


def test_an_explicit_inside_root_is_left_alone(as_container):
    assert CT.default_worktree_root("build/wt") == "build/wt"


def test_an_absolute_root_is_left_alone(as_container):
    """Assumed to be a deliberate mount; `warnings()` says so rather than overriding."""
    assert CT.default_worktree_root("/mnt/worktrees") == "/mnt/worktrees"


def test_nothing_is_relocated_outside_a_container(monkeypatch):
    monkeypatch.delenv("ORCHARD_IN_CONTAINER", raising=False)
    monkeypatch.setattr(CT, "in_container", lambda: False)
    assert CT.default_worktree_root("../.orchard-worktrees") == "../.orchard-worktrees"


@pytest.mark.parametrize(
    "url,want",
    [
        ("http://127.0.0.1:8000/v1", f"http://{CT.HOST_ALIAS}:8000/v1"),
        ("http://localhost:11434/v1", f"http://{CT.HOST_ALIAS}:11434/v1"),
        ("http://10.0.0.5:8000/v1", "http://10.0.0.5:8000/v1"),
    ],
)
def test_loopback_endpoints_are_pointed_at_the_host(as_container, url, want):
    assert CT.rewrite_localhost(url) == want


def test_warnings_name_the_linux_add_host_requirement(as_container, repo):
    run_cli(repo, "init")
    run_cli(
        repo,
        "config",
        "--append-toml",
        '[[reviewer]]\nname = "local"\nbase_url = "http://127.0.0.1:8000/v1"\n'
        'model = "qwen"\nfamily = "alibaba"\ngates = ["critic"]\n',
    )
    msgs = " ".join(CT.warnings(repo, Config.load(repo)))
    assert "add-host" in msgs and CT.HOST_ALIAS in msgs


# -- path portability: the requirement for multiple users on one repo -----------------


def test_worktree_paths_are_stored_relative_to_the_repo(repo, cfg):
    """The event log is COMMITTED and shared. An absolute path is true only on the
    machine that wrote it — false for a teammate who cloned elsewhere, for CI, and for
    a container where the repo is /repo and nothing else on the host is."""
    wt = W.create(repo, cfg, "T1")
    stored = W.store_path(repo, wt.path)
    assert not os.path.isabs(stored), f"an absolute path would be stored: {stored}"
    assert W.load_path(repo, stored).resolve() == wt.path.resolve()


def test_an_absolute_stored_path_is_still_honoured(repo):
    """Logs written by earlier versions contain them; reinterpreting one as relative
    would point recovery at a directory that does not exist, and report 'nothing to
    salvage' over real work."""
    assert W.load_path(repo, "/tmp/somewhere") == Path("/tmp/somewhere")


def test_the_event_log_carries_no_absolute_paths(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "p")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "src/*")
    run_cli(repo, "claim", "P1.T1")
    log = "\n".join(p.read_text() for p in (repo / ".orchard" / "events").glob("*.jsonl"))
    entries = [json.loads(ln) for ln in log.splitlines() if ln.strip()]
    for ev in entries:
        for key in ("worktree", "path"):
            val = ev.get("data", {}).get(key, "")
            assert not (isinstance(val, str) and val.startswith("/")), (
                f"{ev['kind']} recorded the absolute path {val!r}; the committed log "
                f"would be wrong on every other checkout"
            )


def test_adopt_gitignores_an_in_repo_worktree_root(repo):
    """Inside a container the root IS in the repo; unignored it shows up as hundreds of
    untracked files and the enforcement hook trips over every one."""
    run_cli(repo, "adopt", "--agents", "claude")
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "check-ignore", "-q", ".orchard-worktrees/T1"]
        ).returncode
        == 0
    )


def test_docker_launch_config_is_stdio_safe(repo):
    """`-t` would allocate a TTY and inject control sequences into a JSON-RPC stream."""
    run_cli(repo, "adopt", "--agents", "claude", "--launch", "docker")
    args = json.loads((repo / ".mcp.json").read_text())["mcpServers"]["orchard"]["args"]
    assert args[0] == "run" and "-i" in args
    assert "-t" not in args and "-it" not in args, "a TTY corrupts the MCP stream"
    assert "--rm" in args
    assert any(a.endswith(":/repo") for a in args), "the repo is not mounted"
    assert any("host-gateway" in a for a in args), (
        "without --add-host, a host-served reviewer endpoint is unreachable on Linux"
    )


# -- the real thing, if docker is available -------------------------------------------

DOCKER = shutil.which("docker") is not None


@pytest.mark.skipif(not DOCKER, reason="docker is not installed")
@pytest.mark.slow
def test_the_image_builds_and_serves_mcp(tmp_path):
    img = "orchard:pytest"
    build = subprocess.run(
        ["docker", "build", "-q", "-t", img, str(ROOT)],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    assert build.returncode == 0, f"docker build failed:\n{build.stderr[-2000:]}"

    repo = tmp_path / "proj"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "a.txt").write_text("x\n")
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
        check=True,
    )

    msg = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            }
        )
        + "\n"
    )
    r = subprocess.run(
        ["docker", "run", "-i", "--rm", "-v", f"{repo}:/repo", img],
        input=msg,
        capture_output=True,
        text=True,
        timeout=600,
    )
    reply = json.loads(r.stdout.splitlines()[0])
    assert reply["result"]["serverInfo"]["name"] == "orchard"

    # Files the container creates must belong to the host user, not root.
    subprocess.run(
        ["docker", "run", "-i", "--rm", "-v", f"{repo}:/repo", img, "orchard", "init"],
        capture_output=True,
        timeout=600,
    )
    cfg = repo / ".orchard" / "config.toml"
    assert cfg.is_file()
    assert cfg.stat().st_uid == os.getuid(), (
        "the container wrote root-owned files into the host repo; the operator would "
        "need sudo to edit their own project"
    )


def test_the_json_interface_returns_a_usable_absolute_path(repo):
    """Storage portable, interface usable.

    The log stores a relative path so a committed log is true on every checkout; but a
    caller handed ".orchard-worktrees/T1" resolves it against its own cwd, which is
    frequently not the repo root. Found by the demo scenarios, which `cd` into what
    `show --json` returns.
    """
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "p")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "src/*")
    run_cli(repo, "claim", "P1.T1")

    shown = json.loads(run_cli(repo, "--json", "show", "P1.T1")[1])
    assert os.path.isabs(shown["worktree"]), shown["worktree"]
    assert Path(shown["worktree"], ".git").exists(), "not a usable worktree path"
    assert os.path.isabs(shown["lease"]["worktree"])

    # ...while the LOG still carries the portable form.
    log = "\n".join(p.read_text() for p in (repo / ".orchard" / "events").glob("*.jsonl"))
    assert '"worktree":"/' not in log.replace(" ", "")
