"""Import an existing project's history so ddflow can continue it, not restart it.

A project that adopts ddflow on day 400 has four hundred days of work already: a todo
file with things done and things not, a lessons corpus, architecture decisions, bugs
fixed, branches half-landed. `ddflow adopt` used to start the queue **empty**, which
is the worst possible answer — the tool then reports "nothing in flight" to a project
with six things in flight, and an agent believes it.

Two halves, and the split is the whole design:

**This module does what it can VERIFY.** A checkbox is a fact: `- [x]` means someone
ticked it. A `## ` heading in a lessons file is a lesson. A file under `docs/adr/` is a
decision. A branch with unmerged commits is work in flight. Those are parsed, attributed
to their source line, and offered.

**The agent does what needs JUDGEMENT**, guided by the `import-existing-project`
workflow prompt: which headings are phases and which are tasks, what depends on what,
which globs each touches, whether a five-year-old story still applies. That half is not
automatable and pretending otherwise produces a confident, wrong queue — and a wrong
queue is worse than no queue, because the scheduler will hand it out.

Three rules hold this together:

1. **Dry-run by default.** `plan_import` reads and reports; `apply_import` writes, and
   only when asked. Nothing is imported as a side effect of looking.
2. **Every item carries its source.** `docs/todo.md:41` goes in the body, so the
   operator can check the import and the next reader can find the original.
3. **Idempotent.** Ids are derived from the source, so re-running after editing the
   todo adds what is new and leaves the rest alone. An import you cannot re-run is an
   import you have to get right first time.
"""

from ddflow.core import _reexport

from ...core.globs import match as glob_match
from ...core.slug import slug as _slug  # noqa: F401
from ...core.slug import slug_words as _slug_words  # noqa: F401

