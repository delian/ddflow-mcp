"""A generated region of README.md: the marker grammar of D-doc-regions, once.

`<!-- ddflow:begin <name> sha=<12 hex> -->` ... `<!-- ddflow:end <name> -->`, `sha` being the
digest of the body between them as rendered. `knob_table` (README/knobs) and `readme_toc`
(README/toc) each own one region; what they share -- finding it, replacing it, noticing a hand
edit, rewriting the file -- lives here.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from pathlib import Path

from ..core.digest import content_digest
from ..infra.fsio import atomic_write


class Region:
    """One named region of the README, rendered by the caller's `render(readme_text)`."""

    def __init__(self, name: str, label: str, render: Callable[[str], str]) -> None:
        self.name = name
        self.label = label  # what the region is, for messages: "knob table"
        self.render = render
        self.end = f"<!-- ddflow:end {name} -->"
        self._begin = re.compile(
            rf"^<!-- ddflow:begin {re.escape(name)} sha=(?P<sha>[0-9a-f]{{12}}) -->(?=\r?$)", re.M
        )
        self._end = re.compile(rf"^{re.escape(self.end)}(?=\r?$)", re.M)

    def block(self, body: str) -> str:
        """The region, markers included, around ``body``."""
        sha = content_digest(body, length=12)
        return f"<!-- ddflow:begin {self.name} sha={sha} -->\n{body}\n{self.end}"

    def match(self, readme: str) -> tuple[re.Match[str], re.Match[str]]:
        """The begin and end marker matches, or ValueError."""
        begin = self._begin.search(readme)
        end = self._end.search(readme, begin.end()) if begin else None
        if begin is None or end is None:
            raise ValueError(f"README has no {self.name} region (ddflow:begin ... {self.end})")
        return begin, end

    def replace(self, readme: str, block: str) -> str:
        """`readme` with its region (markers included) replaced by `block`."""
        begin, end = self.match(readme)
        return readme[: begin.start()] + block + readme[end.end() :]

    def hand_edited(self, readme: str) -> bool:
        """Was the body changed since it was rendered: does it no longer hash to the `sha`
        its begin marker recorded (D-doc-regions)?"""
        begin, end = self.match(readme)
        body = readme[begin.end() : end.start()].replace("\r\n", "\n")
        body = body.removeprefix("\n").removesuffix("\n")
        return content_digest(body, length=12) != begin["sha"]

    def main(self, argv: list[str]) -> int:
        """Rewrite the region of the README named in `argv` (default README.md).

        A region edited by hand is refused (exit 3) unless `--force`: rewriting it would drop
        the edit. This is the interim policy until D-doc-regions' 3-way refresh arrives with
        its region engine (B-tpl-regions, B-tpl-merge)."""
        force = "--force" in argv
        paths = [a for a in argv if a != "--force"]
        path = Path(paths[0] if paths else "README.md")
        text = path.read_text("utf-8")
        new = self.replace(text, self.render(text))
        if new == text:
            print(f"{path}: {self.label} already current")
            return 0
        if self.hand_edited(text) and not force:
            print(
                f"{path}: the {self.name} region was edited by hand (its body no longer matches "
                "its sha). Move the edit outside the region, then rerun; --force rewrites the "
                "region and discards the edit",
                file=sys.stderr,
            )
            return 3
        atomic_write(path, new)
        print(f"{path}: {self.label} rewritten")
        return 0
