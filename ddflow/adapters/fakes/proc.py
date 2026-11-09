"""The shipped fake of the `Proc` port: a command runner that RECORDS every run and returns
SCRIPTED results, so a test can assert both what was run and what came back without a
subprocess."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any

from ..proc import Captured, Proc
from . import fake


@dataclass
class FakePopen:
    """A scripted Popen: `wait`/`poll` answer the scripted exit code without a process."""

    returncode: int = 0

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode


@fake
class FakeProc(Proc):
    """A `Proc` whose runs are recorded (`calls`) and whose answers are scripted (`script`:
    a queue of dicts, one per call, in order). A call with no answer left returns an empty
    success."""

    def __init__(self, script: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.script: list[dict[str, Any]] = list(script or [])

    def _next(self) -> dict[str, Any]:
        return self.script.pop(0) if self.script else {}

    def run(self, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        self.calls.append(("run", args, kwargs))
        r = self._next()
        return subprocess.CompletedProcess(
            r.get("args", args[0] if args else ()),
            r.get("returncode", 0),
            r.get("stdout", ""),
            r.get("stderr", ""),
        )

    def capture(self, argv: list[str], *, timeout: float | None, **kwargs: Any) -> Captured:
        self.calls.append(("capture", (argv,), {"timeout": timeout, **kwargs}))
        r = self._next()
        return Captured(
            r.get("returncode", 0),
            r.get("stdout", ""),
            r.get("stderr", ""),
            r.get("error"),
        )

    def spawn_shell(self, line: str, **kwargs: Any) -> FakePopen:
        self.calls.append(("spawn_shell", (line,), kwargs))
        r = self._next()
        return FakePopen(r.get("returncode", 0))
