"""The `[lanes]` section: the lane ledger and advisor (`ddflow lanes`).

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob


def _whole(v: object) -> str:
    ok = isinstance(v, int) and not isinstance(v, bool) and v >= 0
    return "" if ok else "a whole number of zero or more"


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
    fleet_window_s: int = knob(
        0,
        doc="How far back an agent's last event may be for `ddflow lanes fleet` to list it. 0 (the default) lists every agent the log has ever seen: the fleet registry is durable history, unlike the lane ledger's window_s.",
        check=_whole,
    )
    fleet_max_rows: int = knob(
        18,
        doc="Most agents `ddflow lanes fleet` lists: lost work first, then live work, then the most recently heard from. The cut says how many more there are. 0 lists every one.",
        check=_whole,
    )
    fleet_items: int = knob(
        5,
        doc="Items each agent's row in `ddflow lanes fleet` names, newest first; `items_total` still counts them all. 0 names none.",
        check=_whole,
    )
    fleet_failed_patterns: list[str] = knob(
        factory=lambda: [
            r"\b429\b",
            r"rate.?limit",
            r"spend.?limit",
            r"quota",
            r"overloaded",
            r"context (window|length|exhaust)",
            r"max.?tokens",
            r"crash",
            r"\bOOM\b",
        ],
        doc="Case-insensitive regular expressions: an agent that stopped with its item unfinished and whose recorded stop reason (session summary, lease expiry reason or release note) matches one is classed `failed` in `ddflow lanes fleet`; any other stop is `stalled`. A pattern that does not compile is skipped.",
    )
    fleet_reason_chars: int = knob(
        120,
        doc="Characters of a stop reason `ddflow lanes fleet` keeps and prints (made safe to print first). 0 prints none. The cut is for display only: failure patterns match the whole recorded reason.",
        check=_whole,
    )
    watch_interval_s: int = knob(
        5,
        doc="Seconds between redraws of `ddflow lanes --watch` when the log has not changed; a change in the log redraws at once. The watch only reads the log: it starts no daemon and spawns nothing.",
    )
    advise_max: int = knob(
        10,
        doc="How many moves `ddflow lanes advise` prints, most urgent first (stopped lanes with unfinished work, then glob stalls, then idle lanes beside ready work).",
    )
    idle_s: int = knob(
        1800,
        doc="Seconds a lane holding a lease may go without writing an event or heartbeating before `ddflow lanes --coordinator` flags it `idle`. 0 flags none.",
        check=_whole,
    )
    coordinator_max_rows: int = knob(
        15,
        doc="Most rows each section of `ddflow lanes --coordinator` (suites, holders, approvals, ready phases, chain) prints; the cut says how many more there are. 0 prints every row.",
        check=_whole,
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
        check=_whole,
    )
    release_mode: str = knob(
        "flag",
        doc="Release at enqueue (`ddflow queue add ITEM --release`): the agent hands the item to the merge queue, gives up its lease and may claim the next item; the integrator runs the remaining machine gates and merges. 'off' refuses every release, 'flag' releases only when `--release` is given, 'auto' releases every item that is eligible when it is enqueued. An item is eligible only when each judgement gate of `release_gates` is recorded passed on the current tree (or skipped with its reason).",
        choices=("off", "flag", "auto"),
        strictest=(
            "off",
            "no item is released: the agent keeps its lease until the item is merged",
        ),
    )
    release_max: int = knob(
        4,
        doc="How many released items may wait for the integrator at once. The count and the new place are taken under one lock, so two releases never both take the last slot; a release over the cap is refused with the count (the agent keeps its lease and may enqueue without `--release`).",
        check=lambda v: (
            ""
            if isinstance(v, int) and not isinstance(v, bool) and v >= 1
            else "a whole number of one or more"
        ),
    )
    release_gates: list[str] = knob(
        factory=lambda: ["rubber_duck", "critic", "bug_hunt", "dedupe"],
        doc="The judgement gates that must already be recorded passed (on the tree as it is now) or skipped before an item may be released at enqueue. A gate not in the item's pipeline is not asked.",
    )
    release_machine_gates: list[str] = knob(
        factory=lambda: ["standards"],
        doc="Gates, besides ci and (by `[merge_queue].run_unit_tests`) unit_tests, that the integrator runs itself for a released item when they carry no passed outcome. Every other gate must carry an outcome before release.",
    )
