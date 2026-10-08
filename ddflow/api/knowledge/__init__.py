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

# The operations live one module per area in this package (B-uni-splits); every name --
# public, private and the modules the single file imported -- is re-exported, so
# `from ddflow.api import knowledge as K; K.bug_found(...)` keeps working. A REBINDING is
# not shared: each function reads names from its own module, so to replace a helper or a
# constant in a test, patch the module that defines it (`knowledge.bugs.BUG_REPORT_COMMAND`,
# `knowledge.research._load`), not this package.

from __future__ import annotations

import ast  # noqa: F401
import re  # noqa: F401
from dataclasses import dataclass  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any  # noqa: F401

import ddflow.api._dedupe as DD  # noqa: F401

from ...config import Config, csv_list  # noqa: F401
from ...core import globspec as GS  # noqa: F401
from ...core import ids as IDS  # noqa: F401
from ...core import outcome as O  # noqa: F401
from ...core.budget import RECALL_MAX_CHARS  # noqa: F401
from ...core.events import parse_changelog  # noqa: F401
from ...core.model import LINK_RELATIONS, fold  # noqa: F401
from .._base import _load  # noqa: F401
from .bug_close import (  # noqa: F401
    NOTHING_TO_REMOVE,
    STILL_QUEUED,
    _capture_lesson,
    _drop_fix_task,
    _drop_fix_task_unchecked,
    _fixing_item,
    _regression_status,
    _verify_regression,
    bug_fixed,
    bug_invalid,
)
from .bugs import (  # noqa: F401
    _FIX_TITLE_MAX,
    BUG_REPORT_COMMAND,
    BUG_SCOPES,
    BUG_SEVERITIES,
    _bug_fields_problem,
    _file_fix_task,
    _fix_task_id,
    _fix_task_of,
    _has_fix_task,
    _link,
    _live,
    _needs_fix_task,
    _open_phase_of,
    _own_fix_id,
    _unknown_bug,
    bug_file_tasks,
    bug_found,
    upstream_offer,
)
from .lessons import (  # noqa: F401
    LessonDraft,
    _store,
    lesson_add,
    lesson_search,
    lessons_verify,
)
from .memory import (  # noqa: F401
    _memory_row,
    memory_add,
    memory_forget,
    memory_list,
)
from .pairs import (  # noqa: F401
    _MIN_PAIR,
    _is_open,
    _record_kind,
    _settled,
    _sweep_records,
    _titled,
    dupes,
    link_record,
    pair_records,
    pairs_from,
)
from .regression import (  # noqa: F401
    _NODE_PATH,
    _PARAMS,
    _PARAMS_LOOSE,
    _bare_names,
    _defines,
    _definitions,
    _inside,
    _looks_like_several,
    _malformed,
    _member,
    _split_outside_brackets,
    _unresolved_tests,
)
from .research import (  # noqa: F401
    VERDICTS,
    Finding,
    _research_fields,
    _research_id_taken,
    research_add,
)
from .retrieval import (  # noqa: F401
    _origin,
    _wire_hit,
    recall,
    similar,
)
from .sessions import (  # noqa: F401
    _EMPTY_SESSION_TEXT,
    history,
    session_adopt_orphans,
    session_end,
    session_note,
    session_prompt,
    session_start,
)
