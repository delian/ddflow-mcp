"""The `[quota]` section: where the usage meters read and how far they may go.

Re-exported from `ddflow.config`, which assembles `Config` from every section. Read by
`ddflow.services.meters_base.context`. The enforcement knobs arrive with their reader
(B-quota-pause); these are the meters' own (D-quotas-governor)."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob

#: The windows a counter can total over: the keys of `services.quota.WINDOWS`.
TOKEN_WINDOWS = ("5h", "day", "week", "month", "total")
#: The token kinds a total can include.
TOKEN_FIELDS = ("input", "output", "cache_read", "cache_write")


def _positive(value: object) -> str:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
    return "" if ok else "a number above zero"


def _subset(allowed: tuple[str, ...]):
    def check(value: object) -> str:
        ok = isinstance(value, list) and bool(value) and all(v in allowed for v in value)
        ok = ok and len(set(value)) == len(value)  # a repeat would count the same tokens twice
        return "" if ok else f"a non-empty list, no repeats, drawn from {', '.join(allowed)}"

    return check


@declare("quota")
@dataclass
class QuotaConfig:
    """The usage meters: vendor percent where a vendor reports it, local counts elsewhere."""

    stale_after_s: int = knob(
        900,
        doc="Seconds after which a vendor percent reading counts as stale (default 900). A reading is also stale once its window's reset has passed. A stale figure keeps its value and carries `stale: true`: it is never presented as current.",
        check=_positive,
    )
    claude_state: str = knob(
        "",
        doc="The harness state file holding `cachedUsageUtilization`, the vendor's cached percent and reset per window. Empty (default): the path in `templates/meter_sources.toml`. ddflow only reads it and never calls the vendor's usage endpoint with the user's credentials (D-quotas-governor).",
    )
    claude_projects: str = knob(
        "",
        doc="The directory of harness transcripts, read for token counts, subagent transcripts included. Empty (default): the path in `templates/meter_sources.toml`.",
    )
    kilo_db: str = knob(
        "",
        doc="The SQLite message store, opened read-only for token and cost counts per message. Empty (default): the path in `templates/meter_sources.toml`.",
    )
    snapshot_path: str = knob(
        "",
        doc="The file holding the latest vendor percent taken from a statusline or stream-json event. Empty (default): `vendor-usage.json` in the user's state directory (`$XDG_STATE_HOME/ddflow`, else `~/.local/state/ddflow`), because several projects share one account.",
    )
    samples_path: str = knob(
        "",
        doc="The local usage sample log. Empty (default): `usage-samples.jsonl` in the user's state directory, for the same reason. It holds figures only, never prompt text or a raw account id.",
    )
    samples_max_bytes: int = knob(
        4 * 1024 * 1024,
        doc="The size the sample log may reach before its oldest rows are dropped (default 4 MiB). Trimming keeps the newest rows up to half this size.",
        check=_positive,
    )
    sample_interval_s: int = knob(
        300,
        doc="The shortest time between two samples taken by `sample_if_due` (default 300).",
        check=_positive,
    )
    sample_tail_bytes: int = knob(
        8192,
        doc="How much of the sample log's end `sample_if_due` reads to find its last row (default 8192). A row longer than this reads as no last row, so a sample is taken.",
        check=_positive,
    )
    token_windows: list[str] = knob(
        factory=lambda: ["5h", "day", "week"],
        doc="The rolling windows the token and session counters total over: any of 5h, day, week, month, total. A longer window reads more: `total` reads every transcript and is usually over `transcript_max_bytes`, which then reports UNAVAILABLE.",
        check=_subset(TOKEN_WINDOWS),
    )
    token_fields: list[str] = knob(
        factory=lambda: list(TOKEN_FIELDS),
        doc="Which token kinds a reading's total includes: any of input, output, cache_read, cache_write (default all four). The per-kind figures stay in the reading's detail either way.",
        check=_subset(TOKEN_FIELDS),
    )
    transcript_max_bytes: int = knob(
        1024 * 1024 * 1024,
        doc="The most transcript bytes one reading may scan (default 1 GiB). Over it the reading is UNAVAILABLE with this knob named, never a partial sum that would understate the use.",
        check=_positive,
    )
    kilo_timeout_s: float = knob(
        10.0,
        doc="Seconds one read of the message store may take (default 10). Over it the reading is UNAVAILABLE.",
        check=_positive,
    )
