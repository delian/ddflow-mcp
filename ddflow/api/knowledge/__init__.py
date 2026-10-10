"""What the project remembers: lessons, research, bugs, sessions, and the history.

Three refusals here are the reason this family is policy rather than plumbing, and each
exists because the record is worthless without it:

* **A research verdict must be CONFIRMED, REFUTED or THEORETICAL**, and the first two
  need a probe. A verdict with no probe behind it is an opinion, and a note with no
  verdict is a literature summary.
* **A bug may not be closed without naming the regression test** that would catch it
  again — write the test, watch it FAIL against the unfixed code, then close.
* **Recall is a prompt to CHECK, not a verdict.** That sentence ships in the output
  because the failure mode is an agent treating a three-week-old prompt as binding.
"""

# The operations live one module per area in this package (B-uni-splits); the public
# functions and classes are re-exported, so
# `from ddflow.api import knowledge as K; K.bug_found(...)` keeps working. A REBINDING is
# not shared: each function reads names from its own module, so to replace a helper or a
# constant in a test, patch the module that defines it (`knowledge.bugs.BUG_REPORT_COMMAND`,
# `knowledge.research._load`), not this package.

from __future__ import annotations

from .bug_close import NOTHING_TO_REMOVE, STILL_QUEUED, bug_fixed, bug_invalid  # noqa: F401
from .bugs import (  # noqa: F401
    BUG_REPORT_COMMAND,
    BUG_SCOPES,
    BUG_SEVERITIES,
    bug_file_tasks,
    bug_found,
    upstream_offer,
)
from .lessons import LessonDraft, lesson_add, lesson_search, lessons_verify  # noqa: F401
from .memory import memory_add, memory_forget, memory_list  # noqa: F401
from .pairs import dupes, link_record, pair_records, pairs_from  # noqa: F401
from .research import VERDICTS, Finding, research_add  # noqa: F401
from .retrieval import recall, similar  # noqa: F401
from .sessions import (  # noqa: F401
    history,
    session_adopt_orphans,
    session_end,
    session_note,
    session_prompt,
    session_start,
)
