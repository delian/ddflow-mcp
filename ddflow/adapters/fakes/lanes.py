"""Shipped stand-in for the agent-lane port: `FakeLane` replays a scripted transcript.

No process and no agent: ``start`` answers a handle at once, ``events`` replays the script as
canonical `LaneEvent`s (numbered from 1, the buffer cap honoured with the same drop-oldest
count), ``status`` reports the scripted exit once the script is read, ``steer`` is recorded when
the fake is built with the ``steer`` capability and ``unavailable`` otherwise, like a real
transport. Swarm, ETA and conformance tests drive a lane through it without spawning an agent.
Registered in `FAKES` under the port's name by `@fake`; the same cases run against it and the
default adapter (tests/test_adapter_lanes.py).
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from .. import lanes as L
from . import fake

#: What a fake handle reports as its transport.
TRANSPORT = "fake"


@dataclass
class FakeHandle:
    id: str
    spec: L.LaunchSpec
    script: list[L.LaneEvent]
    transport: str = TRANSPORT
    capabilities: frozenset[str] = frozenset()
    cursor: int = 0
    dropped: int = 0
    stopped: bool = False
    steered: list[str] = field(default_factory=list)


@fake
class FakeLane:
    """A scripted `Lane`. ``transcript`` is ``(event, fields)`` pairs; ``exit_code`` is what
    the "process" ends with; ``refuse`` makes every ``start`` return that `Result`."""

    def __init__(
        self,
        transcript: Iterable[tuple[str, Mapping[str, Any]]] = (),
        *,
        exit_code: int = 0,
        capabilities: Iterable[str] = ("interrupt", "usage", "tool_events"),
        refuse: L.Result | None = None,
    ) -> None:
        self.transcript = [(ev, dict(fields)) for ev, fields in transcript]
        self.exit_code = exit_code
        self.capabilities = frozenset(capabilities) & L.CAPABILITIES
        self.refuse = refuse
        self.started: list[L.LaunchSpec] = []
        self._ids = itertools.count(1)

    def start(self, spec: L.LaunchSpec) -> FakeHandle | L.Result:
        if self.refuse is not None:
            return self.refuse
        if not spec.argv:
            return L.failed("cannot start: the launch has no argv")
        self.started.append(spec)
        script = [
            L.LaneEvent(i, spec.name, ev, dict(fields))
            for i, (ev, fields) in enumerate(self.transcript, 1)
        ]
        cap = max(1, spec.event_buffer)
        dropped = max(0, len(script) - cap)
        return FakeHandle(
            f"{spec.name}-fake{next(self._ids)}",
            spec,
            script,
            capabilities=self.capabilities,
            dropped=dropped,
            cursor=dropped,
        )

    def events(self, h: FakeHandle, idle_s: float | None = None) -> Iterator[L.LaneEvent]:
        while h.cursor < len(h.script):
            h.cursor += 1
            yield h.script[h.cursor - 1]

    def steer(self, h: FakeHandle, text: str) -> L.Result:
        if "steer" not in h.capabilities:
            return L.unavailable("the fake lane was built without the steer capability")
        h.steered.append(text)
        return L.ok("recorded")

    def stop(self, h: FakeHandle, grace_s: float | None = None) -> L.Result:
        h.stopped = h.stopped or h.cursor < len(h.script)
        h.cursor = len(h.script)
        return L.ok(f"exit {self.exit_code}")

    def status(self, h: FakeHandle) -> L.LaneStatus:
        done = h.cursor >= len(h.script)
        if not done:
            state = "running"
        elif h.stopped:
            state = "stopped"
        else:
            state = "exited" if self.exit_code == 0 else "failed"
        session = next(
            (
                e.fields["session"]
                for e in h.script
                if e.event == "started" and isinstance(e.fields.get("session"), str)
            ),
            "",
        )
        return L.LaneStatus(
            state=state,
            exit_code=self.exit_code if done else None,
            exit_meaning=h.spec.exit_codes.get(str(self.exit_code), "") if done else "",
            session=session,
            events=len(h.script),
            dropped=h.dropped,
            capabilities=h.capabilities,
        )
