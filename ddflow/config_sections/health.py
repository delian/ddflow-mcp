"""The `[health]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, int_at_least, knob


@declare("health")
@dataclass
class HealthConfig:
    """The watchdog: idle, stalled, unresponsive, locked, crashed and runaway lanes, found and corrected."""

    mode: str = knob(
        "propose",
        doc="What the watchdog does about a lane it finds unhealthy: 'off' inspects nothing in the periodic pass; 'propose' (default) reports each finding with the exact command that corrects it; 'apply' also performs the corrections that lose nothing -- it nudges an idle lane, asks a stalled lane to stop and, once that request has gone unanswered for reply_s, PARKS the holder (a logged park record: worktree, branch and the lane it waits for) and only then breaks its lease for the agent waiting (the same logged break as `claim --break`, the holder's branch and worktree kept), adds the auto-order `needs` edges that end a lease deadlock (this mode decides for the pass, whatever [schedule].auto_order says), and records a crashed lane's lease as expired so `ddflow recover` and any claim can pick its work up. It never kills a process and never deletes a worktree, a branch or a commit. This is the default for every finding kind; mode_idle, mode_stalled, mode_unresponsive, mode_locked, mode_crashed and mode_runaway override it for one kind.",
        choices=("off", "propose", "apply"),
        strictest=("propose", "a finding is reported, never acted on, by a typo"),
    )
    stall_s: int = knob(
        3600,
        doc="A lane whose lease is current but which has shown no progress (an event on its item other than lease bookkeeping, a commit, a test job in flight, a change in its worktree since the lease began) for this many seconds is STALLED. 0 turns the stalled check off. A heartbeat is not progress, and neither is a file that was changed once and never again.",
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
    idle_s: int = knob(
        900,
        doc="A lane whose lease keeps being renewed but which has shown no progress for this many seconds is IDLE (earlier than STALLED, which is stall_s): the correction is a nudge, nothing is taken. 0 turns the check off.",
        check=int_at_least(0),
    )
    reply_s: int = knob(
        600,
        doc="How long a nudge or stop request to a lane may go unanswered (no progress, no release) before the lane is UNRESPONSIVE, which is what lets the watchdog park it and break its lease. 0 turns the check off and the park-and-break follows the stop request at once.",
        check=int_at_least(0),
    )
    crash_s: int = knob(
        300,
        doc="A lane whose holder's process is gone (the harness that declared its identity has exited; `ddflow_identify` records it) and whose lease has not been renewed for this many seconds is CRASHED, minutes before the lease TTL lapses. Only a holder that declared its identity can be seen to die; others fall back to the lease TTL. 0 turns the check off.",
        check=int_at_least(0),
    )
    mode_idle: str = knob(
        "inherit",
        doc="The mode for an idle lane (nudge): 'inherit' (default) takes [health].mode; otherwise 'off', 'propose' or 'apply' as for mode, for this finding kind alone.",
        choices=("inherit", "off", "propose", "apply"),
        strictest=("propose", "a finding is reported, never acted on, by a typo"),
    )
    mode_stalled: str = knob(
        "inherit",
        doc="The mode for a stalled lane (stop request): 'inherit' (default) takes [health].mode; otherwise 'off', 'propose' or 'apply' as for mode, for this finding kind alone.",
        choices=("inherit", "off", "propose", "apply"),
        strictest=("propose", "a finding is reported, never acted on, by a typo"),
    )
    mode_unresponsive: str = knob(
        "inherit",
        doc="The mode for an unresponsive lane (park, then break): 'inherit' (default) takes [health].mode; otherwise 'off', 'propose' or 'apply' as for mode, for this finding kind alone.",
        choices=("inherit", "off", "propose", "apply"),
        strictest=("propose", "a finding is reported, never acted on, by a typo"),
    )
    mode_locked: str = knob(
        "inherit",
        doc="The mode for a lease deadlock (the auto-order edges): 'inherit' (default) takes [health].mode; otherwise 'off', 'propose' or 'apply' as for mode, for this finding kind alone.",
        choices=("inherit", "off", "propose", "apply"),
        strictest=("propose", "a finding is reported, never acted on, by a typo"),
    )
    mode_crashed: str = knob(
        "inherit",
        doc="The mode for a crashed lane (record its lease expired): 'inherit' (default) takes [health].mode; otherwise 'off', 'propose' or 'apply' as for mode, for this finding kind alone.",
        choices=("inherit", "off", "propose", "apply"),
        strictest=("propose", "a finding is reported, never acted on, by a typo"),
    )
    mode_runaway: str = knob(
        "inherit",
        doc="The mode for a runaway job (report only): 'inherit' (default) takes [health].mode; otherwise 'off', 'propose' or 'apply' as for mode, for this finding kind alone.",
        choices=("inherit", "off", "propose", "apply"),
        strictest=("propose", "a finding is reported, never acted on, by a typo"),
    )
