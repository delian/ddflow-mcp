"""Onboarding stage 6: one report that proves the harness is alive NOW.

Every check is a probe, not a reading of intent. The registered MCP entry is STARTED
and asked to list its tools; the git hooks are present and executable; the commit-msg
hook actually REFUSES a forbidden trailer; `brief` answers with something; the freeze
ratchet exists and its files are unchanged; the configured unit_tests command is green
in a detached tree. A check that could not run is `unavailable`, never a pass -- the
same rule the gates run on.
"""

from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..infra import proc as P
from ..infra import worktree as W
from . import harness as H
from . import legacy as L
from . import onboard_tests as T

#: How long the registered server may take to answer initialize + tools/list.
_HANDSHAKE_TIMEOUT = 30
#: The trailer the commit-msg probe tries to sneak past the hook.
_TRAILER = "Co-Authored-By: onboard-verify <verify@example.invalid>"
#: A message the probe expects the hook to read.
_VIOLATION = f"onboard verify probe\n\n{_TRAILER}\n"


@dataclass(frozen=True)
class Check:
    """One probe and what it found. `unavailable` is not a pass."""

    name: str
    outcome: str  #: passed | failed | unavailable
    detail: str = ""


@dataclass
class VerifyReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.outcome == "passed" for c in self.checks)

    @property
    def problems(self) -> list[Check]:
        return [c for c in self.checks if c.outcome != "passed"]

    def render(self) -> str:
        lines = [f"{c.outcome:11} {c.name}: {c.detail}" for c in self.checks]
        bad = self.problems
        lines.append(
            "everything asked for answers"
            if not bad
            else f"{len(bad)} of {len(self.checks)} check(s) did not pass: "
            + ", ".join(c.name for c in bad)
        )
        return "\n".join(lines)


def _handshake(repo: Path, entry: object) -> Check:
    """Start the REGISTERED entry and open a real MCP session with it."""
    if not isinstance(entry, dict) or not entry.get("command"):
        return Check("mcp handshake", "unavailable", "no ddflow entry registered in .mcp.json")
    argv = [str(entry["command"]), *[str(a) for a in entry.get("args") or []]]
    env = {**os.environ, **{str(k): str(v) for k, v in (entry.get("env") or {}).items()}}
    proc = P.popen(
        argv,
        cwd=repo,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        init = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "onboard-verify", "version": "0"},
                },
            }
        )
        proc.stdin.write(init + "\n")
        proc.stdin.flush()
        answer = _line(proc, _HANDSHAKE_TIMEOUT)
        if answer is None:
            _stop(proc)  # before ANY stderr read: a live pipe's read never returns
            return Check(
                "mcp handshake", "failed", f"no initialize answer in {_HANDSHAKE_TIMEOUT}s"
            )
        hello = json.loads(answer)
        if "result" not in hello:
            return Check("mcp handshake", "failed", f"initialize answered {hello.get('error')!r}")
        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        )
        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n"
        )
        proc.stdin.flush()
        listing = _line(proc, _HANDSHAKE_TIMEOUT)
        if listing is None:
            return Check("mcp handshake", "failed", "no tools/list answer")
        tools = json.loads(listing).get("result", {}).get("tools") or []
        name = (hello.get("result") or {}).get("serverInfo", {}).get("name", "?")
        return Check(
            "mcp handshake", "passed", f"{name} answered initialize and listed {len(tools)} tools"
        )
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        return Check("mcp handshake", "failed", f"the session broke: {exc}")
    finally:
        _stop(proc)


def _line(proc: subprocess.Popen, timeout: float) -> str | None:
    readable, _, _ = select.select([proc.stdout], [], [], timeout)
    if not readable:
        return None
    return proc.stdout.readline()


