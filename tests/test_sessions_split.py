"""SZ-split-sessions: `ddflow/services/sessions` is a package (r-small-files) behind the
import path the single 30 KB module had.

Every name the old module defined at top level, public or private, plus `redactor` (the one
imported name a test replaces), is still an attribute of the package, and the package's own
``__init__`` defines nothing. The list was generated from the last single-file version
(``git show 631bfb158:ddflow/services/sessions.py``).

A test that REPLACES a name must patch the submodule that defines it (see
tests/test_module_splits.py): a function reads its own module's globals.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

#: Reads the package's modules from disk, so a module added to it is a change no import reaches.
GOVERNS = ("ddflow/services/sessions/**", "ddflow/services/sessions.py")

PKG = "ddflow.services.sessions"
MAX_CHARS = 20_000

DEFINED = """
    DEDUPE_WINDOW_S DOUBLE_FIRE_S HOOK_START_MAX_S ReplayStep _REPLAY_RENDERERS _age_s _cfg_of
    _link_note _rc_decisions _rc_knowledge _recorded_recently _rs_completed _rs_decision
    _rs_decision_superseded _rs_definition _rs_extended _rs_lesson _rs_link _rs_note _rs_phase
    _rs_prompt _rs_research _rs_resolved _rs_session_ended _rs_session_started
    _rs_skew_overridden _rs_task _rs_upgrade_applied _rs_upgrade_progress adopt_orphans bundle
    capture_prompt end harness_session_id is_started new_session_id note open_sessions orphans
    plan_adoptions process_started_at prompt redact redactor render_reconstruction replay
    resolve start unadopted_orphans verify
""".split()


def test_no_name_the_single_module_defined_is_lost():
    pkg = importlib.import_module(PKG)
    assert [n for n in DEFINED if not hasattr(pkg, n)] == []


def test_the_package_lists_every_name_in_dir():
    pkg = importlib.import_module(PKG)
    assert set(DEFINED) <= set(dir(pkg))


def test_the_init_defines_nothing_and_no_module_is_big():
    pkg = importlib.import_module(PKG)
    init = Path(pkg.__file__)
    if init.name != "__init__.py":  # the single file, before the split
        return
    body = ast.parse(init.read_text("utf-8")).body
    assert [n for n in body if isinstance(n, ast.FunctionDef | ast.ClassDef)] == []
    for p in init.parent.glob("*.py"):
        assert len(p.read_text("utf-8")) < MAX_CHARS, p.name
