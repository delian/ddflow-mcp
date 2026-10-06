"""B-uni-splits: `api/reporting`, `services/gates` and `api/knowledge` are packages, one
module per domain, behind the same imports.

Every name the single module defined is still an attribute of the package, the same object
the area module holds, so `from ddflow.services import gates as G; G.run_command_gate(...)`
and every other call site are unchanged. A test that REPLACES a name must patch the area
module that defines it: patching the package rebinds a name no function reads.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

#: What each single module defined before the split -- the names no caller may lose.
DEFINED = {
    "ddflow.api.reporting": """
        DEFAULT_RENDER_DIR STATUS_LIST_LIMIT _DOCTOR_PAIR_CAP _DOCTOR_SWEEP_MAX _RENDERABLE
        _SHARDS_NAMED _SHARDS_SHOWN _TEXT_CAP _UNTITLED_SHOWN _bound _dependency_findings
        _driver_drift_notes _dupe_note _epoch _export_target_notes _finished_phase_remedy
        _launcher_findings _loose_shards _only_ids _orphan_notes _primary_mid_merge _show_bug
        _unknown_author_notes _untitled addenda board doctor loops new_reports progress rebuild
        record_summary recover render replay show status
    """.split(),
    "ddflow.services.gates": """
        DEFAULT_GATES FINGERPRINT_EXCLUDE GateDef GateStatus KEEP_RUN_LOGS LEGACY_CLEAN
        MAX_SUMMARY_LINES MAX_UNTRACKED_HASHED MutationResult REGRESSION_COULD_NOT_RUN
        REGRESSION_FAILS_ON_FIX REGRESSION_PASSES_ON_PREFIX REGRESSION_VERIFIED REVIEWER_GATES
        RUNS_DIR StaleNote TreeEntries _COMMENT _NUMSTAT_FIELDS _PYTEST _PYTEST_NAME
        _PY_MANIFESTS _RUN_FIELDS _SHELL_BUILTINS _SHELL_META _SUMMARY_LINE _VERDICT
        _XDIST_CHOSEN _XDIST_NAME _account_for_drift _blob_id _declared_family _git_z _hash_into
        _index_entries _looks_like_not_found _manifest_texts _missing_executable _ours
        _read_text _run_spec _run_ticking _unapproved_reviewer _untracked_digest
        _untracked_paths _what_differs _working_entry approve classify_exit commit_source_tree
        commit_tree_entries content_id declares_xdist diff_stat differing_paths digest family_of
        gate_config_drift inert_requirements load_gates normal_fingerprint parallel_test_advice
        pipeline_for pipelined pipelines record reviewer_independence rounds_line
        run_command_gate run_log_writer runs_pytest_serially source_tree stale_evidence
        stale_evidence_detail status suggested_test_command summary_lines tree_fingerprint
        triage_counts triage_line uses_pytest verify verify_regression_test worktree_entries
    """.split(),
    "ddflow.api.knowledge": """
        BUG_REPORT_COMMAND BUG_SCOPES BUG_SEVERITIES Finding LessonDraft NOTHING_TO_REMOVE
        STILL_QUEUED VERDICTS _EMPTY_SESSION_TEXT _FIX_TITLE_MAX _MIN_PAIR _bug_fields_problem
        _capture_lesson _defines _definitions _drop_fix_task _drop_fix_task_unchecked
        _file_fix_task _fix_task_id _fix_task_of _has_fix_task _inside _is_open _link _live
        _looks_like_several _member _memory_row _needs_fix_task _open_phase_of _origin
        _own_fix_id _record_kind _research_fields _research_id_taken _settled
        _split_outside_brackets _store _sweep_records _titled _unknown_bug _unresolved_tests
        _verify_regression _wire_hit bug_file_tasks bug_fixed bug_found bug_invalid dupes
        history lesson_add lesson_search lessons_verify link_record memory_add memory_forget
        memory_list pair_records pairs_from recall research_add session_adopt_orphans
        session_end session_note session_prompt session_start similar upstream_offer
    """.split(),
}
#: The single modules were 1,123 to 2,176 lines; no area module should grow back toward that.
MAX_MODULE_LINES = 600


def _areas(pkg):
    return [
        importlib.import_module(f"{pkg.__name__}.{m.name}")
        for m in pkgutil.iter_modules(pkg.__path__)
    ]


def _top_level_names(path: Path) -> set[str]:
    """Every name a module binds at top level: defs, classes, assignments, imports."""
    names: set[str] = set()
    for n in ast.parse(path.read_text("utf-8")).body:
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(n.name)
        elif isinstance(n, ast.Assign | ast.AnnAssign):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            names |= {x.id for t in targets for x in ast.walk(t) if isinstance(x, ast.Name)}
        elif isinstance(n, ast.Import | ast.ImportFrom):
            names |= {a.asname or a.name.split(".")[0] for a in n.names}
    return names - {"annotations"}


@pytest.fixture(params=sorted(DEFINED), ids=lambda m: m.rsplit(".", 1)[1])
def pkg(request):
    return importlib.import_module(request.param)


def test_it_is_a_package_and_the_init_defines_nothing(pkg):
    init = Path(pkg.__file__)
    assert init.name == "__init__.py"
    body = ast.parse(init.read_text("utf-8")).body
    defs = [n for n in body if not isinstance(n, ast.Import | ast.ImportFrom | ast.Expr)]
    assert not defs, [ast.unparse(d)[:60] for d in defs]


def test_no_name_the_single_module_defined_is_lost(pkg):
    missing = [n for n in DEFINED[pkg.__name__] if not hasattr(pkg, n)]
    assert not missing, missing


#: Modules .importlinter confines to one home: an area that uses one imports it, and the
#: package does not import it again just to re-export it.
CONFINED = {"subprocess", "tempfile", "hashlib", "fcntl"}


def test_every_name_an_area_binds_is_the_same_object_on_the_package(pkg):
    """Functions, constants and the modules an area imports alike: whatever an area binds
    is reachable as `<package>.<name>`, the same object (two areas binding one name would
    leave the package holding only one of them)."""
    for mod in _areas(pkg):
        for name in _top_level_names(Path(mod.__file__)) - CONFINED:
            assert getattr(pkg, name, None) is getattr(mod, name), (
                f"{pkg.__name__}.{name} is not {mod.__name__}.{name}"
            )


def test_an_area_is_reachable_as_its_module(pkg):
    """No area shares its name with a function: `gates.evidence` is the module, so a test
    can patch `gates.evidence.MAX_UNTRACKED_HASHED`."""
    for mod in _areas(pkg):
        assert getattr(pkg, mod.__name__.rsplit(".", 1)[1]) is mod


def test_each_defined_name_lives_in_exactly_one_area(pkg):
    homes: dict[str, list[str]] = {}
    for mod in _areas(pkg):
        for n in ast.parse(Path(mod.__file__).read_text("utf-8")).body:
            if isinstance(n, ast.FunctionDef | ast.ClassDef):
                homes.setdefault(n.name, []).append(mod.__name__)
    assert {k: v for k, v in homes.items() if len(v) > 1} == {}
    assert set(homes) <= set(DEFINED[pkg.__name__])


def test_no_area_module_is_a_monolith_again(pkg):
    for mod in _areas(pkg):
        n = len(Path(mod.__file__).read_text("utf-8").splitlines())
        assert n < MAX_MODULE_LINES, f"{mod.__name__} is {n} lines"


def test_patching_the_package_does_not_reach_an_area(monkeypatch):
    """Why the tests that replace a name patch the area: a function reads its own module's
    globals. Pinned so nobody 'fixes' a test back to patching the package."""
    from ddflow.services import gates as G

    monkeypatch.setattr(G, "MAX_UNTRACKED_HASHED", -1)
    assert G.evidence.MAX_UNTRACKED_HASHED != -1