# `services.importer` was one module split into submodules. Re-export its flat namespace
# as attributes of this package so every existing `from ddflow.services.importer import X`
# keeps resolving. The re-export is LAZY (ddflow/core/_reexport.py): the package imports
# no submodule until a name is asked for, which keeps the invariants a one-file module
# had — monkeypatch re-binds the name on THIS module, and the layering ratchet would read
# an eager `from .sub import ...` here as a latent parent<->child cycle. The three
# `glob_match`/`_slug`/`_slug_words` aliases above were package-level re-exports of the
# old flat module, so they are re-exported eagerly rather than via a submodule.
#: Public (and patch-covered private) name -> the sibling submodule that defines it.
_SUBMODULE_OF = {
    # _titles
    "DECISION_GLOBS": "_titles",
    "JOURNAL_GLOBS": "_titles",
    "LESSON_GLOBS": "_titles",
    "LESSON_SUMMARY_GLOBS": "_titles",
    "OPTMEM_GLOBS": "_titles",
    "RESEARCH_GLOBS": "_titles",
    "SOURCE_GLOBS": "_titles",
    "TODO_GLOBS": "_titles",
    "_ANNOTATION_CHARS": "_titles",
    "_ASIDE_CLAUSE": "_titles",
    "_ASIDE_NOT_DISPOSITIONS": "_titles",
    "_BACKTICKED": "_titles",
    "_BODY_CHARS": "_titles",
    "_BOLD": "_titles",
    "_CHECK": "_titles",
    "_CLAUSE_END": "_titles",
    "_CLOSED_MARKERS": "_titles",
    "_DECISION_INDEX_STEMS": "_titles",
    "_DONE_CAPS": "_titles",
    "_DONE_CAPS_ENDING": "_titles",
    "_DONE_CHECK": "_titles",
    "_DONE_CLAUSE": "_titles",
    "_DONE_WORD": "_titles",
    "_FUTURE_WORK": "_titles",
    "_GLOBS": "_titles",
    "_HEADING": "_titles",
    "_HOLD_MARKERS": "_titles",
    "_ID": "_titles",
    "_ID_TOKEN": "_titles",
    "_ITEM_ID": "_titles",
    "_LEAD_WORD_CHARS": "_titles",
    "_LINE_SUFFIX": "_titles",
    "_NEEDS": "_titles",
    "_NEGATED_BEFORE": "_titles",
    "_NEGATION": "_titles",
    "_NONBOLD_SPLIT": "_titles",
    "_NOT_AFTER_OF": "_titles",
    "_OLD_PHASE_SLUG": "_titles",
    "_OLD_TASK_SLUG": "_titles",
    "_OPEN_ASIDE": "_titles",
    "_OPTMEM": "_titles",
    "_PATH_SUFFIX": "_titles",
    "_PROSE_DEP": "_titles",
    "_PROSE_NEGATED": "_titles",
    "_PROSE_NEGATING": "_titles",
    "_RESOURCES": "_titles",
    "_SECTION_CLOSED": "_titles",
    "_SECTION_HOLD": "_titles",
    "_SECTION_LIVE": "_titles",
    "_SENTENCE_END": "_titles",
    "_STATUS_CLOSED": "_titles",
    "_STATUS_HOLD": "_titles",
    "_STATUS_LINE": "_titles",
    "_STATUS_LIVE": "_titles",
    "_TITLE_ASIDE": "_titles",
    "_TITLE_CHARS": "_titles",
    "_VERDICT_FILLER": "_titles",
    "_WORD_END": "_titles",
    "_clean_title": "_titles",
    "_continuation": "_titles",
    "_cut_title": "_titles",
    "_is_id": "_titles",
    "_leads_with": "_titles",
    "_marker_in": "_titles",
    "_prose_globs": "_titles",
    "_prose_needs": "_titles",
    "_split_id": "_titles",
    "_split_nonbold": "_titles",
    "_title_of": "_titles",
    "_titled": "_titles",
    "_verdict_asides": "_titles",
    # _tables
    "KINDS": "_tables",
    "LEGACY_MEMORY_SESSION": "_tables",
    "NOTE_SESSIONS": "_tables",
    "_ANY_HEADING": "_tables",
    "_BY_HEADING": "_tables",
    "_FENCE": "_tables",
    "_GLOB_DECOR": "_tables",
    "_HEADING_LEAD": "_tables",
    "_ID_DECOR": "_tables",
    "_NEEDS_SPLIT": "_tables",
    "_NEGATED": "_tables",
    "_SECTION": "_tables",
    "_TABLE_FIELDS": "_tables",
    "_TABLE_HEAD": "_tables",
    "Duplicate": "_tables",
    "Found": "_tables",
    "ImportPlan": "_tables",
    "_col": "_tables",
    "_disposition": "_tables",
    "_fenced": "_tables",
    "_files": "_tables",
    "_heading_disposition": "_tables",
    "_heading_title": "_tables",
    "_parse_globs": "_tables",
    "_parse_needs": "_tables",
    "_push_heading": "_tables",
    "_scan_task_tables": "_tables",
    "_sections": "_tables",
    "_status_disposition": "_tables",
    "_table_cells": "_tables",
    "_table_columns": "_tables",
    "_table_delim": "_tables",
    "_table_task": "_tables",
    "_unique": "_tables",
    # _todo
    "_COMPRESSED": "_todo",
    "_DATE": "_todo",
    "_HEADING_NUMBER": "_todo",
    "_INDEX_TITLES": "_todo",
    "_LESSON_BODY_CHARS": "_todo",
    "_LESSON_HEAD": "_todo",
    "_LESSON_ID": "_todo",
    "_PREFIX_QUORUM": "_todo",
    "_SEEN_IN": "_todo",
    "_add_prose_needs": "_todo",
    "_adopt_child_prefix": "_todo",
    "_agrees": "_todo",
    "_box_disposition": "_todo",
    "_common_dotted_prefix": "_todo",
    "_date_hint": "_todo",
    "_heading_number": "_todo",
    "_id_entries": "_todo",
    "_ids_config": "_todo",
    "_is_index_section": "_todo",
    "_lesson_level": "_todo",
    "_one_paragraph": "_todo",
    "_scan_sections": "_todo",
    "_set_legacy_ids": "_todo",
    "scan_todos": "_todo",
    # _scan
    "_BULLET": "_scan",
    "_CITES": "_scan",
    "_GENERATED_MARK": "_scan",
    "_LEAD": "_scan",
    "_NOTE_EXAMPLES": "_scan",
    "_RESEARCH_HEAD": "_scan",
    "_TRAILING_CITE": "_scan",
    "_attach_summaries": "_scan",
    "_flag_shipped_phases_with_open_tasks": "_scan",
    "_flag_unresolvable_needs": "_scan",
    "_id_key": "_scan",
    "_known_ids": "_scan",
    "_norm": "_scan",
    "_pull_in_needed": "_scan",
    "_research_ident": "_scan",
    "_research_legacy": "_scan",
    "_summary_bullet": "_scan",
    "_summary_text": "_scan",
    "_verdict": "_scan",
    "scan_branches": "_scan",
    "scan_decisions": "_scan",
    "scan_journal": "_scan",
    "scan_lesson_summaries": "_scan",
    "scan_lessons": "_scan",
    "scan_optmem": "_scan",
    "scan_research": "_scan",
    # _phases
    "_HEADING_LIVE": "_phases",
    "_HEADING_STALLED": "_phases",
    "_IMPORT_ADDS": "_phases",
    "_IMPORT_STATES": "_phases",
    "_NEGATED_DONE": "_phases",
    "_STATE_EVENTS": "_phases",
    "Scanner": "_phases",
    "Touched": "_phases",
    "_Settled": "_phases",
    "_by_import": "_phases",
    "_claims_done": "_phases",
    "_is_imported_phase": "_phases",
    "_keep_imported_ids": "_phases",
    "_note_settled": "_phases",
    "_note_withheld": "_phases",
    "_phase_verdict": "_phases",
    "_says_unfinished": "_phases",
    "_scan_sources": "_phases",
    "_scanners": "_phases",
    "_settle_existing": "_phases",
    "_settle_needed_phases": "_phases",
    "_settle_phases": "_phases",
    "_triage_found": "_phases",
    "all_source_globs": "_phases",
    "sources_from": "_phases",
    "touched_since_import": "_phases",
    # _plan
    "_MARKUP_AROUND_PATH": "_plan",
    "_MARKUP_INSIDE": "_plan",
    "_MAX_SOURCE_PATH": "_plan",
    "_MD_LINK": "_plan",
    "VerifyReport": "_plan",
    "_cap_or_settle": "_plan",
    "_has_open_child": "_plan",
    "_memory_sources": "_plan",
    "_note_sources": "_plan",
    "_path_shaped": "_plan",
    "_scan_queue": "_plan",
    "plan_import": "_plan",
    "verify_import": "_plan",
    # _apply
    "_REFUSED_KEY": "_apply",
    "_SCREENED": "_apply",
    "_SHOWN_IDENTICAL": "_apply",
    "_add_imported_tasks": "_apply",
    "_apply_state": "_apply",
    "_count_long_memories": "_apply",
    "_dedupe_found": "_apply",
    "_imported_research": "_apply",
    "_leave_out_long_memories": "_apply",
    "_memories_within": "_apply",
    "_resolve_chains": "_apply",
    "apply_import": "_apply",
    "task_body": "_apply",
}

