"""The `[lanes]` section: the lane ledger and advisor (`ddflow lanes`).

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob


@declare("lanes")
@dataclass
class LanesConfig:
    """The lane ledger: which agents work this project, in what state, and what to do next."""

    window_s: int = knob(
        86400,
        doc="How far back a lane's last event may be for `ddflow lanes` to list it (a lane holding a live lease is always listed). Older agents are history, not lanes.",
    )
    stale_after_s: int = knob(
        0,
        doc="Seconds without a heartbeat after which a lane that still holds its lease is reported `stale` instead of `working`. 0 (the default) uses lease.ttl_s, the moment the lease itself is due to lapse.",
    )
    watch_interval_s: int = knob(
        5,
        doc="Seconds between redraws of `ddflow lanes --watch` when the log has not changed; a change in the log redraws at once. The watch only reads the log: it starts no daemon and spawns nothing.",
    )
    advise_max: int = knob(
        10,
        doc="How many moves `ddflow lanes advise` prints, most urgent first (stopped lanes with unfinished work, then glob stalls, then idle lanes beside ready work).",
    )
    brief_notes: int = knob(
        3,
        doc="How many of a lane's most recent session notes `ddflow lanes brief` quotes, fenced as data, under 'Previous run'. 0 quotes none.",
    )
    brief_text_chars: int = knob(
        1200,
        doc="Characters kept of each text a lane brief quotes (the objective, the last session summary, a note), cut at a line boundary.",
    )
    stop_grace_s: int = knob(
        10,
        doc="Seconds a lane driver waits after SIGTERM to a lane's process group before SIGKILL (`Lane.stop`, process-headless). 0 kills at once.",
    )
    event_buffer: int = knob(
        1000,
        doc="How many canonical events a lane driver keeps unread per lane; when a reader falls behind, the oldest are dropped and counted in the lane status (`dropped`), never silently.",
    )
    scope: str = knob(
        "none",
        doc="Extra containment for a launched lane beyond its process group: 'none' (the group alone) or 'systemd' (`systemd-run --user --scope`, so a cgroup holds everything the lane started). 'systemd' where it is not available makes the start report `unavailable`; it never falls back silently.",
        choices=("none", "systemd"),
        strictest=(
            "systemd",
            "the most containment; a start where systemd-run is missing then reports unavailable instead of running uncontained",
        ),
    )
    pass_env: list[str] = knob(
        factory=lambda: ["PATH", "LANG", "LC_ALL", "TERM", "TMPDIR"],
        doc="Environment variable NAMES a launched lane inherits from the launching process, besides its own private home and the key names its launch spec lists. Everything else is dropped, so the operator's credentials and settings do not reach the lane.",
    )
    endpoint_slots: int = knob(
        1,
        doc="Lanes a local endpoint serves at once when its route declares no `concurrency` fact and `[schedule] resources` names no capacity for it (pool `ep-<route>`). The scheduler never plans more lanes than this on one endpoint, and a launched lane takes one flock slot of it.",
        check=lambda v: (
            ""
            if isinstance(v, int) and not isinstance(v, bool) and v >= 1
            else "a whole number of one or more"
        ),
    )
    slot_wait_s: int = knob(
        600,
        doc="Seconds a launched lane waits for a free endpoint or GPU slot before giving up with a report (it holds no lease while it waits and starts no daemon). 0 tries each slot once.",
        check=lambda v: (
            ""
            if isinstance(v, int) and not isinstance(v, bool) and v >= 0
            else "a whole number of zero or more"
        ),
    )
