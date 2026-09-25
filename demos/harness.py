"""A scenario harness that drives ddflow against invented projects, for real.

Nothing here is mocked. Each "agent" is a real subprocess invoking the real CLI against
a real git repository, creating real worktrees, running real test commands and making
real merges. That is the only way an end-to-end test of a coordination system means
anything: the failures this system exists to prevent — a lost append, a stolen lease, a
merge that swaps files under a live session — are all properties of the actual tools.

Run:  python3 demos/harness.py [scenario ...]   (default: all)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PY = sys.executable

_C = {
    "h": "\033[1;36m",
    "ok": "\033[32m",
    "bad": "\033[31m",
    "dim": "\033[2m",
    "warn": "\033[33m",
    "end": "\033[0m",
}
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    _C = dict.fromkeys(_C, "")


class Fail(AssertionError):
    pass


class Scenario:
    def __init__(self, name: str, workdir: Path) -> None:
        self.name = name
        self.dir = workdir
        self.steps = 0
        self.checks = 0

    # -- output -------------------------------------------------------------------
    def head(self, text: str) -> None:
        print(f"\n{_C['h']}{'━' * 78}\n {text}\n{'━' * 78}{_C['end']}")

    def step(self, text: str) -> None:
        self.steps += 1
        print(f"\n{_C['h']}▸ {self.steps}. {text}{_C['end']}")

    def note(self, text: str) -> None:
        for line in textwrap.wrap(text, 74):
            print(f"  {_C['dim']}{line}{_C['end']}")

    def check(self, label: str, cond: bool, detail: str = "") -> None:
        self.checks += 1
        if cond:
            print(f"  {_C['ok']}✓{_C['end']} {label}")
        else:
            print(f"  {_C['bad']}✗ {label}{_C['end']}")
            if detail:
                print(f"    {detail}")
            raise Fail(f"{self.name}: {label}\n{detail}")

    # -- process ------------------------------------------------------------------
    def ddflow(
        self,
        *argv: str,
        agent: str = "",
        repo: Path | None = None,
        expect: int | None = 0,
        quiet: bool = False,
    ) -> tuple[int, str, str]:
        r = repo or self.repo
        cmd = [PY, "-m", "ddflow", "--repo", str(r)]
        if agent:
            cmd += ["--agent", agent]
        cmd += list(argv)
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600)
        if not quiet:
            shown = " ".join(argv[:4])
            print(
                f"  {_C['dim']}$ ddflow {shown}"
                f"{' (as ' + agent + ')' if agent else ''} → exit {p.returncode}{_C['end']}"
            )
        if expect is not None and p.returncode != expect:
            raise Fail(
                f"`ddflow {' '.join(argv)}` exit {p.returncode}, expected {expect}\n"
                f"STDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}"
            )
        return p.returncode, p.stdout, p.stderr

    def jddflow(self, *argv: str, **kw) -> object:
        _, out, _ = self.ddflow("--json", *argv, **kw)
        return json.loads(out)

    def git(self, *argv: str, repo: Path | None = None) -> str:
        p = subprocess.run(
            ["git", "-C", str(repo or self.repo), *argv], capture_output=True, text=True
        )
        return (p.stdout or p.stderr).strip()

    # -- fixture ------------------------------------------------------------------
    def make_repo(self, name: str, files: dict[str, str]) -> Path:
        repo = self.dir / name
        # Remove the repo AND the worktree root. Worktrees live outside the repo by
        # design, so deleting the repo alone strands them and the next run's
        # `git worktree add` fails on a path that already exists. Anything else the
        # scenario put in this directory is left alone -- scenario 3 keeps its
        # surviving event log here on purpose.
        self.dir.mkdir(parents=True, exist_ok=True)
        for victim in (repo, self.dir / ".ddflow-worktrees"):
            if victim.exists():
                shutil.rmtree(victim)
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        for k, v in (
            ("user.email", "agent@example.com"),
            ("user.name", "Agent"),
            ("commit.gpgsign", "false"),
        ):
            subprocess.run(["git", "-C", str(repo), "config", k, v], check=True)
        for rel, body in files.items():
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(textwrap.dedent(body).lstrip(), "utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial scaffold"], check=True)
        self.repo = repo
        return repo

    def write(self, rel: str, body: str, *, repo: Path | None = None) -> Path:
        p = (repo or self.repo) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body).lstrip(), "utf-8")
        return p

    def commit_in(self, worktree: Path, message: str) -> str:
        subprocess.run(["git", "-C", str(worktree), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(worktree), "commit", "-qm", message], check=True)
        return self.git("rev-parse", "HEAD", repo=worktree)


def summarise(results: list[tuple[str, bool, int, int, float]]) -> int:
    print(f"\n{_C['h']}{'═' * 78}\n SCENARIO RESULTS\n{'═' * 78}{_C['end']}")
    ok = 0
    for name, passed, steps, checks, secs in results:
        mark = f"{_C['ok']}PASS{_C['end']}" if passed else f"{_C['bad']}FAIL{_C['end']}"
        print(f"  {mark}  {name:<44s} {steps:>3d} steps {checks:>3d} checks {secs:6.1f}s")
        ok += passed
    total_checks = sum(r[3] for r in results)
    print(f"\n  {ok}/{len(results)} scenarios passed, {total_checks} assertions.")
    return 0 if ok == len(results) else 1


class McpClient:
    """A minimal MCP client speaking the real protocol to a real server process.

    Deliberately not a mock. The whole point of an MCP scenario is to exercise the
    transport an agent actually uses -- newline-delimited JSON-RPC over a pipe to a
    separately-spawned process -- because that is where the failures live: a stray
    print corrupting the stream, a notification that wrongly gets a reply, an exit code
    that never reaches the model.
    """

    def __init__(self, repo: Path, root: Path, agent: str = "") -> None:
        self.repo, self.root, self.agent, self._id = repo, root, agent, 0
        env = {**os.environ, "PYTHONPATH": str(root)}
        if agent:
            env["DDFLOW_AGENT"] = agent
        self.proc = subprocess.Popen(
            [PY, "-m", "ddflow", "--repo", str(repo), "mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )

    def notify(self, method: str, params: dict | None = None) -> None:
        """Send a NOTIFICATION: write, do not read.

        A JSON-RPC notification has no id and gets no reply. Reading one anyway blocks
        forever -- the client waits for a line the server is right not to send, while
        the server waits for the next request. Both processes then sit at 0% CPU
        looking perfectly healthy, which is the worst shape a hang can take. Caught by
        the orchestration scenario, which is the first thing here to send one.
        """
        self.proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}}) + "\n"
        )
        self.proc.stdin.flush()

    def call(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        self.proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})
            + "\n"
        )
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            err = self.proc.stderr.read()[:2000]
            raise Fail(f"MCP server closed the stream. stderr:\n{err}")
        return json.loads(line)

    def tool(self, name: str, **args) -> tuple[str, int]:
        """Returns (text, exit_code). Exit 2/3 are RESULTS, not errors."""
        r = self.call("tools/call", {"name": name, "arguments": args})
        if "error" in r:
            raise Fail(f"{name} -> JSON-RPC error {r['error']}")
        res = r["result"]
        return res["content"][0]["text"], res.get("_meta", {}).get("exit", 0)

    def jtool(self, name: str, **args) -> object:
        text, _code = self.tool(name, **args)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise Fail(f"{name} did not return JSON: {exc}\n{text[:400]}") from exc

    def initialize(self) -> dict:
        r = self.call(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "scenario", "version": "1"},
            },
        )
        self.notify("notifications/initialized")
        return r["result"]

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
