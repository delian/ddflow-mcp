"""Sessions, prompt provenance, and reconstruction from the log alone.

The requirement this module serves is unusual and worth stating plainly: *if
everything except the log were lost, could the project be rebuilt?* Not byte-for-byte
— an LLM is not a deterministic function, so replaying prompts cannot reproduce the
same source. What CAN be reproduced is the **decision history**: every operator
instruction, in order, with the state it applied to, the research that informed it, the
gates it passed and the commit it produced.

So ddflow records two layers, and keeps them separate on purpose:

* **Intent** — operator prompts, research verdicts, lessons, the queue's shape. This
  is irreplaceable; no artefact elsewhere contains it. It is what ``replay`` emits.
* **Outcome** — commit shas, gate evidence, output digests. This is *verification*
  data: it cannot rebuild anything, but it proves whether a rebuild matches what
  happened, and ``replay --verify`` checks each recorded sha still resolves.

The literature calls this an event-sourced agent: the log is the agent, the working
state is a projection, and replay reconstructs a run by folding forward rather than by
restoring a snapshot (Sanders et al., *The Log is the Agent*, arXiv:2605.21997).
The determinism caveat is theirs too — replay is made sound by recording responses,
not by assuming they reproduce.

**Redaction is applied on the way in, not on the way out.** The log is committed to
git, so a secret written once is a secret leaked permanently; scrubbing at read time
would be a scrub that a `git show` walks straight past.

This package is the former ``ddflow/services/sessions.py``, split into submodules that
stay under 20 KB each (rule r-small-files, decision D-small-files-30k): ``_record`` (redact,
session ids, prompt/note capture, start/end), ``_replay`` (the intent history) and
``_reconstruct`` (the rebuild brief, `verify`, `bundle`). Every name the old module
defined is re-exported LAZILY (``core._reexport``), so ``from ddflow.services import
sessions as S`` binds what it bound before. A test that REPLACES a name patches the
submodule that defines it (e.g. ``sessions._record.redactor``).
"""

from __future__ import annotations

from ddflow.core import _reexport

#: Public (and patch-covered private) name -> the submodule that defines it.
_SUBMODULE_OF = {
    "_rc_decisions": "_reconstruct",
    "_rc_knowledge": "_reconstruct",
    "bundle": "_reconstruct",
    "render_reconstruction": "_reconstruct",
    "verify": "_reconstruct",
    "DEDUPE_WINDOW_S": "_record",
    "DOUBLE_FIRE_S": "_record",
    "HOOK_START_MAX_S": "_record",
    "_age_s": "_record",
    "_cfg_of": "_record",
    "_recorded_recently": "_record",
    "adopt_orphans": "_record",
    "capture_prompt": "_record",
    "end": "_record",
    "harness_session_id": "_record",
    "is_started": "_record",
    "new_session_id": "_record",
    "note": "_record",
    "open_sessions": "_record",
    "orphans": "_record",
    "plan_adoptions": "_record",
    "process_started_at": "_record",
    "prompt": "_record",
    "redact": "_record",
    "redactor": "_record",
    "resolve": "_record",
    "start": "_record",
    "unadopted_orphans": "_record",
    "ReplayStep": "_replay",
    "_REPLAY_RENDERERS": "_replay",
    "_link_note": "_replay",
    "_rs_completed": "_replay",
    "_rs_decision": "_replay",
    "_rs_decision_superseded": "_replay",
    "_rs_definition": "_replay",
    "_rs_extended": "_replay",
    "_rs_lesson": "_replay",
    "_rs_link": "_replay",
    "_rs_note": "_replay",
    "_rs_phase": "_replay",
    "_rs_prompt": "_replay",
    "_rs_research": "_replay",
    "_rs_resolved": "_replay",
    "_rs_session_ended": "_replay",
    "_rs_session_started": "_replay",
    "_rs_skew_overridden": "_replay",
    "_rs_task": "_replay",
    "_rs_upgrade_applied": "_replay",
    "_rs_upgrade_progress": "_replay",
    "replay": "_replay",
}

_reexport.reexport(globals(), _SUBMODULE_OF, __name__)
del _reexport
