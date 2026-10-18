"""The standard fake kit for the shared evaluation harness (SE-eval-harness.2).

Every evaluation and acceptance task needs the same stand-ins for the things a workflow
touches: a clock it can move, an agent that answers from a script, meters that count, a
control plane and a hub that record what was sent, a bench that returns fixed scores, and a
vendor tool that "runs" with a scripted result. One kit, found by name in a `Registry`, so
no task writes its own. Deterministic: nothing here reads the real time, network or disk.

Pure of ddflow apart from `core.registry`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..core.registry import Registry


class ScriptExhausted(AssertionError):
    """A fake was asked for more answers than the fixture scripted."""


class FakeClock:
    """Time that moves only when told to: ``clock()`` is the reading, ``advance`` moves it."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> float:
        if seconds < 0:
            raise ValueError("a clock does not run backwards")
        self.t += seconds
        return self.t


class _Scripted:
    """Answers from a list, in order, and records every call."""

    def __init__(self, answers: Iterable[Any] = (), *, repeat_last: bool = False) -> None:
        self.answers = list(answers)
        self.repeat_last = repeat_last
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def _next(self, *args: Any, **kw: Any) -> Any:
        self.calls.append((args, kw))
        n = len(self.calls)
        if n <= len(self.answers):
            return self.answers[n - 1]
        if self.repeat_last and self.answers:
            return self.answers[-1]
        raise ScriptExhausted(
            f"{type(self).__name__} was called {n} time(s); {len(self.answers)} scripted"
        )


class FakeAgent(_Scripted):
    """An agent or model: ``agent(prompt)`` returns the next scripted reply; an exception
    instance in the script is raised, to stand for a refusal or a crash."""

    def __call__(self, prompt: str, **kw: Any) -> Any:
        out = self._next(prompt, **kw)  # positional whichever way the caller spelled it
        if isinstance(out, BaseException):
            raise out
        return out

    @property
    def prompts(self) -> list[str]:
        return [c[0][0] for c in self.calls]


class FakeVendorTool(_Scripted):
    """An external tool run: ``tool(argv)`` returns the next scripted `(exit, stdout)`."""

    def __call__(self, argv: Iterable[str]) -> tuple[int, str]:
        return self._next(list(argv))


class FakeMeters:
    """Named counters and gauges with no backend; ``snapshot`` is what was recorded."""

    def __init__(self) -> None:
        self.counters: dict[str, float] = {}
        self.gauges: dict[str, float] = {}

    def incr(self, name: str, by: float = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + by

    def gauge(self, name: str, value: float) -> None:
        self.gauges[name] = value

    def snapshot(self) -> dict[str, dict[str, float]]:
        return {"counters": dict(self.counters), "gauges": dict(self.gauges)}


@dataclass
class FakeEndpoint:
    """A control plane or a hub: records what was sent, answers from ``replies`` by topic
    (a missing topic gets ``default``)."""

    replies: Mapping[str, Any] = field(default_factory=dict)
    default: Any = None
    sent: list[tuple[str, Any]] = field(default_factory=list)

    def send(self, topic: str, payload: Any = None) -> Any:
        self.sent.append((topic, payload))
        return self.replies.get(topic, self.default)

    def topics(self) -> list[str]:
        return [t for t, _ in self.sent]


class FakeBench:
    """A bench that scores a candidate from a table, 0.0 for one it does not know."""

    def __init__(self, scores: Mapping[str, float] | None = None) -> None:
        self.scores = dict(scores or {})
        self.runs: list[str] = []

    def run(self, candidate: str) -> float:
        self.runs.append(candidate)
        return self.scores.get(candidate, 0.0)


#: The kit by name; a task adds its own fake with ``FAKES.register(factory, "name")``.
FAKES: Registry[Callable[..., Any]] = Registry("eval fake", order="registered")
for _name, _factory in (
    ("clock", FakeClock), ("agent", FakeAgent), ("meters", FakeMeters),
    ("control_plane", FakeEndpoint), ("hub", FakeEndpoint), ("bench", FakeBench),
    ("vendor_tool", FakeVendorTool),
):  # fmt: skip
    FAKES.register(_factory, _name)


def kit(*names: str, **options: Mapping[str, Any]) -> dict[str, Any]:
    """Fresh fakes by name (all of them with no names); ``options[name]`` are the keyword
    arguments for that one. An unknown name is a `core.registry.UnknownName`."""
    return {n: FAKES[n](**dict(options.get(n, {}))) for n in (names or FAKES.names())}
