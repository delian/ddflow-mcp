"""Enforcement: the layer that does not depend on the agent agreeing.

The distinction these tests protect: the MCP instructions and the rules file are
*persuasion*, and only the git hook is *enforcement*. A test suite that checked the
wording of the instructions and called that "the workflow is enforced" would be
measuring the wrong thing entirely.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from orchard import enforce as E


def _commit(repo: Path, message: str, *, agent: str = "", paths: list[str] | None = None):
    if paths:
        subprocess.run(["git", "-C", str(repo), "add", *paths], check=True)
    env = {**os.environ}
    if agent:
        env["ORCHARD_AGENT"] = agent
    return subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", message],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )


@pytest.fixture
def enforced(repo):
    run_cli(repo, "adopt", "--agents", "claude")
    (repo / ".orchard" / "config.toml").write_text('[enforce]\ncommit_without_lease = "block"\n')
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "a.py").write_text("x = 1\n")
    (repo / "src" / "b.py").write_text("y = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "scaffold", "--no-verify"], check=True)
    run_cli(repo, "phase", "add", "P1", "--title", "Core")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "src/a.py")
    run_cli(repo, "task", "add", "P1.T2", "--phase", "P1", "--globs", "src/b.py")
    return repo


def test_adopt_installs_the_hook(repo):
    run_cli(repo, "adopt", "--agents", "claude")
    assert E.installed(repo), "adopt did not install the enforcement hook"


def test_an_unclaimed_commit_is_refused(enforced):
    (enforced / "src" / "a.py").write_text("x = 2\n")
    r = _commit(enforced, "unclaimed", paths=["src/a.py"])
    assert r.returncode != 0, "the hook allowed an unclaimed commit"
    assert "not covered by a lease you hold" in r.stderr
    assert "orchard claim" in r.stderr, "the refusal must name the remedy"


def test_a_claimed_commit_is_allowed(enforced):
    run_cli(enforced, "claim", "P1.T1", "--no-worktree", agent="alpha")
    (enforced / "src" / "a.py").write_text("x = 2\n")
    r = _commit(enforced, "claimed", agent="alpha", paths=["src/a.py"])
    assert r.returncode == 0, f"the hook refused a properly claimed commit:\n{r.stderr}"


def test_committing_another_agents_file_names_the_holder(enforced):
    run_cli(enforced, "claim", "P1.T1", "--no-worktree", agent="alpha")
    run_cli(enforced, "claim", "P1.T2", "--no-worktree", agent="beta")
    (enforced / "src" / "b.py").write_text("y = 9\n")
    r = _commit(enforced, "steal", agent="alpha", paths=["src/b.py"])
    assert r.returncode != 0
    assert "beta" in r.stderr and "STOP" in r.stderr, (
        "taking another agent's file must be called out distinctly from simply "
        f"forgetting to claim:\n{r.stderr}"
    )


def test_orchards_own_bookkeeping_needs_no_lease(enforced):
    """The event log records the lease, so requiring a lease to commit it is a deadlock."""
    (enforced / ".orchard" / "notes.txt").write_text("x\n")
    r = _commit(enforced, "orchard state", paths=[".orchard"])
    assert r.returncode == 0, f"could not commit Orchard's own state:\n{r.stderr}"


def test_warn_mode_reports_but_allows(repo):
    run_cli(repo, "adopt", "--agents", "claude")
    (repo / ".orchard" / "config.toml").write_text('[enforce]\ncommit_without_lease = "warn"\n')
    (repo / "f.py").write_text("x\n")
    r = _commit(repo, "unclaimed but warn only", paths=["f.py"])
    assert r.returncode == 0
    assert "warning only" in r.stderr


def test_off_mode_says_nothing(repo):
    run_cli(repo, "adopt", "--agents", "claude")
    (repo / ".orchard" / "config.toml").write_text('[enforce]\ncommit_without_lease = "off"\n')
    (repo / "f.py").write_text("x\n")
    r = _commit(repo, "unclaimed, enforcement off", paths=["f.py"])
    assert r.returncode == 0
    assert "not covered by a lease" not in r.stderr


def test_install_refuses_to_clobber_a_foreign_hook(repo):
    hooks = E.hooks_dir(repo)
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-commit").write_text("#!/bin/sh\necho someone elses hook\n")
    msg = E.install(repo)
    assert msg.startswith("REFUSED")
    assert "someone elses hook" in (hooks / "pre-commit").read_text()
    # ...and says how to combine them by hand.
    assert "hooks check-commit" in msg
    assert E.install(repo, force=True).startswith("installed")


def test_uninstall_leaves_a_foreign_hook_alone(repo):
    hooks = E.hooks_dir(repo)
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-commit").write_text("#!/bin/sh\necho mine\n")
    assert E.uninstall(repo).startswith("REFUSED")
    assert (hooks / "pre-commit").exists()


def test_hooks_status_reports_a_policy_with_no_hook(repo):
    run_cli(repo, "init")
    (repo / ".orchard" / "config.toml").write_text('[enforce]\ncommit_without_lease = "block"\n')
    _code, out, _ = run_cli(repo, "hooks", "status")
    assert "NOT installed" in out
    assert "nothing" in out.lower() and "enforces" in out.lower(), (
        "a block policy with no hook installed enforces NOTHING and must say so"
    )


# -- the persuasion layer, tested as persuasion ---------------------------------------


def _instructions(repo: Path) -> str:
    from orchard.mcp_server import serve

    out = io.StringIO()
    serve(
        repo,
        stdin=io.StringIO(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18"},
                }
            )
            + "\n"
        ),
        stdout=out,
    )
    return json.loads(out.getvalue())["result"]["instructions"]


def test_an_unadopted_repo_is_told_to_run_setup(repo):
    text = _instructions(repo)
    assert "does not use Orchard yet" in text
    assert "orchard_setup" in text
    # On INTENT, not on a phrase. This text is now
    # `templates/prompts/mcp_instructions.md`, which a project may rewrite wholesale —
    # so pinning the exact wording would make an operator's edit read as a regression.
    # What must survive any rewording is the instruction: do not start talking about a
    # work queue nobody asked for.
    low = text.lower()
    assert "has not asked" in low and ("carry on" in low or "say nothing" in low), (
        f"an unadopted repo must not be nagged into adopting:\n{text[-300:]}"
    )


def test_an_adopted_repo_names_its_remaining_setup_gaps(repo):
    run_cli(repo, "adopt", "--agents", "claude")
    text = _instructions(repo)
    assert "orchard_brief" in text and "Claim before you edit" in text
    assert "No test command is configured" in text
    assert "No cross-family reviewer is configured" in text


def test_a_fully_configured_repo_gets_no_setup_nagging(repo):
    run_cli(repo, "adopt", "--agents", "claude")
    assert run_cli(repo, "config", "--set", "gate.unit_tests.command", "pytest -q")[0] == 0
    assert (
        run_cli(
            repo,
            "config",
            "--append-toml",
            '[[reviewer]]\nname = "r"\nbase_url = "http://x/v1"\n'
            'model = "qwen-x"\nfamily = "alibaba"\ngates = ["critic"]\n',
        )[0]
        == 0
    )
    text = _instructions(repo)
    assert "Setup still needed" not in text, text[-400:]


# -- the out-of-box path: no ORCHARD_AGENT, no --agent, anywhere ----------------------


def test_the_default_identity_is_stable_across_processes(repo):
    """`host-pid` was stable for exactly ONE process.

    So `orchard claim` and the `git commit` hook that ran seconds later were different
    agents: the hook refused the holder's own commit AND reported that the lease
    belonged to somebody else. Out of the box, the enforcement layer rejected correct
    behaviour and blamed the user for it.
    """
    import subprocess as sp

    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    env.pop("ORCHARD_AGENT", None)
    code = (
        "import sys;sys.path.insert(0,'.');"
        "from orchard.events import default_agent_id;"
        "print(default_agent_id(" + repr(str(repo)) + "))"
    )
    a = sp.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120
    ).stdout.strip()
    b = sp.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120
    ).stdout.strip()
    assert a == b and a, f"identity differs between processes: {a!r} vs {b!r}"
    assert str(os.getpid()) not in a, "the pid leaked into the identity"


def test_a_claimed_commit_succeeds_with_no_agent_configured_anywhere(repo):
    """The out-of-box experience. No --agent, no ORCHARD_AGENT, nothing."""
    run_cli(repo, "adopt", "--agents", "claude")
    run_cli(repo, "config", "--set", "enforce.commit_without_lease", "block")
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "a.py").write_text("x = 1\n")
    (repo / "src" / "b.py").write_text("y = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "scaffold", "--no-verify"], check=True)
    run_cli(repo, "phase", "add", "P1", "--title", "c")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "src/a.py")
    run_cli(repo, "claim", "P1.T1", "--no-worktree")

    (repo / "src" / "a.py").write_text("x = 2\n")
    env = {k: v for k, v in os.environ.items() if k != "ORCHARD_AGENT"}
    r = subprocess.run(["git", "-C", str(repo), "add", "src/a.py"], check=True)
    r = subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "legit"],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    assert r.returncode == 0, f"the hook refused the holder's OWN commit:\n{r.stderr}"

    # ...and an unclaimed path is still refused.
    (repo / "src" / "b.py").write_text("y = 2\n")
    subprocess.run(["git", "-C", str(repo), "add", "src/b.py"], check=True)
    r = subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "unclaimed"],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    assert r.returncode != 0, "an unclaimed path was allowed through"


def test_a_lease_is_mine_if_it_created_the_tree_i_am_committing_in(repo, cfg):
    """The robust rule: the hook runs inside a worktree, and the lease that produced
    that worktree is the relevant claim whatever identity string made it. Without this,
    claiming from the primary checkout and committing inside the worktree disagree."""
    from orchard import lease as L
    from orchard import worktree as W
    from orchard.events import EventLog

    run_cli(repo, "adopt", "--agents", "claude")
    run_cli(repo, "config", "--set", "enforce.commit_without_lease", "block")
    run_cli(repo, "phase", "add", "P1", "--title", "c")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "src/*")
    wt = W.create(repo, cfg, "P1.T1")
    log = EventLog(repo, "some-other-identity")
    L.acquire(
        log,
        cfg,
        "P1.T1",
        holder="some-other-identity",
        worktree=W.store_path(repo, wt.path),
        branch=wt.branch,
        globs=["src/*"],
    )

    (wt.path / "src").mkdir(exist_ok=True)
    (wt.path / "src" / "new.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(wt.path), "add", "-A"], check=True)
    env = {k: v for k, v in os.environ.items() if k != "ORCHARD_AGENT"}
    r = subprocess.run(
        ["git", "-C", str(wt.path), "commit", "-m", "in my worktree"],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    assert r.returncode == 0, (
        f"the hook refused a commit inside the very worktree the lease created:\n{r.stderr}"
    )
