"""Ratchet: a numeric knob must declare its own ``check=`` (R-audit-code: numeric-knob
validation was one of the classes the existing ratchets did not see).

Every numeric knob (its field type spells ``int`` or ``float``; see
``ddflow/config/_knob.kind``) is held, on load, to the universal rule -- the right kind of
number, finite, and not below its floor -- by ``checked_number``. That rule cannot know a
knob's DOMAIN bound (a timeout that must be at least 2 s, a sample count that must be at
least 2), so a numeric knob should also declare its own ``check=`` on ``knob()``. The knobs
that do not yet are the shrink-only baseline ``_UNCHECKED`` below:

- a numeric knob with no ``check=`` that is not listed fails (add the check), and
- a key that gained a ``check=`` or went away fails (delete it, lowering the baseline),
  so the baseline can only shrink.

Sized S, RISK low. Falsifier: a new unchecked numeric knob passes.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from ddflow.config._knob import kind as _numeric_kind
from ddflow.config_sections import _docs as D

#: The numeric knobs (int/float) that declare no ``check=`` of their own, keyed
#: "section.knob", in declaration order. Add a ``check=`` to one and DELETE its key here --
#: this list only shrinks. Never add a key for a new unchecked knob: give it a ``check=``.
_UNCHECKED: set[str] = {
    "agent.ask_code_length",
    "agent.ask_ttl_s",
    "approvals.expiry_warn_s",
    "approvals.min_digest_chars",
    "approvals.policy_max_uses",
    "approvals.policy_unavailable_runs",
    "approvals.reason_chars",
    "approvals.use_ttl_s",
    "cadence.architecture_review_every_phases",
    "cadence.dedupe_sweep_every_tasks",
    "cadence.integration_tests_every_tasks",
    "cadence.lessons_pass_every_phases",
    "cadence.max_missed",
    "cadence.mutation_tests_every_phases",
    "ci.max_concurrent_suites",
    "ci.max_workers",
    "ci.min_workers",
    "ci.selected_lock_min_runs",
    "ci.selected_lock_runs",
    "ci.selected_lock_s",
    "ci.selected_lock_tests",
    "ci.suite_min_workers",
    "ci.suite_queue_grace_s",
    "ci.suite_queue_stale_s",
    "ci.suite_wait_s",
    "ci.timeout_s",
    "companions.probe_cache_ttl_s",
    "companions.tool_probe_timeout_s",
    "companions.tool_run_timeout_s",
    "enforce.complexity_warn_at",
    "eta.min_samples",
    "eta.window_days",
    "gates.baseline_cache_ttl_s",
    "gates.baseline_red_rate_warn",
    "gates.max_run_timeout_s",
    "gates.rate_max_fail",
    "gates.rate_min_runs",
    "gates.standards_wait_s",
    "gates.unit_tests_hub_importers",
    "gates.unit_tests_max_files",
    "gates.unit_tests_max_hops",
    "gates.unit_tests_name_max_tests",
    "gates.unit_tests_small_lines",
    "grants.max_count",
    "grants.ttl_h",
    "hooks.fixture_max_age_months",
    "hooks.fixture_max_reply_bytes",
    "importer.max_tasks",
    "importer.preview_rows",
    "lanes.advise_max",
    "lanes.brief_notes",
    "lanes.brief_text_chars",
    "lanes.event_buffer",
    "lanes.stale_after_s",
    "lanes.stop_grace_s",
    "lanes.watch_interval_s",
    "lanes.window_s",
    "lease.acquire_timeout_s",
    "lease.grace_s",
    "lease.heartbeat_s",
    "lease.ttl_s",
    "lease.wait_max_s",
    "lease.waiter_reservation_s",
    "lessons.cadence_growth_pct",
    "lessons.cadence_min_entries",
    "lessons.max_results",
    "lessons.reflect_after_items",
    "lessons.snippet_chars",
    "lessons.verify_load_ratio",
    "lessons.verify_output_lines",
    "log.max_cached_events",
    "loops.max_claims_per_item",
    "loops.max_duplicate_items",
    "loops.max_gate_flaps",
    "loops.max_reopens",
    "loops.max_repeated_failures",
    "loops.no_progress_window",
    "mcp.stale_check_every_s",
    "memory.brief_items",
    "memory.max_chars",
    "metrics.forecast_runs",
    "metrics.forecast_seed",
    "metrics.grid_max_folds",
    "metrics.grid_step_s",
    "metrics.lock_falsifier_conflict_pct",
    "metrics.lock_falsifier_red_main_pct",
    "metrics.lock_probe_max",
    "metrics.lock_red_main_window_s",
    "metrics.min_samples",
    "metrics.stage_size_large",
    "metrics.stage_size_small",
    "metrics.top",
    "notify.ask_timeout_s",
    "notify.send_timeout_s",
    "reinstruct.every_calls",
    "reinstruct.every_seconds",
    "reinstruct.max_items",
    "review.chunk_max_tokens",
    "review.combined_under_lines",
    "review.degraded_cooldown_s",
    "review.manifest_max_files",
    "review.max_rounds",
    "review.split_rounds",
    "rules.max_rules",
    "rules.max_size_bytes",
    "session.agent_brief_decisions",
    "session.agent_brief_lessons",
    "session.agent_brief_max_chars",
    "session.brief_lesson_count",
    "session.brief_max_tokens",
    "session.claim_rules_reserved",
    "session.compaction_digest_chars",
    "session.first_call_max_tokens",
    "session.report_brief_chars",
    "session.report_commits",
    "session.report_note_chars",
    "session.report_summary_chars",
    "session.report_test_files",
    "session.report_title_chars",
    "session.rules_charter_title_chars",
    "session.rules_charter_tokens",
    "upgrade.backup_keep",
    "upgrade.check_interval_h",
    "upgrade.start_timeout_s",
}


def numeric_knobs() -> dict[str, str]:
    """{"section.knob": "int"|"float"} for every knob whose field type is a number."""
    out: dict[str, str] = {}
    for section, cls in D.SECTION_CLASSES.items():
        for f in fields(cls):
            if D.KNOB not in f.metadata:
                continue
            ts: Any = f.type if isinstance(f.type, str) else getattr(f.type, "__name__", str(f.type))
            number = _numeric_kind(ts)
            if number:
                out[f"{section}.{f.name}"] = number
    return out


def unchecked() -> set[str]:
    """The keys of the numeric knobs that declare no ``check=`` (the gap this ratchet holds)."""
    return {key for key in numeric_knobs() if D.DECLARED[key].check is None}


def test_a_new_unchecked_numeric_knob_is_refused() -> None:
    """A numeric knob with no ``check=`` that the baseline does not name is a new gap: add its
    ``check=``. This is the falsifier: a new unchecked knob must not pass."""
    new = sorted(unchecked() - _UNCHECKED)
    assert not new, f"numeric knobs that declare no check=; give each a check= on knob(): {new}"


def test_the_baseline_only_shrinks() -> None:
    """A key that gained a ``check=`` (or went away) is stale: delete it from `_UNCHECKED`,
    so the baseline cannot grow back into the room a check left."""
    stale = sorted(_UNCHECKED - unchecked())
    assert not stale, (
        "baseline keys that now declare a check= (or no longer exist); delete them from "
        f"_UNCHECKED in tests/test_numeric_knob_ratchet.py: {stale}"
    )


def test_the_scan_is_not_vacuous() -> None:
    """The registry is really read: hundreds of numeric knobs, most of them checked."""
    knobs = numeric_knobs()
    checked = sum(1 for key in knobs if D.DECLARED[key].check is not None)
    assert len(knobs) > 200
    assert checked > len(knobs) // 2


def test_a_checked_knob_is_not_counted() -> None:
    """`kind` spelling is a number, and not a bool, a string or a list (the ratchet would
    otherwise ask a field that is no number to declare a check)."""
    assert _numeric_kind("int") == "int"
    assert _numeric_kind("float") == "float"
    assert _numeric_kind("int | None") == "int"
    assert _numeric_kind("Optional[float]") == "float"
    assert _numeric_kind("bool") == ""
    assert _numeric_kind("str") == ""
    assert _numeric_kind("list[str]") == ""
