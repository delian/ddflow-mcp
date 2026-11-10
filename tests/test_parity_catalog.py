"""The feature catalog: every in-agent and orchestrator feature is a data row, and each row
names the descriptor field(s) that answer it and the offline conformance check(s) that grade
it -- all of which already exist elsewhere. This test pins that contract, so a row that drifts
from the descriptor schema, or cites a check nobody implemented, fails here.

It deliberately does NOT reimplement any check: the backing modules
(``ddflow.services.driver_conformance``, ``ddflow.services.swarm.conformance`` and
``tests/harness_conformance/declared.py``) remain the single owners of each check.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ddflow.services import harnessreg as HR
from ddflow.services import harnessv2 as HV
from ddflow.services.parity import catalog as C
from ddflow.services.swarm import conformance as CF

ROOT = Path(__file__).resolve().parents[1]


def _field_paths():
    """The descriptor schema's real ``section.key`` paths, computed from the live
    dataclasses, so the test asserts against the schema itself rather than a copy."""


@pytest.fixture(scope="module")
def catalog():
    return C.load()


def test_both_axes_have_rows(catalog):
    assert catalog.by_axis(C.IN_AGENT), "the in-agent axis has at least one feature"
    assert catalog.by_axis(C.ORCHESTRATOR), "the orchestrator axis has at least one feature"
    # every feature the task listed is present (13 in-agent + 13 orchestrator)
    assert len(catalog.rows) >= 26


def test_every_feature_is_unique(catalog):
    seen = [r.feature for r in catalog.rows]
    assert len(seen) == len(set(seen)), "feature slugs are unique across both axes"


def test_every_descriptor_field_resolves(catalog):
    for row in catalog.rows:
        for path in row.descriptor_fields:
            assert path in C.field_paths(), (row.feature, path)


def test_every_check_resolves(catalog):
    for row in catalog.rows:
        # a documented gap row (ddflow-side, no per-agent field) names no check on purpose
        if not row.descriptor_fields:
            assert not row.checks and row.note, (row.feature, "a gap row documents why")
            continue
        assert row.checks, (row.feature, "a graded row names at least one offline check")
        for cid in row.checks:
            assert C.known_check(cid), (row.feature, cid)


def test_documented_gap_rows_name_a_ddflow_side_knob(catalog):
    gap = {r.feature for r in catalog.rows if not r.descriptor_fields}
    assert gap, "the ddflow-side features are recorded (never guessed)"
    assert "session-start-brief" in gap and "key-by-env-name" in gap


def test_the_catalog_cites_the_existing_checks_it_must_reuse():
    cited = {cid for row in C.load().rows for cid in row.checks}
    # the declared payload-fixture check (per canonical event) is reused, not rebuilt
    assert any(c.startswith("hook:") and c.partition(":")[2] in HR.CANONICAL_EVENTS for c in cited)
    # the descriptor-only checks and the lane conformance levels are reused, not rebuilt
    assert {"facts", "hooks"} <= cited
    # the lane check is cited for the conformance-levels feature, and the levels it grades
    # are the real swarm-conformance ladder (1-7), not the bare token
    levels_row = C.load().by_feature("conformance-levels-1-7")
    assert "lane" in levels_row.checks
    assert CF.CHECKS and len(CF.CHECKS) == 7  # launch claim heartbeat gate_record hook lease_stop tool_accuracy


def test_lane_and_declared_check_modules_exist_and_are_the_single_owner():
    # The catalog reuses these; if they moved or emptied, the catalog would silently cite a
    # check nobody runs. Import the declared-check owner and pin its kind constants, so a
    # catalog citing `argv`/`stream`/`hook:` is provably cited from THIS module, not forged.
    from harness_conformance import declared as D

    assert HR.CANONICAL_EVENTS
    assert CF.CHECKS
    assert C.FIXED_CHECKS == ("facts", "hooks")
    assert D.HOOK == "hook" and D.ARGV == "argv" and D.STREAM == "stream"
    assert set(C.DECLARED_CHECKS) == {D.ARGV, D.STREAM}


def test_rows_are_data_not_code():
    # The catalog is a TOML data file and a thin loader; the loader adds no per-agent code.
    assert (ROOT / "ddflow" / "harnesses" / "catalog.toml").is_file()
    assert "python" not in (ROOT / "ddflow" / "harnesses" / "catalog.toml").suffix


def test_every_active_row_has_an_add_history_entry(catalog):
    added = {h.feature for h in catalog.history if h.action == "add"}
    for row in catalog.rows:
        assert row.feature in added, (row.feature, "an active row was added via a history entry")


def test_history_actions_are_known(catalog):
    for entry in catalog.history:
        assert entry.action in C.HISTORY_ACTIONS, entry


def test_the_catalog_files_stay_under_the_size_ratchet():
    for rel in ("ddflow/harnesses/catalog.toml", "ddflow/services/parity/catalog.py"):
        size = (ROOT / rel).stat().st_size
        assert size < 20 * 1024, (rel, size)


def test_a_row_with_no_check_is_refused(tmp_path):
    _write(tmp_path, "[in_agent]\nfeature = \"x\"\ndescriptor_fields = [\"env\"]\nchecks = []\n")
    with pytest.raises(C.CatalogError):
        C.load(tmp_path / "catalog.toml")


def test_a_live_row_without_an_add_history_entry_is_refused(tmp_path):
    _write(
        tmp_path,
        "[in_agent]\nfeature = \"x\"\ndescriptor_fields = [\"env\"]\nchecks = [\"facts\"]\n",
    )
    with pytest.raises(C.CatalogError):
        C.load(tmp_path / "catalog.toml")


def test_a_non_string_check_is_refused(tmp_path):
    _write(
        tmp_path,
        "[in_agent]\nfeature = \"x\"\ndescriptor_fields = [\"env\"]\nchecks = [1]\n"
        "[[history]]\nfeature = \"x\"\naction = \"add\"\ndate = \"2026-11-10\"\n",
    )
    with pytest.raises(C.CatalogError):
        C.load(tmp_path / "catalog.toml")


def test_an_orphan_add_history_entry_is_refused(tmp_path):
    _write(
        tmp_path,
        "[in_agent]\nfeature = \"x\"\ndescriptor_fields = [\"env\"]\nchecks = [\"facts\"]\n"
        "[[history]]\nfeature = \"x\"\naction = \"add\"\ndate = \"2026-11-10\"\n"
        "[[history]]\nfeature = \"ghost\"\naction = \"add\"\ndate = \"2026-11-10\"\n",
    )
    with pytest.raises(C.CatalogError):
        C.load(tmp_path / "catalog.toml")


def test_a_retired_slug_must_not_stay_live(tmp_path):
    _write(
        tmp_path,
        "[in_agent]\nfeature = \"x\"\ndescriptor_fields = [\"env\"]\nchecks = [\"facts\"]\n"
        "[[history]]\nfeature = \"x\"\naction = \"retire\"\ndate = \"2026-11-10\"\n",
    )
    with pytest.raises(C.CatalogError):
        C.load(tmp_path / "catalog.toml")


def test_an_unknown_top_level_table_is_refused(tmp_path):
    _write(
        tmp_path,
        "[in_agent]\nfeature = \"x\"\ndescriptor_fields = [\"env\"]\nchecks = [\"facts\"]\n"
        "[[history]]\nfeature = \"x\"\naction = \"add\"\ndate = \"2026-11-10\"\n"
        "[[features]]\nfeature = \"y\"\ndescriptor_fields = [\"env\"]\nchecks = [\"facts\"]\n",
    )
    with pytest.raises(C.CatalogError):
        C.load(tmp_path / "catalog.toml")


def _write(tmp_path, body: str) -> None:
    (tmp_path / "catalog.toml").write_text(body)


