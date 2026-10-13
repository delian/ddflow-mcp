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
