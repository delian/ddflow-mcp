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

import importlib
from typing import Any

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
    "_UNHELD": "report",
    "_UNMEASURED": "report",
    "addenda_lines": "report",
    "may_hold_work": "report",
    "new_reports_block": "report",
    "new_reports_line": "report",
    "recovery_band": "report",
    # brief
    "BRIEF_DECISION_CHARS": "brief",
    "BRIEF_OTHER_JOBS": "brief",
    "SECTION_TRIMMED": "brief",
    "_CAPPED_SHOWN": "brief",
    "_SECTION_FLOOR": "brief",
    "_SECTION_MORE": "brief",
    "_SECTION_NAMED": "brief",
    "_SECTION_SHARE": "brief",
    "_allowances": "brief",
    "_assemble": "brief",
    "_brief_current": "brief",
    "_brief_decisions": "brief",
    "_brief_jobs": "brief",
    "_brief_lessons": "brief",
    "_brief_memories": "brief",
    "_brief_ready": "brief",
    "_brief_recovery": "brief",
    "_brief_skills": "brief",
    "_fit_sections": "brief",
    "_gate_facts": "brief",
    "_last_prompt_of": "brief",
    "_record_id": "brief",
    "_resume_line": "brief",
    "_trim_section": "brief",
    "brief": "brief",
    "cap_held": "brief",
}

_MODULES: dict[str, Any] = {}


def __getattr__(name: str) -> Any:
    sub = _SUBMODULE_OF.get(name)
    if sub is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod = _MODULES.get(sub)
    if mod is None:
        mod = importlib.import_module(f"{__name__}.{sub}")
        _MODULES[sub] = mod
    return getattr(mod, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_SUBMODULE_OF))
