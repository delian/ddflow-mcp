"""A gate or CI command does not inherit the caller's identity (B3a8e04ca38).

A shell with `DDFLOW_AGENT` exported ran the project's test command with it, and three
`test_clone_id_recovery` tests that read the caller's identity failed in the gate while
passing without it. `[gates].scrub_env` removes the identity variables from the child's
environment, `[gates].keep_env` / `[ci].keep_env` preserve some.
"""

from __future__ import annotations

import subprocess

from conftest import run_cli

from ddflow.config import Config
from ddflow.services import ci as CI
from ddflow.services import gates as G
from ddflow.services.gateenv import child_env

SHOW = 'printf "[%s|%s|%s]" "${DDFLOW_AGENT-unset}" "${DDFLOW_SESSION_ID-unset}" "${KEEPME-unset}"'


def _gate(tmp_path, monkeypatch, cfg=None, **env):
    monkeypatch.setenv("DDFLOW_AGENT", "someone")
    monkeypatch.setenv("DDFLOW_SESSION_ID", "s1")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    outcome, ev = G.run_command_gate(G.GateDef(id="t", command=SHOW), tmp_path, cfg=cfg)
    assert outcome == "passed", ev
    return ev["tail"]


def test_a_gate_command_does_not_see_the_callers_identity(tmp_path, monkeypatch):
    assert "[unset|unset|unset]" in _gate(tmp_path, monkeypatch)


def test_keep_env_preserves_a_scrubbed_variable(tmp_path, monkeypatch):
    cfg = Config()
    cfg.gates.keep_env = ["DDFLOW_AGENT"]
    assert "[someone|unset|unset]" in _gate(tmp_path, monkeypatch, cfg)


def test_scrub_env_removes_a_project_variable(tmp_path, monkeypatch):
    cfg = Config()
    cfg.gates.scrub_env = [*cfg.gates.scrub_env, "KEEPME"]
    assert "[unset|unset|unset]" in _gate(tmp_path, monkeypatch, cfg, KEEPME="x")
    assert "[unset|unset|x]" in _gate(tmp_path, monkeypatch, Config(), KEEPME="x")


def test_the_gates_own_env_still_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("DDFLOW_AGENT", "someone")
    gdef = G.GateDef(id="t", command=SHOW, env={"DDFLOW_AGENT": "chosen"})
    _, ev = G.run_command_gate(gdef, tmp_path)
    assert "[chosen|" in ev["tail"]


def test_the_ci_command_does_not_see_the_callers_identity(repo, monkeypatch):
    run_cli(repo, "init")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "adopt"], check=True)
    monkeypatch.setenv("DDFLOW_AGENT", "someone")
    cfg = Config.load(repo)
    assert "[unset|" in CI.run(repo, cfg, base="HEAD", command=SHOW).output_tail
    cfg.ci.keep_env = ["DDFLOW_AGENT"]
    assert "[someone|" in CI.run(repo, cfg, base="HEAD", command=SHOW).output_tail


def test_child_env_leaves_everything_else(monkeypatch):
    assert child_env(None, {"A": "1", "DDFLOW_AGENT": "x"}) == {"A": "1"}


def test_a_machine_local_scrub_env_reaches_a_gate_run_in_the_items_worktree(repo, monkeypatch):
    """The local layer is git-ignored, so it is absent from the item's worktree: the gate
    must take the pipeline's resolved config, not re-read the worktree's."""
    import json

    run_cli(repo, "init")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "adopt"], check=True)
    local = repo / ".ddflow" / "local"
    local.mkdir(parents=True, exist_ok=True)
    (local / "config.toml").write_text('[gates]\nscrub_env = ["DDFLOW_AGENT", "DDFLOW_SESSION_ID", "KEEPME"]\n')
    (repo / ".ddflow" / "gates.toml").write_text(f"[gate.unit_tests]\ncommand = {SHOW!r}\n")
    monkeypatch.setenv("KEEPME", "x")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    code, out, err = run_cli(repo, "claim", "T1")
    assert code == 0, (out, err)
    code, out, err = run_cli(repo, "--json", "gate", "run", "T1", "unit_tests")
    assert code == 0, (out, err)
    assert "[unset|unset|unset]" in json.loads(out)["evidence"]["tail"], out
