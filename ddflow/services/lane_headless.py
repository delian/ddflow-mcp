"""The ``process-headless`` lane driver: a coding agent run as one unattended child process.

Covers ``claude -p``, ``codex exec``, ``gemini -p``, ``kimi``, ``dsh headless``, ``opencode run``:
anything whose launch is an argv template and whose output is a JSON-lines stream. Built from
the engines: E4 renders the argv, E5 maps stdout to canonical events, E6 detects a usage limit.
It can interrupt and stop; it cannot steer a running process (the prompt is the argv), so
``steer`` is ``unavailable`` rather than a restart in disguise.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import shutil
import threading
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from ..infra import fsio, proc
from .engines import limit_detect, render_argv, stream_map
from .engines.safety import DEFAULT, EngineError
from .lane_driver import (
    Lane,
    LaneEvent,
    LaneStatus,
    LaunchSpec,
    Result,
    failed,
    ok,
    register_transport,
    unavailable,
)

TRANSPORT = "process-headless"
SECRET_MIN = 6  #: values shorter than this are not worth redacting (and would mangle text)
STDERR_TAIL = 4096


@dataclass
class _Run:
    id: str
    spec: LaunchSpec
    popen: object
    capabilities: frozenset[str]
    transport: str = TRANSPORT
    secrets: tuple[str, ...] = field(default=(), repr=False)
    private: list[Path] = field(default_factory=list)
    unit: str = ""
    cond: threading.Condition = field(default_factory=threading.Condition, repr=False)
    buf: deque = field(default_factory=deque, repr=False)
    stderr: deque = field(default_factory=lambda: deque(maxlen=STDERR_TAIL), repr=False)
    seq: int = 0
    seen: set = field(default_factory=set)
    session: str = ""
    skipped: int = 0
    dropped: int = 0
    reader_done: bool = False
    stopped: bool = False
    cleaned: bool = False
    threads: list = field(default_factory=list, repr=False)

    def redact(self, v):
        if isinstance(v, str):
            for s in self.secrets:
                v = v.replace(s, "[redacted]")
        return v


def _read_stdout(run: _Run) -> None:
    spec, stream = run.spec, run.popen.stdout  # type: ignore[attr-defined]
    cap = DEFAULT.max_line_chars
    try:
        while True:
            line = stream.readline(cap + 1)
            if not line:
                break
            if len(line) > cap and not line.endswith("\n"):
                while (rest := stream.readline(cap + 1)) and not rest.endswith("\n"):
                    pass  # drop the overlong line to its end, never buffer it
                with run.cond:
                    run.skipped += 1
                continue
            ev = stream_map.map_line(line, spec.stream_rules)
            with run.cond:
                if ev is None:
                    run.skipped += 1
                    continue
                fields = {k: run.redact(v) for k, v in ev.fields.items()}
                run.seq += 1
                run.seen.add(ev.event)
                if ev.event == "started" and isinstance(fields.get("session"), str):
                    run.session = fields["session"]
                if len(run.buf) >= max(1, spec.event_buffer):
                    run.buf.popleft()
                    run.dropped += 1
                run.buf.append(LaneEvent(run.seq, spec.name, ev.event, fields))
                run.cond.notify_all()
    except (OSError, ValueError):
        pass
    finally:
        with run.cond:
            run.reader_done = True
            run.cond.notify_all()


def _read_stderr(run: _Run) -> None:
    try:
        for chunk in iter(lambda: run.popen.stderr.read(1024), ""):  # type: ignore[attr-defined]
            run.stderr.extend(chunk)
    except (OSError, ValueError):
        pass


def _write_stdin(popen, text: str) -> None:
    with contextlib.suppress(OSError, ValueError):
        popen.stdin.write(text)
        popen.stdin.close()


def _caps(base: frozenset[str], spec: LaunchSpec) -> frozenset[str]:
    """What THIS lane can do: no resume without a resume flag, no usage or tool events
    without a stream rule that emits them."""
    caps = set(base)
    if not spec.resume:
        caps.discard("resume")
    emits = {rule.emit for rule in spec.stream_rules}
    for cap, ev in (("usage", "usage"), ("tool_events", "tool")):
        if ev not in emits:
            caps.discard(cap)
    return frozenset(caps)


def _prepare(spec: LaunchSpec, private: list[Path]):
    """(rendered argv, child env, placeholder values) or a failed Result. Directories made
    here are appended to ``private`` so the caller can remove them on failure."""
    try:
        home = spec.home
        if home is None:
            home = fsio.unused_dir("ddflow-lane-")
            home.mkdir(mode=0o700)  # fails if the name was taken meanwhile
            private.append(home)
        else:
            home.mkdir(parents=True, exist_ok=True)
        home.chmod(0o700)
        values = dict(spec.values)
        if spec.prompt and "{prompt_file}" in spec.argv:
            pf = home / "prompt.txt"
            fsio.atomic_write(pf, spec.prompt, mode=0o600)
            values["prompt_file"] = str(pf)
        r = render_argv.render(spec.argv, values, env=dict(spec.env), environ=os.environ)
    except (EngineError, OSError) as e:
        return failed(f"cannot prepare the launch: {e}")
    if r.missing:
        return failed(f"key environment variable(s) not set: {', '.join(r.missing)}")
    env = {n: os.environ[n] for n in spec.pass_env if n in os.environ}
    env["HOME"] = str(home)
    if spec.home_env:
        env[spec.home_env] = str(home)
    env.update(spec.fixed_env)
    env.update(r.env)
    return r, env, values


class ProcessHeadless:
    """``Lane`` for a child process. One instance serves any number of lanes."""

    capabilities = frozenset({"interrupt", "resume", "usage", "tool_events"})

    def start(self, spec: LaunchSpec) -> _Run | Result:
        if spec.scope not in ("none", "systemd"):
            return failed(f"unknown scope {spec.scope!r}: none or systemd")
        if spec.scope == "systemd" and not shutil.which("systemd-run"):
            return unavailable("scope=systemd needs systemd-run on PATH; not falling back")
        private: list[Path] = []
        prep = _prepare(spec, private)
        if isinstance(prep, Result):
            self._rm(private)
            return prep
        r, env, values = prep
        run_id = f"{spec.name}-{secrets.token_hex(3)}"
        argv, unit = list(r.argv), ""
        if spec.scope == "systemd":
            unit = f"ddflow-lane-{run_id}"
            argv = [
                "systemd-run",
                "--user",
                "--scope",
                "--quiet",
                "--collect",
                f"--unit={unit}",
                "--",
                *argv,
            ]
        try:
            p = proc.spawn_stdio(argv, cwd=spec.cwd, env=env, text=True)
        except (OSError, ValueError) as e:
            self._rm(private)
            return failed(f"cannot start {argv[0]}: {e}")
        secret_values = tuple(v for v in r.env.values() if len(v) >= SECRET_MIN)
        run = _Run(
            run_id,
            spec,
            p,
            _caps(self.capabilities, spec),
            secrets=secret_values,
            private=private,
            unit=unit,
        )
        for fn in (_read_stdout, _read_stderr):
            t = threading.Thread(target=fn, args=(run,), daemon=True)
            t.start()
            run.threads.append(t)
        if spec.prompt and "prompt_file" not in values:
            threading.Thread(target=_write_stdin, args=(p, spec.prompt), daemon=True).start()
        else:
            with contextlib.suppress(OSError):
                p.stdin.close()
        return run

    def events(self, h: _Run, idle_s: float | None = None) -> Iterator[LaneEvent]:
        while True:
            with h.cond:
                if not h.buf and not h.reader_done:
                    h.cond.wait(timeout=idle_s)
                if h.buf:
                    ev = h.buf.popleft()
                elif h.reader_done or idle_s is not None:
                    return
                else:
                    continue
            yield ev

    def steer(self, h: _Run, text: str) -> Result:
        return unavailable(
            "process-headless cannot steer a running process: the prompt is its argv"
        )

    def interrupt(self, h: _Run) -> Result:
        if h.popen.poll() is not None:
            return failed("the lane has ended")
        proc.kill_group(h.popen, 2)  # SIGINT to the group
        return ok("SIGINT sent to the process group")

    def stop(self, h: _Run, grace_s: float | None = None) -> Result:
        p = h.popen
        grace = h.spec.stop_grace_s if grace_s is None else grace_s
        h.stopped = h.stopped or p.poll() is None
        if p.poll() is None:
            proc.kill_group(p, 15)
            try:
                p.wait(timeout=max(0.0, grace))
            except proc.TimeoutExpired:  # asking was not enough
                pass
        # the group may outlive its leader: always finish with a group kill, then reap
        proc.kill_group(p)
        proc.stop_group(p, reap_s=proc.TIMEOUTS["drain"])
        if h.unit:
            proc.capture(
                ["systemctl", "--user", "stop", f"{h.unit}.scope"], timeout=proc.TIMEOUTS["instant"]
            )
        for t in h.threads:
            t.join(timeout=2)
        self._cleanup(h)
        return ok(f"exit {p.returncode}")

    def status(self, h: _Run) -> LaneStatus:
        code = h.popen.poll()
        if code is not None:
            for t in h.threads:
                t.join(timeout=2)  # the stream is at its end once the child is
        with h.cond:
            state = (
                "running"
                if code is None
                else ("stopped" if h.stopped else "exited" if code == 0 else "failed")
            )
            hit = None
            if code is not None and h.spec.limit is not None:
                hit = limit_detect.detect(
                    h.spec.limit, exit_code=code, events=h.seen, text="".join(h.stderr)
                )
            if code is not None:
                self._cleanup(h)
            return LaneStatus(
                state=state,
                pid=h.popen.pid,
                exit_code=code,
                exit_meaning=h.spec.exit_codes.get(str(code), "") if code is not None else "",
                limit=hit.by if hit else "",
                reset=hit.reset if hit else "",
                session=h.session,
                events=h.seq,
                skipped=h.skipped,
                dropped=h.dropped,
                capabilities=h.capabilities,
            )

    @staticmethod
    def _rm(paths: list[Path]) -> None:
        for d in paths:
            shutil.rmtree(d, ignore_errors=True)

    def _cleanup(self, h: _Run) -> None:
        if not h.cleaned:
            h.cleaned = True
            self._rm(h.private)


_: Lane = ProcessHeadless()  # the class satisfies the contract (checked at import)
register_transport(TRANSPORT, ProcessHeadless)
