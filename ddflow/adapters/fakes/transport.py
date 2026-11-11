"""The shipped fake of the `Transport` port: a lane that never spawns a subprocess.

`FakeTransport` records every port call (`calls`) and scripts each lane's lifecycle -- running
or already ended, its exit code, its ``lane.log`` output and the files `copy_out` returns --
so the conformance cases run against it and the default `LocalTransport` without a process or
the network. It reaches nothing outside the process but the files a test hands it to copy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..lanes import LaneStatus, Result, failed, ok
from ..transport import Transport
from . import fake


@dataclass
class _Run:
    running: bool = True
    exit_code: int | None = None
    log: str = ""
    files: dict[str, bytes] = field(default_factory=dict)
    pid: int = 100
    stopped: bool = False


@fake
class FakeTransport(Transport):
    def __init__(
        self, name: str = "local", target: str = "local", script: dict | None = None
    ) -> None:
        self.name = name
        self.target = target
        #: every port call, in order: (verb, *args).
        self.calls: list[tuple] = []
        #: lane -> a dict scripting its lifecycle, or a `Result` `start` returns instead.
        self.script: dict[str, Any] = dict(script or {})
        self.lanes: dict[str, _Run] = {}

    def _run(self, lane: str) -> _Run | None:
        return self.lanes.get(lane)

    def start(self, lane: str, argv: Any) -> Result:
        self.calls.append(("start", lane, tuple(argv)))
        sc = self.script.get(lane)
        if isinstance(sc, Result):
            return sc
        run = self._run(lane)
        if run is not None and run.running:
            return failed(f"lane {lane!r} is already running")
        opts = sc if isinstance(sc, dict) else {}
        self.lanes[lane] = _Run(
            running=opts.get("running", True),
            exit_code=opts.get("exit_code"),
            log=opts.get("log", ""),
            files=dict(opts.get("files", {})),
        )
        return ok()

    def signal(self, lane: str, sig: str = "TERM") -> Result:
        self.calls.append(("signal", lane, sig))
        run = self._run(lane)
        if run is None:
            return failed(f"no such lane {lane!r}")
        if not run.running:
            return failed("the lane has ended")
        run.stopped = True
        run.running = False
        return ok(f"{sig} sent to the process group")

    def observe(self, lane: str) -> LaneStatus | Result:
        self.calls.append(("observe", lane))
        run = self._run(lane)
        if run is None:
            return failed(f"no such lane {lane!r}")
        if run.running:
            return LaneStatus("running", pid=run.pid)
        state = "stopped" if run.stopped else "exited" if run.exit_code == 0 else "failed"
        return LaneStatus(state, pid=run.pid, exit_code=run.exit_code)

    def copy_in(self, lane: str, local: Path, name: str) -> Result:
        self.calls.append(("copy_in", lane, str(local), name))
        run = self._run(lane)
        if run is None:
            return failed(f"no such lane {lane!r}")
        run.files[name] = Path(local).read_bytes()
        return ok()

    def copy_out(self, lane: str, name: str, local: Path, max_bytes: int) -> Result:
        self.calls.append(("copy_out", lane, name, str(local), max_bytes))
        run = self._run(lane)
        if run is None:
            return failed(f"no such lane {lane!r}")
        data = run.log.encode() if name == "lane.log" else run.files.get(name)
        if data is None:
            return failed(f"no {name!r} in lane {lane!r}")
        if len(data) > max_bytes:
            return failed(f"the file is over the {max_bytes} byte cap")
        Path(local).write_bytes(data)
        return ok()

    def cleanup(self, lane: str) -> Result:
        self.calls.append(("cleanup", lane))
        run = self._run(lane)
        if run is not None:
            run.running = False
            self.lanes.pop(lane, None)
        return ok()
