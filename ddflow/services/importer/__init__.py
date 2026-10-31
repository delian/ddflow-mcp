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

from ...core.slug import slug as _slug  # noqa: F401
from ...core.slug import slug_words as _slug_words  # noqa: F401
from . import _apply, _phases, _plan, _scan, _tables, _titles, _todo

# `services.importer` was one module split into submodules. Re-export its flat namespace
# as attributes of this package so every existing `from ddflow.services.importer import X`
# keeps resolving, and publish that namespace into each submodule so a function in one
# submodule can call a helper defined in another by bare name (resolved from the defining
# module's globals at call time) without top-level circular imports.
_EXPORTS = {
    _name: getattr(_mod, _name)
    for _mod in (_apply, _phases, _plan, _scan, _tables, _titles, _todo)
    for _name in _mod.__all__
}
globals().update(_EXPORTS)
for _mod in (_apply, _phases, _plan, _scan, _tables, _titles, _todo):
    _mod.__dict__.update(_EXPORTS)
del _EXPORTS

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
    "plan_import",
    "scan_branches",
    "scan_decisions",
    "scan_journal",
    "scan_lesson_summaries",
    "scan_lessons",
    "scan_optmem",
    "scan_research",
    "scan_todos",
    "sources_from",
    "task_body",
    "touched_since_import",
    "verify_import",
]
