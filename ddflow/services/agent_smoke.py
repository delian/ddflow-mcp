"""The live smoke harness, and the minimal headless runner P-agent-parity.launcher adopts.

For one agent: detect it, build its headless command from the descriptor (`[headless]` argv,
turn-cap flag and autonomy profile; `[test]` overrides), run a fixed tiny ddflow task in a
throwaway project, and assert ONLY on ddflow's own event log. Controls: a timeout, a turn cap,
one retry of a failed attempt, a majority of N rounds. The verdicts are pass, fail and
`unavailable` (a missing CLI, a missing key, a timeout, a rate limit): unavailable is never
pass. Commands run through `cmdrunner.CommandRunner` (output cap, redaction). Nothing here
installs an agent; keys come from the environment given, never from a file.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..adapters import git as G
from ..adapters.fsio import scratch_dir
from ..core.redact import Redactor
from ..infra.fsio import atomic_write
from . import cmdrunner as C
from . import harnessreg

PASS, FAIL, UNAVAILABLE = "pass", "fail", "unavailable"
KIND = "agent.smoke"
RUNNER_IDENTITY = "smoke-runner"
EXIT_FOR = {PASS: 0, FAIL: 1, UNAVAILABLE: 2}
EXIT_REFUSED = 3
PROMPT = (
    "Call ddflow_brief, claim the one task, add a file, run the gates you can, and complete it "
    "(or release it honestly if you cannot)."
)
_RATE = re.compile(r"\b429\b|rate.?limit|too many requests|quota", re.I)
#: The assertions a verdict rests on; the others are listed as gaps in the report.
CORE = ("tool_called", "claim_exists", "completed_or_released", "no_stray_lease")


@dataclass
class Attempt:
    verdict: str
    reason: str = ""
    assertions: dict[str, bool] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)

    @property
    def ran(self) -> bool:
        return self.verdict != UNAVAILABLE

    def as_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "reason": self.reason,
                "assertions": self.assertions, "gaps": self.gaps}  # fmt: skip


@dataclass
class SmokeResult:
    agent: str
    verdict: str
    reason: str = ""
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(a.verdict == PASS for a in self.attempts)

    def as_dict(self) -> dict[str, Any]:
        return {"agent": self.agent, "verdict": self.verdict, "reason": self.reason,
                "attempts": [a.as_dict() for a in self.attempts]}  # fmt: skip


def majority(verdicts: list[str]) -> str:
    """pass or fail only on a strict majority of the rounds; anything else is unavailable."""
    n = len(verdicts)
    if verdicts.count(PASS) * 2 > n:
        return PASS
    if verdicts.count(FAIL) * 2 > n:
        return FAIL
    return UNAVAILABLE


def _test(h: harnessreg.Harness) -> dict[str, str]:
    sec = h.reserved.get("test")
    return dict(sec.values) if sec else {}


def command_line(h: harnessreg.Harness, *, prompt: str = PROMPT, max_turns: int = 20) -> str:
    """The headless command: `[test].argv` or `[headless].argv`, the turn cap, the autonomy
    profile's flags, then the prompt as the last argument."""
    t = _test(h)
    head = h.headless
    if t.get("argv"):
        argv = shlex.split(t["argv"])
    elif head and not head.none and head.argv:
        argv = list(head.argv)
    else:
        raise ValueError(f"{h.id}: no headless command (give [headless].argv or [test].argv)")
    flag = t.get("turn_flag") or (head.max_turns_flag if head else "")
    if flag:
        argv += [flag, str(max_turns)]
    if head and head.autonomy and h.autonomy and head.autonomy in h.autonomy.profiles:
        argv += shlex.split(h.autonomy.profiles[head.autonomy])
    return shlex.join([*argv, prompt])


def _path(env: Mapping[str, str]) -> str:
    return env.get("PATH", "/usr/bin:/bin")


