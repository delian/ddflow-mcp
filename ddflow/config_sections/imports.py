"""The `[importer]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass, field

from ._docs import _doc


@dataclass
class ImportConfig:
    """Adopting ddflow on a project that already has history: `[importer]`.

    Named for the module rather than the command because `import` is a keyword and a
    section called `[import_]` would be a TOML wart the operator has to remember.
    """

    max_tasks: int = 200
    preview_rows: int = 8
    #: Where each source family is read from. Empty = the built-in locations; set = those
    #: paths INSTEAD (see `importer.sources_from`).
    todo_globs: list[str] = field(default_factory=list)
    lesson_globs: list[str] = field(default_factory=list)
    lesson_summary_globs: list[str] = field(default_factory=list)
    decision_globs: list[str] = field(default_factory=list)
    research_globs: list[str] = field(default_factory=list)
    journal_globs: list[str] = field(default_factory=list)
    memory_globs: list[str] = field(default_factory=list)
    archive_globs: list[str] = field(default_factory=list)


_doc(
    "importer",
    "max_tasks",
    "Refuse to propose more tasks than this in one import. An import writes events into a log that is committed to git, and five thousand of them is not recoverable by anything short of editing history; one real repository yielded 4,799. Raise it deliberately once you have looked at what it would write.",
)
for _family, _what in (
    ("todo", "todo checklists (phases and tasks)"),
    ("lesson", "the lessons corpus"),
    ("lesson_summary", "a hand-written lessons summary (a GENERATED one is always skipped)"),
    ("decision", "architectural decision records, one file each"),
    ("research", "the research log"),
    ("journal", "the engineering journal"),
    ("memory", "an OptMem-style `#N date text` memory store"),
):
    _doc(
        "importer",
        f"{_family}_globs",
        f"Paths `ddflow import` reads {_what} from. Empty uses the built-in locations; "
        f"a list REPLACES them rather than adding to them, because the same filename can "
        f"mean opposite things in two projects -- one repository's docs/LOG.md is its whole "
        f"journal, another's is a generated index of it.",
    )

_doc(
    "importer",
    "archive_globs",
    "Todo files that are an ARCHIVE: their open boxes are history until someone names the section. They import as BLOCKED, one phase per section, and `ddflow unblock <phase>` releases a whole section at once. For a long plan file most of whose unticked boxes are notes and filed findings inside sections that shipped long ago -- no classifier over that prose is trustworthy, because whether a box is work is a property of what the operator intends. Matched against repo-relative paths; the files must also be read by `todo_globs` (or its defaults).",
)

_doc(
    "importer",
    "preview_rows",
    "How many items of each kind the import proposal prints before summarising the rest. The whole list is always in the --json output; this only caps the human-readable preview.",
)
