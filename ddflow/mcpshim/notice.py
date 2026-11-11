"""The restart-required notice (SU-restart-notice).

The shim remembers the tool list it served in the connection's last `tools/list`. When the
worker's list differs and the client is not known to honour `list_changed` (or did not
acknowledge the announcement), tool results carry ONE line saying what changed and to restart
the agent, and a call to a tool the upgrade removed gets the same text instead of a bare
"no such tool". `message` is the one text `ddflow doctor` and the SessionStart hook are to say too
(SU-upgrade-integration wires them; this module has no caller until it and the shim land).

Rate limited, never silent: the first result after a change carries it at once, then it repeats
at most once per `every_calls` results AND `every_seconds`; a different change is news again.
Both are the caller's (the shim passes `[reinstruct]`'s cadence); a value below 1 call or below 0
seconds is clamped, so a mistaken knob can neither mute the footer nor make it a crash.

Stdlib only, pure: the shim imports this module and hands in the lists, the client's capability
(`honours` | `ignores` | `unknown`, from `mcpshim.clients`), the cadence and the clock
(`time.monotonic`).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .clients import HONOURS


@dataclass(frozen=True)
class Delta:
    added: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.added or self.changed or self.removed)


def _by_name(tools: Any) -> dict[str, str]:
    """`{name: canonical form of what a client sees of the tool}`; malformed entries are skipped."""
    out: dict[str, str] = {}
    if not isinstance(tools, list):
        return out
    for t in tools:
        if isinstance(t, dict) and isinstance(t.get("name"), str) and t["name"]:
            seen = {k: t.get(k) for k in ("description", "inputSchema", "outputSchema")}
            try:
                out[t["name"]] = json.dumps(seen, sort_keys=True, default=str)
            except (TypeError, ValueError):
                out[t["name"]] = repr(seen)
    return out


def delta(served: Any, live: Any) -> Delta:
    """What the worker's `live` tool list has that the client's `served` one lacks."""
    if not (isinstance(served, list) and isinstance(live, list)):
        return Delta()  # a list not loaded yet is no baseline, not an empty one
    old, new = _by_name(served), _by_name(live)
    return Delta(
        added=tuple(sorted(new.keys() - old.keys())),
        changed=tuple(sorted(n for n in old.keys() & new.keys() if old[n] != new[n])),
        removed=tuple(sorted(old.keys() - new.keys())),
    )


def message(d: Delta) -> str:
    """The one-line notice for `d`, or "" when nothing changed."""
    if not d:
        return ""
    parts = []
    if d.added:
        parts.append(f"{len(d.added)} tool{'s' * (len(d.added) != 1)} added")
    if d.changed:
        parts.append(f"{len(d.changed)} changed")
    if d.removed:
        parts.append(f"{len(d.removed)} removed")
    many = len(d.added) + len(d.changed) + len(d.removed) != 1
    return f"ddflow was upgraded ({', '.join(parts)}): restart your agent to see {'them' if many else 'it'}"


def removed_tool(name: Any, served: Any, live: Any) -> str | None:
    """The message for a call to `name` when the upgrade removed it (the client's `served` list
    has it, the worker's `live` one does not): exactly `message` of the change, the line every
    surface says. None for an ordinary unknown tool. The name is never echoed."""
    if not (isinstance(served, list) and isinstance(live, list) and isinstance(name, str)):
        return None
    if name not in _by_name(served) or name in _by_name(live):
        return None
    return message(delta(served, live))


class RestartNotice:
    """Per connection: decides whether a tool result carries the footer."""

    def __init__(
        self,
        *,
        every_calls: int,
        every_seconds: float,
        clock: Callable[[], float],
    ) -> None:
        self._calls = max(1, int(every_calls))
        self._seconds = max(0.0, float(every_seconds))
        self._clock = clock
        self._said: Delta | None = None  # what the last footer announced
        self._since = 0  # results since the last footer
        self._at = 0.0

    def footer(
        self,
        client: Any,
        served: Any,
        live: Any,
        *,
        unacknowledged: bool = False,
    ) -> str:
        """The footer for the next tool result, or "". `client` is the capability of the
        connected client; `unacknowledged` says an honouring client did not re-fetch the list."""
        d = delta(served, live)
        if not d or (client == HONOURS and not unacknowledged):
            self._said, self._since = None, 0  # caught up (or told another way): start over
            return ""
        self._since += 1
        now = self._clock()
        news = d != self._said
        if not news and (self._since < self._calls or now - self._at < self._seconds):
            return ""
        self._said, self._since, self._at = d, 0, now
        return message(d)
