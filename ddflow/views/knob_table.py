"""The README's knob reference, generated from the knobs themselves (B-uni-knobs-readme).

Every knob is a field of one of `Config`'s section dataclasses; its default is the field's
default and an enum knob's values are `KNOB_CHOICES`. The table between `BEGIN` and `END`
in README.md is rendered from exactly that, with the count, so neither can drift from the
code. tests/test_readme_knob_table.py fails when the README's block is not what this
renders; rewrite it with

    uv run python -m ddflow.views.knob_table README.md
"""

from __future__ import annotations

import sys
from dataclasses import fields
from pathlib import Path

from ..config import KNOB_CHOICES, Config
from ..infra.fsio import atomic_write
from ..infra.tomlcfg import value

BEGIN = "<!-- ddflow:knobs:begin (generated from the knob declarations; do not edit) -->"
END = "<!-- ddflow:knobs:end -->"
#: A default longer than this is not shown in a cell: `config --explain` prints it whole.
SHOWN = 40


def _default(v: object) -> str:
    """A default as TOML spells it, in a table cell; a long one is pointed at instead."""
    text = value(v)
    if len(text) > SHOWN:
        return "(long: see `ddflow config --explain`)"
    return "`" + text.replace("|", "\\|") + "`"


def render(cfg: Config | None = None, choices: dict[str, tuple[str, ...]] | None = None) -> str:
    """The block: the markers, the count, then one row per knob in `Config`'s order --
    its key, its default and, for an enum knob, its values."""
    cfg = cfg or Config()
    choices = KNOB_CHOICES if choices is None else choices
    sections = list(cfg._sections())
    rows = []
    for sec in sections:
        for f in fields(getattr(cfg, sec)):
            key = f"{sec}.{f.name}"
            values = " \\| ".join(f"`{v}`" for v in choices.get(key, ()))
            rows.append(f"| `{key}` | {_default(getattr(getattr(cfg, sec), f.name))} | {values} |")
    return "\n".join(
        [
            BEGIN,
            f"<details><summary>All {len(rows)} knobs across {len(sections)} sections</summary>",
            "",
            "| Knob | Default | Values |",
            "|---|---|---|",
            *rows,
            "",
            "</details>",
            END,
        ]
    )


def replace(readme: str, block: str) -> str:
    """`readme` with its generated block (markers included) replaced by `block`."""
    head, begin, rest = readme.partition(BEGIN)
    _, end, tail = rest.partition(END)
    if not begin or not end:
        raise ValueError(f"README has no {BEGIN!r} ... {END!r} block")
    return head + block + tail


def main(argv: list[str]) -> int:
    """Rewrite the generated block of the README named by `argv[0]` (default README.md)."""
    path = Path(argv[0] if argv else "README.md")
    text = path.read_text("utf-8")
    new = replace(text, render())
    if new != text:
        atomic_write(path, new)
        print(f"{path}: knob table rewritten")
    else:
        print(f"{path}: knob table already current")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