def _stop(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _hooks_dir(repo: Path) -> Path:
    """Where git ACTUALLY runs hooks: a linked worktree's .git is a file, and
    core.hooksPath can point anywhere (rubber_duck on 11e2bc14)."""
    r = W.git(repo, "rev-parse", "--git-path", "hooks")
    if r.ok and r.out.strip():
        path = Path(r.out.strip())
        return path if path.is_absolute() else (repo / path).resolve()
    return repo / ".git" / "hooks"


def _hooks(repo: Path) -> Check:
    """Both git hooks present, executable, and carrying ddflow's invocation."""
    missing = []
    for name in ("pre-commit", "commit-msg"):
        path = _hooks_dir(repo) / name
        if not path.is_file() or "ddflow" not in path.read_text("utf-8", errors="replace"):
            missing.append(name)
        elif not os.access(path, os.X_OK):
            missing.append(f"{name} (not executable)")
    if missing:
        return Check("hooks armed", "failed", "not armed: " + ", ".join(missing))
    return Check("hooks armed", "passed", "pre-commit and commit-msg carry ddflow's invocation")


def _trailer_refused(repo: Path) -> Check:
    """The commit-msg hook must REFUSE a forbidden trailer, not merely exist."""
    hook = _hooks_dir(repo) / "commit-msg"
    if not hook.is_file():
        return Check("trailer refused", "unavailable", "no commit-msg hook to probe")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
        handle.write(_VIOLATION)
        message = handle.name
    try:
        done = P.run([str(hook), message], cwd=repo, capture_output=True, text=True, timeout=60)
        if done.returncode == 0:
            return Check("trailer refused", "failed", "the hook accepted a forbidden trailer")
        return Check("trailer refused", "passed", f"the hook refused it (exit {done.returncode})")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Check("trailer refused", "unavailable", f"the hook could not be probed: {exc}")
    finally:
        Path(message).unlink(missing_ok=True)


def _answers(repo: Path) -> Check:
    """`brief` and `next` answer on stdout. Exit 2 means nothing READY for `next` (a
    real answer with text); `brief` exiting 2 is a failure to run, never a pass."""
    brief = P.run(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), "brief"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if brief.returncode != 0:
        return Check(
            "brief/next answer",
            "failed",
            f"brief exited {brief.returncode}: {(brief.stderr or '')[-200:].strip()}",
        )
    if not (brief.stdout or "").strip():
        return Check("brief/next answer", "failed", "brief printed nothing")
    nxt = P.run(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), "next"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if nxt.returncode not in (0, 2) or not (nxt.stdout or "").strip():
        return Check(
            "brief/next answer",
            "failed",
            f"next exited {nxt.returncode}: {(nxt.stderr or '')[-200:].strip()}",
        )
    ready = "ready" if nxt.returncode == 0 else "nothing ready"
    return Check(
        "brief/next answer",
        "passed",
        f"brief {len(brief.stdout.splitlines())} line(s); next: {ready}",
    )


def _frozen(repo: Path) -> Check:
    """The freeze ratchet exists, and every frozen file still has its bytes."""
    manifest = L.read_frozen(repo)
    if manifest is None:
        return Check("freeze ratchet", "failed", "no .ddflow/frozen.toml; the import is unfrozen")
    if not manifest:
        return Check(
            "freeze ratchet", "passed", "present, freezing nothing (all unfrozen deliberately)"
        )
    drift = L.check_frozen(repo) or []
    if drift:
        return Check(
            "freeze ratchet",
            "failed",
            f"{len(drift)} frozen file(s) changed: " + ", ".join(drift[:3]),
        )
    return Check("freeze ratchet", "passed", f"{len(manifest)} frozen file(s) unchanged")


def _suite_command(cfg: Config) -> str:
    """The configured unit_tests command, however the config table is shaped."""
    gate = getattr(cfg, "gate", None)
    entry = gate.get("unit_tests") if isinstance(gate, dict) else getattr(gate, "unit_tests", None)
    return str(getattr(entry, "command", "") or "")


def _suite(repo: Path, command: str, timeout: int) -> Check:
    """The configured suite in a detached tree of the default branch."""
    if not command:
        return Check("suite green", "unavailable", "no gate.unit_tests.command configured")
    result = T.baseline(repo, command, timeout=timeout)
    if not result.ran:
        return Check("suite green", "unavailable", result.detail)
    if result.green:
        return Check(
            "suite green", "passed", f"{result.detail} in {result.seconds:.1f}s (detached tree)"
        )
    return Check("suite green", "failed", result.detail)


def verify(
    repo: Path,
    *,
    suite_command: str | None = None,
    suite_timeout: int = 1800,
    suite: bool = True,
) -> VerifyReport:
    """Run every probe and return the one report; `suite=False` skips the full run."""
    repo = Path(repo)
    cfg_command = suite_command if suite_command is not None else _suite_command(Config.load(repo))
    checks = [
        _handshake(repo, H.registered_entry(repo)),
        _hooks(repo),
        _trailer_refused(repo),
        _answers(repo),
        _frozen(repo),
    ]
    if suite:
        checks.append(_suite(repo, cfg_command, suite_timeout))
    return VerifyReport(checks)
