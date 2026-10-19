"""`python -m ddflow.surfaces.harness_table [README.md]`: rewrite the README's table of supported
agents and the ladder level each gets, from the harness descriptors (B-hx-guide).

The table used to be written by hand and listed six agents while the descriptors held
twenty-two. It is rendered from `ddflow/harnesses/*.toml`, so adding a descriptor adds the row
when the pin recipe re-records it."""

from __future__ import annotations

import re
import sys

from ..api.harness import agent_table
from ..views.readme_region import Region

REGION = "README/agents"


def render(_readme: str = "") -> str:
    """The region, markers included: one row per descriptor."""
    lines = [
        "| Agent | Id | MCP config written by `adopt` | Hook style | Best level |",
        "|---|---|---|---|---|",
    ]
    for title, hid, mcp, style, level in agent_table():
        cell = f"`{mcp}`" if mcp != "none" else "none"
        lines.append(f"| {title} | `{hid}` | {cell} | {style} | {level} |")
    lines += [
        "",
        "One row per `ddflow/harnesses/<id>.toml`; `ddflow harness show <id>` says what each "
        "agent gets, event by event. To add an agent see "
        "[`docs/ddflow/adding-a-harness.md`](docs/ddflow/adding-a-harness.md).",
    ]
    return _REGION.block(re.sub(r"[ \t]+\n", "\n", "\n".join(lines)))


_REGION = Region(REGION, "agents table", render)
replace = _REGION.replace
hand_edited = _REGION.hand_edited


def main(argv: list[str]) -> int:
    return _REGION.main(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
