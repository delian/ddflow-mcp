"""The application layer: one typed entry point per operation, for both surfaces.

`surfaces/mcp.py` reached `surfaces/cli.py` — a protocol adapter depending on a
presentation layer — and the edge was carried by **strings**. Typed MCP arguments were
flattened to argv, re-parsed by argparse, and the result recovered by scraping stdout
plus an exit code. Two costs are visible in the code itself:

* `mcp._opt(..., clearable=True)` exists only to rebuild the "absent vs empty"
  distinction argv erased. `ddflow_update(id="X", needs="")` silently did nothing while
  `ddflow update X --needs ""` cleared the field — and breaking a dependency cycle is
  exactly the operation that needs it, and the one the loop detector tells you to do.
  In a typed call `None` and `""` are simply different values and no helper is needed.
* `mcp._run_cli` swaps process-global `sys.stdout`/`sys.stderr` for every call. That is
  not reentrant: it forecloses concurrency in a server for a tool whose entire purpose
  is parallel agents.

Parity was held by ratchets where types would hold it structurally, and those ratchets
catch a *missing* flag, never a *changed encoding*.

**Migration, not a rewrite.** Each operation here is one a surface used to implement
inline. A tool with an `api` entry is dispatched through this module; the rest still go
through argv, and `tests/test_mcp_parity.py` counts the remainder and refuses to let it
grow. A big-bang port of ~60 commands would be one unreviewable change against a suite
that cannot tell which half broke.

Every function takes plain values and returns an `Outcome` — one description of a
result, from which both the machine view (`data`) and the human view are derived. It is
never both surfaces describing the same thing independently, which is the shape that
produced the bug still commented in `cmd_complete`: a coverage gap printed in human
mode only, invisible to the agent reading JSON that most needed it.
"""

from __future__ import annotations

# Re-exported so `api.progress(...)` keeps working after the split. The surfaces reach
# this layer through the PACKAGE (`_api().progress`), never through a family module, so
# moving an operation between families is not a breaking change.
from ._base import _load
from .completion import completion_verdict
from .decisions import (
    decision_add,
    decision_applicable,
    decision_list,
    decision_search,
    decision_show,
    decision_supersede,
)
from .gates import Evidence as GateEvidence
from .gates import record as gate_record
from .gates import run as gate_run
from .gates import status as gate_status
from .gates import verify as gate_verify
from .items import DEFAULT_PRIORITY, phase_add, split, task_add, update
from .knowledge import (
    Finding as ResearchFinding,
)
from .knowledge import (
    bug_fixed,
    bug_found,
    history,
    lesson_add,
    lesson_search,
    recall,
    research_add,
    session_end,
    session_note,
    session_prompt,
    session_start,
)
from .lifecycle import (
    DEFAULT_CHECK_RECOVERY,
    DEFAULT_NEXT_KIND,
    abandon,
    block,
    brief,
    claim,
    complete,
    heartbeat,
)
from .lifecycle import merge as merge_item
from .lifecycle import next_ as next_item
from .lifecycle import release as release_item
from .lifecycle import remove as remove_item
from .reporting import (
    DEFAULT_RENDER_DIR,
    board,
    doctor,
    loops,
    progress,
    rebuild,
    recover,
    render,
    replay,
    show,
    status,
)
from .workflow import GateEdit as WorkflowGateEdit
from .workflow import drop as workflow_drop
from .workflow import gate as workflow_gate
from .workflow import pipeline as workflow_pipeline
from .workflow import show as workflow_show

__all__ = [
    "ResearchFinding",
    "bug_fixed",
    "bug_found",
    "history",
    "lesson_add",
    "lesson_search",
    "recall",
    "research_add",
    "session_end",
    "session_note",
    "session_prompt",
    "session_start",
    "DEFAULT_CHECK_RECOVERY",
    "DEFAULT_NEXT_KIND",
    "DEFAULT_PRIORITY",
    "DEFAULT_RENDER_DIR",
    "GateEvidence",
    "WorkflowGateEdit",
    "_load",
    "abandon",
    "block",
    "board",
    "brief",
    "claim",
    "complete",
    "completion_verdict",
    "decision_add",
    "decision_applicable",
    "decision_list",
    "decision_search",
    "decision_show",
    "decision_supersede",
    "doctor",
    "gate_record",
    "gate_run",
    "gate_status",
    "gate_verify",
    "heartbeat",
    "loops",
    "merge_item",
    "next_item",
    "phase_add",
    "progress",
    "rebuild",
    "recover",
    "release_item",
    "remove_item",
    "render",
    "replay",
    "show",
    "split",
    "status",
    "task_add",
    "update",
    "workflow_drop",
    "workflow_gate",
    "workflow_pipeline",
    "workflow_show",
]
