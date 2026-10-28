"""Counts for the documentation check: numbers a document states that a command recomputes
(decision D-docs-stage: a generic count regex is noise, so counts are opt-in).

Two opt-ins. A **marker** keeps one count current in place:
``<!-- ddflow:count cmd="ddflow list --json">42<!-- /ddflow:count -->`` (the opener
may close with ``-->`` too); the command runs
without a shell, and only when its first word is a configured tool (``[docs].cli_command`` or
a ``[[docs.count]]`` command) -- a document must not run arbitrary programs. A
``[[docs.count]]`` entry names a ``pattern`` (group 1 is the stated number) and the
``command`` that prints the true one. A command prints one integer, or lines that are
counted (a command that prints nothing is not a count of 0: print the 0). A count that could not be recomputed is a note, never a pass; marked text inside
code spans and fences is an example, not a claim.
"""

from __future__ import annotations

import re
import shlex
from collections import Counter
from collections.abc import Mapping, Sequence

from .docscheck_help import Runner
from .docscheck_md import prose_only
from .docscheck_types import Finding, Report
from .docsync import glob_regex

_MARKER = re.compile(
    r"<!--\s*ddflow:count\s+cmd=\"([^\"]*)\"\s*(?:--)?>(.*?)<!--\s*/ddflow:count\s*-->", re.S
)
_NUMBER = re.compile(r"\d[\d,]*")


def _stated(text: str) -> int | None:
    m = _NUMBER.fullmatch(text.strip())
    return int(m.group(0).replace(",", "")) if m else None


class _Recount:
    """Runs each command once; remembers why one could not be run."""

    def __init__(self, run: Runner | None, tools: set[str]) -> None:
        self.run, self.tools = run, tools
        self._seen: dict[str, tuple[int | None, str]] = {}

    def value(self, cmd: str) -> tuple[int | None, str]:
        if cmd not in self._seen:
            self._seen[cmd] = self._compute(cmd)
        return self._seen[cmd]

    def _compute(self, cmd: str) -> tuple[int | None, str]:
        if self.run is None:
            return None, "no command runner: counts were not recomputed"
        try:
            argv = shlex.split(cmd)
        except ValueError as exc:
            return None, f"cannot parse the command: {exc}"
        if not argv or argv[0] not in self.tools:
            first = argv[0] if argv else "(empty)"
            return None, f"{first!r} is not a configured tool (docs.cli_command, docs.count)"
        done = self.run(argv)
        if not done.ran or done.returncode != 0:
            return None, f"`{cmd}` did not run to success: {done.error or done.returncode}"
        out = (done.stdout or "").strip()
        if not out:
            return None, f"`{cmd}` printed nothing: that is not a count of 0"
        n = _stated(out)
        return (n if n is not None else len([x for x in out.splitlines() if x.strip()])), ""


def check_counts(
    report: Report,
    texts: Mapping[str, str],
    entries: Sequence[Mapping],
    run: Runner | None,
    tools: Sequence[str],
    counts: Counter,
) -> None:
    """Add ``count`` findings (stated != recomputed) and notes (could not recompute)."""
    recount = _Recount(run, set(tools) | {_first(e) for e in entries})
    n_checked = 0
    for path in sorted(texts):
        prose = prose_only(texts[path])
        spans = []
        for m in _MARKER.finditer(prose):
            spans.append(m.span())
            stated = _stated(m.group(2))
            n_checked += _judge(report, recount, path, prose, m.start(2), m.group(1), stated)
        for e in entries:
            if "docs" in e and not any(glob_regex(g).fullmatch(path) for g in e["docs"]):
                continue
            for m in re.finditer(e["pattern"], prose, re.M):
                if any(a <= m.start(1) < b for a, b in spans):
                    continue
                stated = _stated(m.group(1))
                n_checked += _judge(
                    report, recount, path, prose, m.start(1), e["command"], stated, m.group(0)
                )
    counts["counts"] += n_checked


def _first(entry: Mapping) -> str:
    words = str(entry.get("command", "")).split()
    return words[0] if words else ""


def _judge(report, recount, path, prose, pos, cmd, stated, ref="") -> int:
    line = prose.count("\n", 0, pos) + 1
    actual, why = recount.value(cmd)
    ref = ref or f"{stated}"
    if actual is None:
        report.notes.append(Finding("count", path, line, ref, f"not checked: {why}"))
        return 0
    if stated is None or stated != actual:
        said = "no number" if stated is None else str(stated)
        detail = f"states {said}, `{cmd}` says {actual}"
        report.findings.append(Finding("count", path, line, ref, detail))
    return 1
