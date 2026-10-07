"""The `[importer]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob

#: Each source family a `*_globs` knob points `ddflow import` at, and what it reads there.
_FAMILIES = {
    "todo": "todo checklists (phases and tasks)",
    "lesson": "the lessons corpus",
    "lesson_summary": "a hand-written lessons summary (a GENERATED one is always skipped)",
    "decision": "architectural decision records, one file each",
    "research": "the research log",
    "journal": "the engineering journal",
    "memory": "an OptMem-style `#N date text` memory store",
}


def _globs_doc(family: str) -> str:
    return (
        f"Paths `ddflow import` reads {_FAMILIES[family]} from. Empty uses the built-in locations; "
        f"a list REPLACES them rather than adding to them, because the same filename can "
        f"mean opposite things in two projects -- one repository's docs/LOG.md is its whole "
        f"journal, another's is a generated index of it."
    )


@declare("importer")
@dataclass
class ImportConfig:
    """Adopting ddflow on a project that already has history: `[importer]`.

    Named for the module rather than the command because `import` is a keyword and a
    section called `[import_]` would be a TOML wart the operator has to remember.
    """

    max_tasks: int = knob(
        200,
        doc="Refuse to propose more tasks than this in one import. An import writes events into a log that is committed to git, and five thousand of them is not recoverable by anything short of editing history; one real repository yielded 4,799. Raise it deliberately once you have looked at what it would write.",
    )
    preview_rows: int = knob(
        8,
        doc="How many items of each kind the import proposal prints before summarising the rest. The whole list is always in the --json output; this only caps the human-readable preview.",
    )
    #: Where each source family is read from. Empty = the built-in locations; set = those
    #: paths INSTEAD (see `importer.sources_from`).
    todo_globs: list[str] = knob(factory=list, doc=_globs_doc("todo"))
    lesson_globs: list[str] = knob(factory=list, doc=_globs_doc("lesson"))
    lesson_summary_globs: list[str] = knob(factory=list, doc=_globs_doc("lesson_summary"))
    decision_globs: list[str] = knob(factory=list, doc=_globs_doc("decision"))
    research_globs: list[str] = knob(factory=list, doc=_globs_doc("research"))
    journal_globs: list[str] = knob(factory=list, doc=_globs_doc("journal"))
    memory_globs: list[str] = knob(factory=list, doc=_globs_doc("memory"))
    archive_globs: list[str] = knob(
        factory=list,
        doc="Todo files that are an ARCHIVE: their open boxes are history until someone names the section. They import as BLOCKED, one phase per section, and `ddflow unblock <phase>` releases a whole section at once. For a long plan file most of whose unticked boxes are notes and filed findings inside sections that shipped long ago -- no classifier over that prose is trustworthy, because whether a box is work is a property of what the operator intends. Matched against repo-relative paths; the files must also be read by `todo_globs` (or its defaults).",
    )
