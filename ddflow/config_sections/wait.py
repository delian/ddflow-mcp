"""The `[wait]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, int_at_least, knob


@declare("wait")
@dataclass
class WaitConfig:
    """Waiting in line, at the cost of the agent that waits."""

    max_call_seconds: int = knob(
        240,
        doc="Longest ONE `ddflow wait` (or `watch`) call blocks before it returns a still-waiting result the caller re-invokes, so a single call never outlives this bound and the caller's heartbeat rides on the next call (D-TE-Q3: the 240 s cap applies always, not only as fallback). A 5-minute subagent prompt cache stays warm under it, and ten minutes waited as one call costs ~4x three short calls. `[lease].wait_cap_s` is the hard ceiling that sits below the client's idle abort; whichever is shorter wins, and a caller's own `--max-s` can still shorten a call (never lengthen it past this cap).",
        check=int_at_least(1),
    )
