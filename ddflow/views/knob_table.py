"""The README's knob reference, generated from the knobs themselves (B-uni-knobs-readme).

Every knob is a field of one of `Config`'s section dataclasses; its default is the field's
default and an enum knob's values are `KNOB_CHOICES`. The table in README.md's
`README/knobs` region is rendered from exactly that, with the count, so neither can drift
from the code. The region's markers follow D-doc-regions:
`<!-- ddflow:begin README/knobs sha=<12 hex> -->` ... `<!-- ddflow:end README/knobs -->`,
`sha` being the digest of the body between them as rendered. tests/test_readme_knob_table.py fails when the README's block is not what this
renders; rewrite it with

    uv run python -m ddflow.views.knob_table README.md
"""

from __future__ import annotations

import re
import sys
from dataclasses import fields
from pathlib import Path

from ..config import KNOB_CHOICES, Config
from ..infra.fsio import atomic_write
from ..infra.tomlcfg import value
from .readme_region import Region

REGION = "README/knobs"
#: A default longer than this is not shown in a cell: `config --explain` prints it whole.
SHOWN = 40


def _default(v: object) -> str:
    """A default as TOML spells it, in a table cell; a long one is pointed at instead."""
    text = value(v)
    if len(text) > SHOWN:
        return "(long: see `ddflow config --explain`)"
    text = text.replace("|", "\\|")
    # A code span closes at the first backtick run as long as its fence: make the fence
    # one longer than the longest run inside, padded so a leading/trailing one is kept.
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    if not longest:
        return f"`{text}`"
    fence = "`" * (longest + 1)
    return f"{fence} {text} {fence}"


def render(cfg: Config | None = None, choices: dict[str, tuple[str, ...]] | None = None) -> str:
    """The region, markers included: the count, then one row per knob in `Config`'s
    order -- its key, its default and, for an enum knob, its values."""
    cfg = cfg or Config()
    choices = KNOB_CHOICES if choices is None else choices
    sections = list(cfg._sections())
    rows = []
    for sec in sections:
        for f in fields(getattr(cfg, sec)):
            key = f"{sec}.{f.name}"
            values = " \\| ".join(f"`{v}`" for v in choices.get(key, ()))
            rows.append(f"| `{key}` | {_default(getattr(getattr(cfg, sec), f.name))} | {values} |")
    body = "\n".join(
        [
            f"<details><summary>All {len(rows)} knobs across {len(sections)} sections</summary>",
            "",
            "| Knob | Default | Values |",
            "|---|---|---|",
            *rows,
            "",
            "</details>",
        ]
    )
    return _REGION.block(body)


_REGION = Region(REGION, "knob table", lambda _readme: render())
END = _REGION.end
replace = _REGION.replace
hand_edited = _REGION.hand_edited
_region = _REGION.match


def fix_counts(text: str, knobs: int, sections: int) -> str:
    """``text`` with every hand-written knob count made the true one: "N knobs [across M
    sections]" and "knobs (K of the N)". K, one section's share, is left alone."""
    text = re.sub(
        r"(\d+) knobs( across )?(\d+)?( sections)?",
        lambda m: f"{knobs} knobs" + (f"{m.group(2)}{sections}{m.group(4)}" if m.group(3) else ""),
        text,
    )
    return re.sub(
        r"(knobs\**[ ]*\n?[ ]*\(\d+ of the )\d+(\))", lambda m: f"{m.group(1)}{knobs}{m.group(2)}", text
    )


def main(argv: list[str]) -> int:
    """Rewrite the knob table of the README named in `argv` (default README.md), then the
    knob counts its prose states by hand."""
    code = _REGION.main(argv)
    if code:
        return code
    path = Path(([a for a in argv if a != "--force"] or ["README.md"])[0])
    cfg = Config()
    sections = list(cfg._sections())
    knobs = sum(len(fields(getattr(cfg, s))) for s in sections)
    text = path.read_text("utf-8")
    fixed = fix_counts(text, knobs, len(sections))
    if fixed != text:
        atomic_write(path, fixed)
        print(f"{path}: knob counts in prose corrected")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
