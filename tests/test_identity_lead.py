"""A harness identity record is scoped to the agent SESSION that declared it (B015c4932be).

The shared tree bug: records in `.git/ddflow-identity` are keyed by process pid alone,
and a subagent's `ddflow_identify` on a legacy connection records the SHARED harness
pid both it and the coordinator's own shells descend from. The coordinator's shells
then walk their ancestry, find the last agent that identified there (impl-ra), and its
approval requests, gate skips and claims are bound to it.

A record now carries the declaring process's harness-session fingerprint. A caller
takes a record only when it carries the SAME fingerprint; an untagged record (one
written before this change, or where no session was known) keeps matching everyone,
so an upgraded harness does not silently split an agent's declaration from its shell.
The coordinator -- a different session, or none -- skips the record and keeps its
derived identity for the whole session; an agent's own shell keeps the declared name.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.infra import harness_identity as H

needs_proc = pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="reads /proc")


def _write(repo: Path, pid: int, agent: str, scope: str = "") -> Path:
    """A record for `pid` as `declare` writes it: the name, and an optional scope line."""
    d = repo / ".git" / H.DIR
    d.mkdir(exist_ok=True)
    f = d / f"{pid}-{H._stat(pid)[2]}"
    scope_line = H.scope_marker(scope) + "\n" if scope else ""
    f.write_text(agent + "\n" + scope_line)
    return f


@needs_proc
def test_a_scoped_record_is_not_inherited_by_a_different_session(repo):
    f = _write(repo, os.getppid(), "impl-ra", scope="s-found-19")
    try:
        assert H.declared(repo, scope="s-other-01") == "", "another session inherited it"
        assert H.declared(repo, scope="s-found-19") == "impl-ra", "the declarer's own does"
        assert H.declared(repo, scope="") == "", "a session-less caller is not poisoned either"
        assert H.declared(repo) == "", "a pre-scope caller skips it"
    finally:
        f.unlink()


@needs_proc
def test_a_scoped_caller_still_inherits_an_untagged_record(repo):
    """Pre-scope records apply to everyone: an upgrade must not split the shell off."""
    f = _write(repo, os.getppid(), "legacy-agent")
    try:
        assert H.declared(repo, scope="s-old-7") == "legacy-agent"
        assert H.declared(repo) == "legacy-agent"
    finally:
        f.unlink()


@needs_proc
def test_declare_tags_the_record_and_liveness_reads_the_name(repo):
    f = _write(repo, os.getppid(), "dec-x", scope="s-old-1")
    try:
        assert H.declare(repo, "impl-ra", scope="s-live-2") == ""
        assert H.declared(repo, scope="s-live-2") == "impl-ra"
        assert H.declared(repo, scope="s-live-1") == ""
        assert H.declared(repo) == ""
        alive, pids = H.liveness(repo, "impl-ra")
        assert alive is True and pids == [os.getppid()], "the scope line must not hide the name"
    finally:
        f.unlink()
        H.declare(repo, "")


@needs_proc
def test_the_cli_identifies_derived_under_a_foreign_sessions_record(repo):
    """End to end: a subagent declared on a shared ancestor pid; a shell of another
    session running `ddflow identify` must answer derived, never the record's name."""
    f = _write(repo, os.getpid(), "impl-ra", scope="s-impl-3")
    try:
        r = subprocess.run(
            [sys.executable, "-m", "ddflow", "--repo", str(repo), "identify", "--json"],
            capture_output=True,
            text=True,
            env={**{k: v for k, v in os.environ.items() if k != "DDFLOW_AGENT"}},
            timeout=120,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "impl-ra" not in r.stdout, r.stdout
        assert "derived" in r.stdout, r.stdout
    finally:
        f.unlink()
