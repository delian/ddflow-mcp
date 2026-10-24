"""`python -m ddflow.surfaces.tool_table [README.md] [--force]`: rewrite the README's MCP tool
table from the registry (`api/readme_tools.py` renders it; B-uni-cmd-core)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ..api import readme_tools as RT
from .mcp import _OPTIONAL_KEYS, TOOLS
from .registry import result_schemas
from .tools import families as F
from .tools import profiles as P
from .tools.tiers import CORE_TOOLS, STANDARD_EXTRA_TOOLS


def tier_of(tool: str) -> str:
    """The smallest tier that lists `tool`."""
    if tool in CORE_TOOLS:
        return "core"
    return "standard" if tool in STANDARD_EXTRA_TOOLS else "all"


def overview() -> str:
    """The profiles and the families, from the live tables (`tools/profiles.py`, `families.py`)."""
    profiles = [
        f"- `{p}` ({len(P.advertised_names(p))} tools){note}"
        for p, note in (
            ("lean", ": the default (`[mcp].tools = auto`): the 9 hot singles and six families"),
            ("families", ": every family beside the hot singles"),
            ("hot+families", ": the 12 most called direct tools as well"),
        )
    ]
    families = []
    for name, fam in F.FAMILIES.items():
        if fam.single:
            continue
        actions = ", ".join(
            f"`{a}`" if old == name else f"`{a}` (was `{old}`)" for a, old in fam.members.items()
        )
        families.append(f"- `{name}` ({len(fam.members)} actions): {actions}")
    return "\n".join(
        [
            "**Profiles** (`[mcp].tools`; nothing a profile hides is removed: every tool and "
            "every old name stays callable):",
            "",
            *profiles,
            "- `core`, `standard` and `all`: the flat tiers, every tool by its own name",
            "",
            "**Families**: one tool with an `action`; the old tool name keeps working as a hidden "
            "alias, said once per session:",
            "",
            *families,
        ]
    )


def render() -> str:
    """The region for the live registry."""
    return RT.render(TOOLS, {t: tier_of(t) for t in TOOLS}, overview())


def schemas() -> str:
    """Every tool's declared result schema as JSON (the table `tests/golden/json` pins)."""
    return json.dumps(result_schemas(TOOLS, _OPTIONAL_KEYS), indent=1) + "\n"


def main(argv: list[str]) -> int:
    if "--schemas" in argv:
        sys.stdout.write(schemas())
        return 0
    paths = [a for a in argv if a != "--force"]
    code, message = RT.refresh(
        Path(paths[0] if paths else "README.md"), render(), force="--force" in argv
    )
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