def detect(h: harnessreg.Harness, env: Mapping[str, str], runner: C.CommandRunner) -> str:
    """Why the agent cannot be smoked ("" when it can): the CLI, then its keys."""
    t = _test(h)
    head = shlex.split(t["argv"]) if t.get("argv") else list(h.headless.argv if h.headless else ())
    if not head:
        return f"{h.id} has no headless command"
    if C.executable_missing(shlex.join(head), _path(env)):
        return f"{head[0]!r} is not installed (never installed by the smoke)"
    for key in [k.strip() for k in t.get("env_keys", "").split(",") if k.strip()]:
        if not env.get(key):
            return f"{key} is not set in the environment"
    if t.get("detect"):
        run = runner.run(C.Declared(t["detect"], f"harness:{h.id}[test].detect"),
                         env=env, timeout_s=30)  # fmt: skip
        if not run.ran:
            return f"detect command unavailable: {run.reason}"
        if run.code != 0:
            return f"detect command exited {run.code}"
    return ""


def assess(project: Path) -> tuple[dict[str, bool], list[str]]:
    """The assertions, from the project's event log only."""
    from ..infra.log import EventLog

    evs = EventLog(project, RUNNER_IDENTITY).read_all()
    theirs = [e for e in evs if e.agent != RUNNER_IDENTITY]
    acquired = [e for e in evs if e.kind == "lease.acquired"]
    open_leases = {e.subject for e in acquired}
    for e in evs:
        if e.kind in ("lease.released", "item.completed", "lease.expired", "item.abandoned"):
            open_leases.discard(e.subject)
    done = any(e.kind in ("item.completed", "lease.released") for e in evs)
    a = {
        "tool_called": bool(theirs),
        "claim_exists": any(e.agent != RUNNER_IDENTITY for e in acquired),
        "completed_or_released": done,
        "no_stray_lease": not open_leases,
    }
    gaps = []
    if not any(e.kind == "session.started" for e in evs):
        gaps.append("no session.started with the harness name")
    if not any(e.kind == "session.prompt" for e in evs):
        gaps.append("no session.prompt: no native prompt hook, first-call fallback")
    porcelain = G.status_run(project, ".ddflow/events").out
    if porcelain.strip():
        gaps.append("the event shard is not committed")
    return a, gaps


def scaffold(project: Path, agent: str = "") -> None:
    """A throwaway git project with ddflow initialised and one task (live runs only)."""

    def run(*argv: str) -> None:
        done = C.CommandRunner().run(
            C.Declared(shlex.join(argv), "agent_smoke.scaffold"), cwd=project, timeout_s=120
        )
        if not done.ran or done.code != 0:
            raise RuntimeError(f"scaffold step failed: {done.reason or done.output[-200:]}")

    G.init_repo(project)
    for k, v in (
        ("user.email", "smoke@example.invalid"),
        ("user.name", "smoke"),
        ("commit.gpgsign", "false"),
    ):
        G.run(project, "config", k, v, check=True)
    atomic_write(project / "README.md", "# smoke\n")
    G.run(project, "add", "README.md", check=True)
    G.run(project, "commit", "-qm", "init", check=True)
    dd = [sys.executable, "-m", "ddflow", "--agent", RUNNER_IDENTITY]
    run(*dd, "init")
    run(*dd, "phase", "add", "P-smoke", "--title", "smoke")
    run(*dd, "task", "add", "T-smoke", "--phase", "P-smoke", "--title", "Add a file smoke.txt")
    if agent:
        run(*dd, "adopt", agent)


def _redactor(env: Mapping[str, str], keys: list[str]) -> Redactor:
    secrets = [re.escape(env[k]) for k in keys if env.get(k)]
    return Redactor("view", secret_patterns=secrets)


def _attempt(h, project: Path, line: str, env, runner, timeout_s, prepare, assess_fn) -> Attempt:
    project.mkdir(parents=True, exist_ok=True)
    prepare(project)
    run = runner.run(C.Declared(line, f"harness:{h.id}[headless]"), cwd=project, env=env,
                     timeout_s=timeout_s, merge_stderr=True)  # fmt: skip
    if not run.ran:
        return Attempt(UNAVAILABLE, f"{run.kind}: {run.reason}")
    if run.code != 0 and _RATE.search(run.output):
        return Attempt(UNAVAILABLE, "rate limited")
    expected = {int(c) for c in _test(h).get("exit_codes", "0").split(",") if c.strip()}
    a, gaps = assess_fn(project)
    ok = all(a[k] for k in CORE) and run.code in expected
    why = (
        ""
        if ok
        else "failed: "
        + ", ".join(
            [k for k in CORE if not a[k]]
            + ([f"exit {run.code}"] if run.code not in expected else [])
        )
    )
    return Attempt(PASS if ok else FAIL, why, a, gaps)


