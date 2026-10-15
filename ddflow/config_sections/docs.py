"""The `[docs]` section: what the documentation check covers, extracts, counts and runs.

Re-exported from `ddflow.config`, which assembles `Config` from every section. The knobs
`ddflow.services.docscheck` reads live here, and so do the ones `ddflow docs check` reads
(severity, links, the document scope, the lint line) -- a knob is declared in the task that
first reads it (D-docs-section-with-its-reader).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ._docs import declare, int_at_least, knob
from .enforce import BLOCK_WARN_OFF

#: What `check_links` follows: nothing, targets inside the tree, or the network too.
DOCS_LINK_MODES = ("off", "local", "network")

#: What a `[[docs.extract]]` entry extracts: command-line flags, tool names, config knobs.
EXTRACT_KINDS = ("flags", "tools", "knobs")
_EXTRACT_FIELDS = ("kind", "pattern", "source_globs")
_COUNT_FIELDS = ("pattern", "command", "docs")


def _regex_problem(v: Any) -> str:
    if not isinstance(v, str) or not v:
        return "pattern must be a non-empty string"
    try:
        rx = re.compile(v)
    except re.error as exc:
        return f"pattern {v!r} is not a regular expression: {exc}"
    if rx.groups < 1:
        return f"pattern {v!r} needs a capture group: the name (or count) it finds"
    return ""


def _globs_problem(v: Any, what: str) -> str:
    ok = isinstance(v, list) and v and all(isinstance(g, str) and g for g in v)
    return "" if ok else f"{what} must be a non-empty list of glob strings"


def _entry_problem(e: Any, fields: tuple[str, ...], need_globs: str) -> str:
    if not isinstance(e, dict):
        return "each entry must be a table"
    if bad := sorted(set(e) - set(fields)):
        return f"unknown field {bad[0]!r}"
    if (p := _regex_problem(e.get("pattern"))) or (
        need_globs in e and (p := _globs_problem(e[need_globs], need_globs))
    ):
        return p
    return ""


def _extract_entry_problem(e: Any) -> str:
    if p := _entry_problem(e, _EXTRACT_FIELDS, "source_globs"):
        return p
    if e.get("kind") not in EXTRACT_KINDS:
        return f"kind {e.get('kind')!r} must be one of {', '.join(EXTRACT_KINDS)}"
    return "" if "source_globs" in e else "source_globs is required"


def _count_entry_problem(e: Any) -> str:
    if p := _entry_problem(e, _COUNT_FIELDS, "docs"):
        return p
    cmd = e.get("command")
    return "" if isinstance(cmd, str) and cmd.strip() else "command must be a non-empty string"


def _problem(v: Any, entry) -> str:
    if not isinstance(v, list):
        return "must be a list of tables, e.g. [[docs.extract]] blocks"
    for i, e in enumerate(v):
        if p := entry(e):
            return f"entry {i + 1}: {p}"
    return ""


def _members(v: Any, fields: tuple[str, ...], entry) -> tuple[Any, list[str]]:
    """A config FILE's entries as this ddflow reads them (D-compat 2): a field a newer release
    added is dropped from its entry, and an entry this version cannot use is skipped, each
    with a note; the rest apply."""
    if not isinstance(v, list):
        return v, []
    keep, notes = [], []
    for i, raw in enumerate(v):
        e = raw
        if isinstance(raw, dict):
            for extra in sorted(set(raw) - set(fields)):
                notes.append(
                    f"ignored field {extra!r} of docs entry {i + 1} (newer than this code)"
                )
            e = {k: x for k, x in raw.items() if k in fields}
        if p := entry(e):
            notes.append(f"ignored docs entry {i + 1}: {p}")
        else:
            keep.append(e)
    return keep, notes


def _extract_problem(v: Any) -> str:
    return _problem(v, _extract_entry_problem)


def _count_problem(v: Any) -> str:
    return _problem(v, _count_entry_problem)


def _extract_members(v: Any) -> tuple[Any, list[str]]:
    return _members(v, _EXTRACT_FIELDS, _extract_entry_problem)


def _count_members(v: Any) -> tuple[Any, list[str]]:
    return _members(v, _COUNT_FIELDS, _count_entry_problem)


@declare("docs")
@dataclass
class DocsConfig:
    """The documentation check: what public surface the code has, and which numbers the docs state."""

    severity: str = knob(
        "warn",
        doc="What a finding from `ddflow docs check` costs. 'warn' (default) reports it and leaves the docs gate skippable with a reason. 'block' keeps the docs gate from being recorded passed while a finding stands. 'off' runs nothing and the command says so (exit 2). A finding is printed at every severity but off: this says what it costs, not whether it is seen. A check that could not run is `unavailable` at every severity, never a pass.",
        choices=BLOCK_WARN_OFF,
        strictest=("warn", "reports without blocking, so a typo cannot start refusing merges"),
    )
    check_links: str = knob(
        "local",
        doc="Which links `ddflow docs check` follows. 'off': none (no link or anchor finding). 'local' (default): relative targets and `#anchors`, resolved against the tree. 'network': also http(s) links, each fetched once with a read-only GET (opt-in; CI flakiness is why it is not the default). A remote link that cannot be reached because the network is down makes the check `unavailable`, never a pass; one the server answers 404 or 410 is a `link` finding.",
        choices=DOCS_LINK_MODES,
        strictest=("local", "follows only links inside the tree, so a typo never reaches out"),
    )
    link_timeout_s: int = knob(
        10,
        doc="Seconds one remote link may take to answer when `check_links` is 'network' before it counts as unreachable (the check is then `unavailable`, not failed).",
        check=int_at_least(1),
    )
    check_paths: list[str] = knob(
        factory=list,
        doc="The documents `ddflow docs check` reads, as repo-relative globs: an explicit allowlist (README plus the docs directories a project keeps true), because a whole-tree check on a project with history drowns in findings about pages that were accurate when written. Empty (default): the files matching `[enforce].doc_globs` minus `[enforce].doc_exclude` and the generated export targets, so what counts as documentation is written once.",
    )
    lint_command: str = knob(
        "",
        doc='A markdown lint or formatter to run as part of the check, as a shell line in the repository root (e.g. `npx markdownlint-cli2 "**/*.md"`). Exit 0 passes; any other exit is a `lint` finding carrying its output. Empty (default): none, and the report lists the lint as `unavailable` rather than passing it. A line that cannot run (command not found, timed out) is `unavailable` too and keeps a clean report from reading as clean. ddflow ships no lint and adds no dependency: the project brings the command. Bounded by `command_timeout_s`.',
    )
    ignore_paths: list[str] = knob(
        factory=list,
        doc="Globs of path-like references in the documents that are not worth even a note: runtime paths, generated files, illustrative examples. Empty (default). Path-like inline code is only 10-30% precise, so a note carries this list instead of failing anyone.",
    )
    ignore_names: list[str] = knob(
        factory=list,
        doc="fnmatch patterns of identifiers and flags ANOTHER tool owns, which this project's docs may name without the code having them (`ddflow_*` in the agent-instructions page of a project that merely uses ddflow). Empty (default); a name matched here is never a finding.",
    )
    extract: list[dict[str, Any]] = knob(
        factory=list,
        doc='Extractors that list the public surface the code offers, so the check can say which of it no document mentions: `[[docs.extract]]` blocks with `kind` (flags, tools or knobs), `pattern` (a regular expression whose first group, or the group named `name`, is the surface name) and `source_globs` (the tracked files to read, in git glob syntax). A flag named in no checked document is reported as a `flag` finding, a tool as a `command` and a knob as an `identifier`, each pointing at the source line. Example: `[[docs.extract]]` with `kind = "flags"`, `pattern = "add_argument[(]\'(--[a-z-]+)\'"` and `source_globs = ["src/**/cli.py"]`. Empty by default: nothing is extracted.',
        check=_extract_problem,
        members=_extract_members,
    )
    count: list[dict[str, Any]] = knob(
        factory=list,
        doc="Numbers the documents state that a command can recompute: `[[docs.count]]` blocks with `pattern` (a regular expression whose first group is the stated number), `command` (what prints the true number: one integer, or lines to count) and optionally `docs` (globs of the documents to read; default every checked document). A stated number that differs from the command's is a `count` finding. A document can also keep a count current in place with `<!-- ddflow:count cmd=\"ddflow list --json\">42<!-- /ddflow:count -->`; a marker's command is run only when its first word is one of `cli_command` or the first word of a `[[docs.count]]` command, and never through a shell. Empty by default.",
        check=_count_problem,
        members=_count_members,
    )
    cli_command: list[str] = knob(
        factory=list,
        doc="The commands whose `<command> <verb> --help` is walked to check that `<command> <verb> --flag` in a document names a flag that verb has (a flag the project spells somewhere else does not pass). Empty (default) uses the project's own scripts: `[project.scripts]` in pyproject.toml and `bin` in package.json. A command that cannot run (missing, timing out) is a note, never a pass.",
    )
    command_timeout_s: int = knob(
        20,
        doc="Seconds one `--help` walk step, one count command or the `lint_command` may run before the check records it as not run (a note) instead of waiting.",
        check=int_at_least(1),
    )
