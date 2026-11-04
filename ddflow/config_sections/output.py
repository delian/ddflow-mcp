"""The `[output]` section: how much a top-level read command prints by default.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, int_at_least, knob


@declare("output")
@dataclass
class OutputConfig:
    """Default size caps of `board`, `doctor`, `recover`, `next` and `status` (CLI and MCP).

    A capped answer ends in a footer naming the exact next call; `--full` (MCP `full=true`)
    lifts every cap for that call. Settable in the config files, in the environment
    (`DDFLOW_OUTPUT_MAX_ROWS`), as `ddflow config set`, and per call with `--full`."""

    max_rows: int = knob(
        50,
        doc="Rows a list shows by default in `board`, `doctor`, `recover`, `next` and `status` (CLI and MCP, text and `--json`). The rest is counted: the answer says how many more there are, and the exact call (`--cursor N`, `--phase ID`, `--full`) that shows them. 0 lifts the row cap for every call, as `--full` does for one.",
        check=int_at_least(0),
    )
    max_chars: int = knob(
        30000,
        doc="Characters a default answer of those commands may hold (text and serialised `--json` alike; about 4 characters a token, so 30000 is roughly 7500 tokens, well under the 25000-token cap an MCP client puts on a tool result). A `--json` body that carries both prose and rows (`board`) gives each half. A longer answer is cut on a line boundary and ends in a footer; a `--json` body first loses rows until it fits and keeps its schema, adding `truncated` and `next`. 0 lifts the character cap.",
        check=int_at_least(0),
    )
    allow_full: bool = knob(
        True,
        doc="Whether `--full` / `full=true` may lift the caps. An organisation that must keep every answer bounded (a shared MCP host, a context-limited fleet) sets it to false in its org config layer: a `full` request then still gets the capped answer and a footer saying the lift is off, so nothing is silently different. Default true: a person at a terminal asking for everything gets everything.",
    )
