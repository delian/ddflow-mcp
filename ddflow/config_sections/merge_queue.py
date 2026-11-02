"""The `[merge_queue]` section: how the single integrator lands queued items.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, int_at_least, knob


@declare("merge_queue")
@dataclass
class MergeQueueConfig:
    """The merge queue (`ddflow queue`): agents enqueue a finished
    item and one integrator at a time lands the queue head."""

    max_batch: int = knob(
        5,
        doc="How many queue entries one `ddflow queue run` integrates before it returns (each merges main into its branch, regenerates the pins, runs the ci gate once and merges, strictly in queue order). A run takes at most this many even when more are waiting; run it again for the rest. `ddflow queue run --max-batch N` and the `max_batch` argument of `ddflow_queue` override it for one run.",
        check=int_at_least(1),
    )
    run_unit_tests: bool = knob(
        False,
        doc="Whether the integrator also runs each entry's `unit_tests` gate on the merged result, beside ci. Off by default: the ci gate is the check run once on the merged result. Every other gate of the item's pipeline must already carry an outcome before an entry runs, whatever this says. `--unit-tests` / `--no-unit-tests` on `ddflow queue run` override it for one run.",
    )
    timeout_s: int = knob(
        0,
        doc="Seconds the integrator lets one entry's ci run before it stops it and parks the entry. 0 (default) uses `[ci].timeout_s`. `ddflow queue run --timeout N` overrides it for one run.",
        check=int_at_least(0),
    )
    eta_default_s: int = knob(
        900,
        doc="Seconds `ddflow queue status` assumes one entry takes to integrate while no entry has been integrated yet, to estimate when each queued entry will land.",
        check=int_at_least(1),
    )
    eta_history: int = knob(
        5,
        doc="How many of the most recent integrated entries `ddflow queue status` averages to estimate how long one entry takes.",
        check=int_at_least(1),
    )
    keep_history: int = knob(
        50,
        doc="How many finished (merged or parked) entries the queue keeps for `ddflow queue status` and the ETA; older ones are deleted when an entry is added.",
        check=int_at_least(0),
    )
    auto_run: str = knob(
        "on",
        doc="Whether ddflow itself starts the integrator (`ddflow queue run`) so a queued entry never waits with nobody to land it. 'on': accepting a release at enqueue starts one detached integrator run through the job machinery (a job with its log path; none is started while one is alive), and a tick started by any ddflow command starts it again when an entry has waited longer than `auto_run_after_s` with no integrator alive. 'off': nothing is started; `ddflow queue run` by hand still works, and `brief`, `queue status` and `doctor` still warn when entries wait with nobody integrating. No daemon is involved.",
        choices=("on", "off"),
        strictest=("off", "ddflow starts no process on its own; the queue is run by hand"),
    )
    auto_run_after_s: int = knob(
        120,
        doc="Seconds a queued entry waits with no integrator alive before the tick starts one, and before `brief`, `queue status` and `doctor` call the wait a problem. Applies when `auto_run` is on (the warning applies always).",
        check=int_at_least(0),
    )
    auto_run_max_parallel: int = knob(
        1,
        doc="How many automatically started integrator jobs may be alive at once. The queue's own lock still lets only one integrate, so more than 1 only matters for a very short overlap while one ends and the next starts.",
        check=int_at_least(1),
    )
