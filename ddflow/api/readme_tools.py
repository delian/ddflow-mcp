"""The README's MCP tool table, rendered from the tool registry (B-uni-cmd-core).

The README said "92 tools" while the registry held 107: a number and a list kept by hand in
a second place drift. The `README/tools` region is rendered from the registry itself --
every tool, the help group it is filed under (`services.help.grouped_tools`), the tier that
lists it and the first sentence of its description -- with the counts for the whole list and
for each tier. Its markers follow D-doc-regions:
`<!-- ddflow:begin README/tools sha=<12 hex> -->` ... `<!-- ddflow:end README/tools -->`,
`sha` being the digest of the body between them as rendered.

The registry and its tiers belong to the MCP surface, which hands them IN (`surfaces/
tool_table.py` is the command); `tests/test_readme_tool_table.py` fails when the README's
block is not what this renders; rewrite it with

    uv run python -m ddflow.surfaces.tool_table README.md
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from ..core.digest import content_digest
from ..core.outcome import OK, REFUSED, Verdict
from ..infra.fsio import Region, atomic_write
from ..services.help import grouped_tools

REGION = "README/tools"
END = f"<!-- ddflow:end {REGION} -->"
_BEGIN = rf"<!-- ddflow:begin {re.escape(REGION)} sha=(?P<sha>[0-9a-f]{{12}}) -->"
#: The region's lines, found by the one line-splice primitive every managed block uses.
_LINES = Region(_BEGIN, re.escape(END))
#: Whitespace after `.`, `!` or `?` that is not the tail of an abbreviation or an ellipsis.
_SENTENCE_END = re.compile(
    r"(?<![Ee]\.g\.)(?<!i\.e\.)(?<!etc\.)(?<!vs\.)(?<!\bNo\.)(?<!\.\.)(?<=[.!?])\s+"
)
#: A summary longer than this is cut at a word and ends in an ellipsis.
SUMMARY_MAX = 110
#: The rewrite was refused: the region was edited by hand.
EDITED = REFUSED


def summary(description: str) -> str:
    """The first sentence of a tool's description, as a table cell. A full stop that ends
    `e.g.`, `i.e.`, `etc.`, `vs.`, `No.` or an ellipsis does not end the sentence."""
    first = _SENTENCE_END.split(" ".join(description.split()), maxsplit=1)[0]
    if len(first) > SUMMARY_MAX:
        first = first[: SUMMARY_MAX - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return first.replace("|", "\\|")


def render(tools: Mapping[str, Mapping[str, object]], tiers: Mapping[str, str]) -> str:
    """The region, markers included: the counts, then one row per tool in help-group order.
    ``tiers`` maps each tool to the smallest tier that lists it (core | standard | all)."""
    rows = []
    for group, members in grouped_tools(tools):
        rows += [
            f"| {group} | `{t}` | {tiers[t]} | {summary(str(tools[t]['description']))} |"
            for t in members
        ]
    core = sum(tiers[t] == "core" for t in tools)
    standard = core + sum(tiers[t] == "standard" for t in tools)
    body = "\n".join(
        [
            f"<details><summary>All {len(tools)} MCP tools: {core} in the `core` tier, "
            f"{standard - core} more in `standard`, {len(tools) - standard} more in `all`"
            "</summary>",
            "",
            "| Group | Tool | Tier | What it does |",
            "|---|---|---|---|",
            *rows,
            "",
            "</details>",
        ]
    )
    return f"<!-- ddflow:begin {REGION} sha={content_digest(body, length=12)} -->\n{body}\n{END}"


def _found(readme: str) -> tuple[int, int, int, int]:
    """The region's ``(start, body_start, body_end, stop)`` offsets, or ValueError."""
    at = _LINES.find(readme)
    if at is None:
        raise ValueError(f"README has no {REGION} region (ddflow:begin ... {END})")
    return at


def replace(readme: str, block: str) -> str:
    """`readme` with its `README/tools` region (markers included) replaced by `block`."""
    _found(readme)
    return _LINES.splice(readme, block + "\n")


def hand_edited(readme: str) -> bool:
    """Does the region's body no longer hash to the `sha` its begin marker recorded?"""
    start, body_start, body_end, _stop = _found(readme)
    sha = re.search(r"sha=([0-9a-f]{12})", readme[start:body_start])
    body = readme[body_start:body_end].replace("\r\n", "\n").removesuffix("\n")
    return sha is None or content_digest(body, length=12) != sha[1]


def refresh(path: Path, block: str, *, force: bool = False) -> Verdict:
    """Rewrite the region of the README at `path`: ``(exit, message)``.

    A region edited by hand is refused (`EDITED`) unless ``force``: the table is the
    registry's to change, not the README's."""
    text = path.read_text("utf-8")
    new = replace(text, block)
    if new == text:
        return Verdict(OK, f"{path}: tool table already current")
    if hand_edited(text) and not force:
        return Verdict(
            EDITED,
            f"{path}: the {REGION} region was edited by hand (its body no longer matches its "
            "sha). Move the edit outside the region (or into the tool declarations), then "
            "rerun; --force rewrites the region and discards the edit",
        )
    atomic_write(path, new)
    return Verdict(OK, f"{path}: tool table rewritten")
