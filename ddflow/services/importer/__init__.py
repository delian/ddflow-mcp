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
which globs each touches, whether a five-year-old lesson still applies. That half is not
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

from ._titles import *
from ._tables import *
from ._todo import *
from ._scan import *
from ._phases import *
from ._plan import *
from ._apply import *
from . import _titles, _tables, _todo, _scan, _phases, _plan, _apply

# `services.importer` was one module split into submodules; a function in one submodule
# calls a helper defined in another by bare name, resolved from the defining module's
# globals at call time. Publish the merged namespace into each submodule so those
# cross-references keep resolving without top-level circular imports.
for _m in (_titles, _tables, _todo, _scan, _phases, _plan, _apply):
    _m.__dict__.update({k: v for k, v in globals().items() if not k.startswith('__')})
del _m

__all__ = ["TODO_GLOBS", "LESSON_GLOBS", "LESSON_SUMMARY_GLOBS", "DECISION_GLOBS", "RESEARCH_GLOBS", "OPTMEM_GLOBS", "JOURNAL_GLOBS", "SOURCE_GLOBS", "NOTE_SESSIONS", "LEGACY_MEMORY_SESSION", "KINDS", "Found", "Duplicate", "ImportPlan", "scan_todos", "scan_lessons", "scan_lesson_summaries", "scan_decisions", "scan_research", "scan_journal", "scan_optmem", "scan_branches", "Touched", "touched_since_import", "Scanner", "sources_from", "all_source_globs", "plan_import", "VerifyReport", "verify_import", "task_body", "apply_import"]
