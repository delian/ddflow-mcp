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
    lifts every cap for that call. Settable in the config files, with `ddflow config output.max_rows N`, over MCP with
    `ddflow_configure`, in the environment (`DDFLOW_OUTPUT_MAX_ROWS`), and per call with
    `--full`."""

    max_rows: int = knob(
        50,
        doc="Rows a list shows by default in `board`, `doctor`, `recover`, `next` and `status` (CLI and MCP, text and `--json`). The rest is counted: the answer says how many more there are, and the exact call that shows them. 0 lifts the row cap for every call, as `--full` does for one, unless `allow_full` is false.",
        check=int_at_least(0),
    )
    max_chars: int = knob(
        30000,
        doc="Characters a default answer of those commands may hold (text and serialised `--json` alike; about 4 characters a token for ASCII text only, so 30000 is roughly 7500 tokens of English; non-ASCII prose costs more, see `non_ascii_weight`; well under the 25000-token cap an MCP client puts on a tool result). A `--json` body that carries both prose and rows (`board`) gives each half. A longer answer is cut on a line boundary and ends in a footer; a `--json` body first loses rows until it fits and keeps its schema, adding `truncated` and `next`. 0 lifts the character cap, unless `allow_full` is false.",
        check=int_at_least(0),
    )
    non_ascii_weight: int = knob(
        4,
        doc="How many characters of `max_chars` one non-ASCII character of prose costs. A character count is only a token estimate for ASCII: 30000 characters measured 5737 tokens of English, 52366 of rare CJK and 84470 of emoji with the Qwen tokenizer, so at weight 1 a non-ASCII report can pass the 25000-token cap on a tool result. At 4 the same cap keeps 7500 such characters, about 21000 tokens at the worst measured rate. 1 counts plain characters.",
        check=int_at_least(1),
    )
    allow_full: bool = knob(
        True,
        doc="Whether `--full` / `full=true` may lift the caps, and whether 0 in `max_rows` or `max_chars` does. Set it to false in the committed config to keep every answer bounded (a shared MCP host, a context-limited fleet): a `full` request then gets the capped answer, and every cut answer says the lift is off, so nothing is silently different. This is a setting, not an enforced lock: ddflow has no organisation layer yet, so the local layer or `DDFLOW_OUTPUT_ALLOW_FULL` can still override it. Default true: a person at a terminal asking for everything gets everything.",
    )
