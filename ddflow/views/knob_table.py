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
from ..core.digest import content_digest
from ..infra.fsio import atomic_write
from ..infra.tomlcfg import value

REGION = "README/knobs"
END = f"<!-- ddflow:end {REGION} -->"
_BEGIN = re.compile(
    rf"^<!-- ddflow:begin {re.escape(REGION)} sha=(?P<sha>[0-9a-f]{{12}}) -->(?=\r?$)", re.M
)
_END = re.compile(rf"^{re.escape(END)}(?=\r?$)", re.M)
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
    return f"<!-- ddflow:begin {REGION} sha={content_digest(body, length=12)} -->\n{body}\n{END}"


def _region(readme: str) -> tuple[re.Match[str], re.Match[str]]:
    """The begin and end marker matches of the `README/knobs` region, or ValueError."""
    begin = _BEGIN.search(readme)
    end = _END.search(readme, begin.end()) if begin else None
    if begin is None or end is None:
        raise ValueError(f"README has no {REGION} region (ddflow:begin ... {END})")
    return begin, end


def replace(readme: str, block: str) -> str:
    """`readme` with its `README/knobs` region (markers included) replaced by `block`."""
    begin, end = _region(readme)
    return readme[: begin.start()] + block + readme[end.end() :]


def hand_edited(readme: str) -> bool:
    """Was the region's body changed since it was rendered: does it no longer hash to the
    `sha` its begin marker recorded (D-doc-regions)?"""
    begin, end = _region(readme)
    body = readme[begin.end() : end.start()].replace("\r\n", "\n")
    body = body.removeprefix("\n").removesuffix("\n")
    return content_digest(body, length=12) != begin["sha"]


def main(argv: list[str]) -> int:
    """Rewrite the generated region of the README named in `argv` (default README.md).

    A region edited by hand is refused (exit 3) unless `--force`: rewriting it would drop
    the edit, and the table is the declarations' to change, not the README's. This is the
    interim policy: D-doc-regions' refresh -- a 3-way merge of the edit, stopping only on a
    conflict, with --keep-mine -- arrives with its region engine (B-tpl-regions,
    B-tpl-merge), which this region then moves onto."""
    force = "--force" in argv
    paths = [a for a in argv if a != "--force"]
    path = Path(paths[0] if paths else "README.md")
    text = path.read_text("utf-8")
    new = replace(text, render())
    if new == text:
        print(f"{path}: knob table already current")
        return 0
    if hand_edited(text) and not force:
        print(
            f"{path}: the {REGION} region was edited by hand (its body no longer matches its "
            "sha). Move the edit outside the region (or into the knob declarations), then "
            "rerun; --force rewrites the region and discards the edit",
            file=sys.stderr,
        )
        return 3
    atomic_write(path, new)
    print(f"{path}: knob table rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
