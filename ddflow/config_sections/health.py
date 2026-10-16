"""The `[health]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, int_at_least, knob


@declare("health")
@dataclass
class HealthConfig:
    """The watchdog: stalled, locked, crashed and runaway lanes, found and corrected."""

    mode: str = knob(
        "propose",
        doc="What the watchdog does about a lane it finds unhealthy: 'off' inspects nothing in the periodic pass; 'propose' (default) reports each finding with the exact command that corrects it; 'apply' also performs the corrections that lose nothing -- it breaks a stalled lease another agent is waiting for (the same logged break as `claim --break`, the holder's branch and worktree kept), adds the auto-order `needs` edges that end a lease deadlock (this mode decides for the pass, whatever [schedule].auto_order says), and records a crashed lane's lease as expired so `ddflow recover` and any claim can pick its work up. It never kills a process and never deletes a worktree, a branch or a commit.",
        choices=("off", "propose", "apply"),
        strictest=("propose", "a finding is reported, never acted on, by a typo"),
    )
    stall_s: int = knob(
        3600,
        doc="A lane whose lease is current but which has shown no progress (an event on its item other than lease bookkeeping, a commit, an uncommitted change in its worktree) for this many seconds is STALLED. 0 turns the stalled check off. A heartbeat is not progress.",
        check=int_at_least(0),
    )
    runaway_s: int = knob(
        14400,
        doc="A job started for an item whose process is still running after this many seconds is a RUNAWAY. It is only reported: the watchdog never signals a process. 0 turns the check off.",
        check=int_at_least(0),
    )
    interval_s: int = knob(
        900,
        doc="How often the periodic watchdog pass falls due, in seconds: `ddflow cadence` lists 'health_watch' as due when `ddflow health --run` has not been recorded within this long, and `--run` is the pass a scheduler, loop or hook invokes. 0 never falls due.",
        check=int_at_least(0),
    )
    shown: int = knob(
        10,
        doc="How many findings `doctor` and `status` print in full; the rest are counted. 0 prints none.",
        check=int_at_least(0),
    )
