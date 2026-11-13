"""Human-readable projections: boards, lessons, and the session brief.

This package is the former ``ddflow/views/markdown.py``, split into submodules that
stay under 30k chars each (rule r-small-files). The public API is unchanged: every
name the old module exported is re-exported here, so ``from ddflow.views.markdown
import board`` and ``from ddflow.views import markdown as M`` bind exactly what they
bound before.

The re-export is LAZY (``__getattr__``): the package imports no submodule until one
name is asked for. That keeps two invariants a one-file module had and a package must
keep — monkeypatch (``monkeypatch.setattr(M, name, ...)`` re-binds the name on THIS
module exactly as before) and the layering ratchet (``test_no_mutually_importing_pair``
would read an eager ``from .views import ...`` here as a latent parent<->child cycle).
"""

from __future__ import annotations

from ddflow.core import _reexport

from ...core.schedule import critical_path as critical_path

#: Public (and patch-covered private) name -> the submodule that defines it.
_SUBMODULE_OF = {
    # head
    "GENERATED": "head",
    "GENERATED_PREFIX": "head",
    "NewerView": "head",
    "ViewHead": "head",
    "_VIEW_HEAD": "head",
    "split_view": "head",
    "stamp_view": "head",
    "view_difference": "head",
    # views
    "VIEWS": "views",
    "_SUMMARY_FALLBACK_CHARS": "views",
    "_bar": "views",
    "_cell": "views",
    "_depth": "views",
    "_fold": "views",
    "_id_cell": "views",
    "_nested": "views",
    "_redact": "views",
    "_summary_of": "views",
    "board": "views",
    "bugs_md": "views",
    "decisions_md": "views",
    "lessons_md": "views",
    "lessons_summary_md": "views",
    "render_views": "views",
    "research_md": "views",
    "sessions_md": "views",
    "unphased": "views",
    "write_views": "views",
    # report
    "_HOLDING": "report",
    "_REPORT_CAP": "report",
    "_REPORTS_SHOWN": "report",
    "_RECOVERY_SHOWN": "report",
    "_approx_tokens": "report",
    "_clip": "report",
    "_reason": "report",
    "_recovery_band": "report",
    "_ref": "report",
    "_score": "report",
    "_CAPPED": "report",
    "_UNHELD": "report",
    "_UNMEASURED": "report",
    "addenda_lines": "report",
    "may_hold_work": "report",
    "new_reports_block": "report",
    "new_reports_line": "report",
    "recovery_band": "report",
    # brief
    "BRIEF_DECISION_CHARS": "briefing",
    "BRIEF_OTHER_JOBS": "briefing",
    "SECTION_TRIMMED": "briefing",
    "_CAPPED_SHOWN": "briefing",
    "_SECTION_FLOOR": "briefing",
    "_SECTION_MORE": "briefing",
    "_SECTION_NAMED": "briefing",
    "_SECTION_SHARE": "briefing",
    "_allowances": "briefing",
    "_assemble": "briefing",
    "_brief_current": "briefing",
    "_brief_decisions": "briefing",
    "_brief_jobs": "briefing",
    "_brief_lessons": "briefing",
    "_brief_memories": "briefing",
    "_brief_ready": "briefing",
    "_brief_recovery": "briefing",
    "_brief_skills": "briefing",
    "_fit_sections": "briefing",
    "_gate_facts": "briefing",
    "_last_prompt_of": "briefing",
    "_record_id": "briefing",
    "_resume_line": "briefing",
    "_trim_section": "briefing",
    "brief": "briefing",
    "cap_held": "briefing",
}

_reexport.reexport(globals(), _SUBMODULE_OF, __name__)
del _reexport