def smoke(
    h: harnessreg.Harness,
    *,
    workdir: Path,
    env: Mapping[str, str],
    prepare: Callable[[Path], None] | None = None,
    assess_fn: Callable[[Path], tuple[dict[str, bool], list[str]]] = assess,
    rounds: int = 3,
    retries: int = 1,
    timeout_s: float = 600.0,
    max_turns: int = 20,
    log: Any = None,
) -> SmokeResult:
    """Smoke ``h``: unavailable without an agent or key; else ``rounds`` rounds in fresh
    projects under ``workdir``, each failed attempt retried ``retries`` times, the verdict the
    majority. An unavailable attempt ends the run (a rate limit is not hammered)."""
    keys = [k.strip() for k in _test(h).get("env_keys", "").split(",") if k.strip()]
    runner = C.CommandRunner(max_output=20000, redactor=_redactor(env, keys))
    why = detect(h, env, runner)
    result = SmokeResult(h.id, UNAVAILABLE, why)
    if not why:
        line = command_line(h, max_turns=max_turns)
        prep = prepare or (lambda p: scaffold(p, h.id))
        verdicts: list[str] = []
        for r in range(rounds):
            att = None
            for t in range(retries + 1):
                att = _attempt(
                    h, Path(workdir) / f"r{r}-t{t}", line, env, runner, timeout_s, prep, assess_fn
                )
                result.attempts.append(att)
                if att.verdict != FAIL:
                    break
            verdicts.append(att.verdict)
            if att.verdict == UNAVAILABLE:
                result.reason = att.reason
                break
        result.verdict = majority(verdicts)
        if result.verdict != PASS and not result.reason:
            result.reason = next((a.reason for a in reversed(result.attempts) if a.reason), "")
    if log is not None:
        log.append(KIND, h.id, {"agent": h.id, "verdict": result.verdict, "reason": result.reason,
                                "attempts": [a.as_dict() for a in result.attempts]})  # fmt: skip
    return result


def plan() -> list[dict[str, Any]]:
    """Every headless-capable harness and its command; runs nothing."""
    out = []
    for h in harnessreg.load():
        try:
            out.append({"agent": h.id, "command": command_line(h)})
        except ValueError:
            continue
    return out


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agent-smoke", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="list the headless commands; runs nothing")
    p.add_argument("--json", action="store_true")
    r = sub.add_parser("run", help="smoke one agent (needs --live)")
    r.add_argument("agent")
    r.add_argument("--live", action="store_true", help="really start the agent")
    r.add_argument("--rounds", type=int, default=None)
    r.add_argument("--retries", type=int, default=None)
    r.add_argument("--timeout", type=float, default=None)
    r.add_argument("--max-turns", type=int, default=None)
    r.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "plan":
        rows = plan()
        print(
            json.dumps(rows, indent=1)
            if a.json
            else "\n".join(f"{x['agent']}: {x['command']}" for x in rows)
        )
        return 0
    if not a.live:
        print(
            "refused: this starts a real, model-backed agent; pass --live to mean it",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    h = harnessreg.get(a.agent)
    if h is None:
        print(f"unknown agent {a.agent!r}", file=sys.stderr)
        return EXIT_REFUSED
    with scratch_dir("agent-smoke-") as work:
        given = {
            "rounds": a.rounds,
            "retries": a.retries,
            "timeout_s": a.timeout,
            "max_turns": a.max_turns,
        }
        res = smoke(h, workdir=Path(work), env=dict(env or {}),
                    **{k: v for k, v in given.items() if v is not None})  # fmt: skip
    print(
        json.dumps(res.as_dict(), indent=1)
        if a.json
        else f"{res.agent}: {res.verdict} {res.reason}"
    )
    return EXIT_FOR[res.verdict]