_reexport.reexport(globals(), _SUBMODULE_OF, __name__)
del _reexport

__all__ = [
    "DECISION_GLOBS",
    "JOURNAL_GLOBS",
    "KINDS",
    "LEGACY_MEMORY_SESSION",
    "LESSON_GLOBS",
    "LESSON_SUMMARY_GLOBS",
    "NOTE_SESSIONS",
    "OPTMEM_GLOBS",
    "RESEARCH_GLOBS",
    "SOURCE_GLOBS",
    "TODO_GLOBS",
    "Duplicate",
    "Found",
    "ImportPlan",
    "Scanner",
    "Touched",
    "VerifyReport",
    "all_source_globs",
    "apply_import",
    "glob_match",
    "plan_import",
    "sources_from",
    "task_body",
    "touched_since_import",
    "verify_import",
]
# The ``scan_*`` names re-exported from `_scan` (its ``__all__`` is their one list) sit
# alphabetically between ``plan_import`` and ``scan_todos``; extend them here from the
# ``_SUBMODULE_OF`` mapping rather than re-listing them beside `_scan`'s own list.
_scan_names = [
    name for name in _SUBMODULE_OF if _SUBMODULE_OF[name] == "_scan" and not name.startswith("_")
]
__all__[__all__.index("sources_from") : __all__.index("sources_from")] = [
    *_scan_names,
    "scan_todos",
]
