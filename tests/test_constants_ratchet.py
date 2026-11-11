"""Ratchet: a tunable module-level constant is a knob to migrate or a reasoned exemption.

D-everything-configurable rule 6. The register is docs/ddflow/constants-exemptions.toml;
the scan is ddflow/services/constants_audit.py. A NEW constant in neither table fails, and
so does a register entry nothing matches any more, so the register only shrinks.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ddflow.services import constants_audit as ca

ROOT = Path(__file__).resolve().parents[1]


def test_no_new_unclassified_constant() -> None:
    a = ca.audit(ROOT)
    assert not a.unclassified, (
        "tunable constants in neither table of docs/ddflow/constants-exemptions.toml -- make "
        "each a config knob, or list it under [exempt] with a reason: "
        f"{[c.key for c in a.unclassified]}"
    )


def test_the_register_only_shrinks() -> None:
    a = ca.audit(ROOT)
    assert not a.stale, f"register entries nothing matches (migrated or renamed? remove): {a.stale}"
    assert not a.both, f"listed as both knob and exemption: {a.both}"
    assert not a.unreasoned, f"exemptions without a reason: {a.unreasoned}"


def test_the_scan_is_not_vacuous() -> None:
    found = ca.scan(ROOT)
    assert len(found) > 100
    assert len({c.path for c in found}) > 40


def _plant(tmp_path: Path, source: str, register: str = "") -> ca.Audit:
    (tmp_path / "ddflow").mkdir(exist_ok=True)
    (tmp_path / "ddflow" / "m.py").write_text(source)
    (tmp_path / "docs" / "ddflow").mkdir(parents=True, exist_ok=True)
    (tmp_path / ca.REGISTER).write_text(register)
    return ca.audit(tmp_path)


def test_detector_flags_a_planted_tunable(tmp_path: Path) -> None:
    a = _plant(tmp_path, "FETCH_TIMEOUT_S = 30\n_MAX_ROWS: int = 5\nNEG_LIMIT = -1\n")
    assert [c.name for c in a.unclassified] == ["FETCH_TIMEOUT_S", "_MAX_ROWS", "NEG_LIMIT"]


def test_detector_folds_literal_arithmetic(tmp_path: Path) -> None:
    a = _plant(
        tmp_path,
        "A_WINDOW_S = 30 * 60.0\nB_MAX_BYTES = 1 << 20\nC_LIMIT = 2 ** 100000\nD_MAX = 1 / 0\n",
    )
    assert [(c.name, c.value) for c in a.unclassified] == [
        ("A_WINDOW_S", 1800.0),
        ("B_MAX_BYTES", 1048576),
    ]


def test_detector_folds_symbolic_name_arithmetic(tmp_path: Path) -> None:
    a = _plant(
        tmp_path,
        "HOUR = 3600.0\nA_WINDOW_S = 7 * 24 * HOUR\nB_LIMIT = HOUR * 2\n",
    )
    assert [(c.name, c.value) for c in a.unclassified] == [
        ("A_WINDOW_S", 604800.0),
        ("B_LIMIT", 7200.0),
    ]


def test_bug_window_is_a_single_knob_not_a_hidden_duplicate(tmp_path: Path) -> None:
    """B82352e0b9f: swarm_metrics.py duplicated ledger.py's BUG_WINDOW_S as
    `7 * 24 * HOUR`; the pre-fold detector could not fold a Name operand, so a
    ROOT scan passed vacuously on the unfixed tree (the duplicate was invisible
    either way). Feed the detector the duplicate's exact shape and require it to
    fold it to its literal value -- that part fails on a detector that still
    cannot see `7 * 24 * HOUR` -- and keep the single-knob check on the real
    tree so a re-introduced duplicate can never hide."""
    a = _plant(
        tmp_path,
        "HOUR = 3600.0\nBUG_WINDOW_S = 7 * 24 * HOUR\n",
    )
    assert [(c.name, c.value) for c in a.unclassified] == [("BUG_WINDOW_S", 604800.0)]
    keys = {c.key for c in ca.scan(ROOT)}
    assert "ddflow/services/swarm/ledger.py:BUG_WINDOW_S" in keys
    assert "ddflow/services/swarm_metrics.py:BUG_WINDOW_S" not in keys


def test_lane_headless_secret_min_is_exempt_and_reasoned() -> None:
    """B-lanes-knobs-manifest-secretmin-ratchet: SECRET_MIN shipped in neither table and
    the ratchet went red. It is a redaction heuristic (shorter values are not worth
    redacting and would mangle text), so it is an [exempt] structural constant, not a tunable
    knob -- lock that it stays classified, WITH a reason, wherever lane_headless is re-homed."""
    reg = ca.load_register(ROOT)
    key = "ddflow/services/lane_headless.py:SECRET_MIN"
    assert reg.exempt.get(key), f"SECRET_MIN left [exempt] (or lost its reason): {key}"


def test_ci_main_drift_reclassifies_moves_and_new_constants() -> None:
    """B-ci-main-constants-ratchet: main went red on real register drift -- the package
    splits moved constants to new modules (re-pinned at the CURRENT home, old key gone)
    and newly-landed constants shipped unclassified. Lock the re-pin and the reasoned
    classification, so a later split must re-pin again rather than drift silently."""
    reg = ca.load_register(ROOT)
    for key in (
        "ddflow/api/records/_kind.py:CELL_MAX",
        "ddflow/infra/store/_fingerprint.py:INDEX_TOUCH_S",
        "ddflow/services/enforce/hooks.py:MAX_LISTED_PATHS",
    ):
        assert key in reg.knob, f"moved constant not re-pinned at its new home: {key}"
    for key in (
        "ddflow/config_sections/approvals.py:STANDING_WINDOW_MAX_S",
        "ddflow/services/inbox.py:READ_CAP",
        "ddflow/services/review_keys.py:MAX_KEY_FILE_BYTES",
    ):
        assert reg.exempt.get(key), f"new constant not classified (or unreasoned): {key}"


def test_detector_folds_no_stale_value_from_a_rebound_name(tmp_path: Path) -> None:
    src = (
        "HOUR = 1\n"
        "HOUR = get_hours()\n"
        "A_WINDOW_S = 7 * 24 * HOUR\n"
        "UNIT = 2\n"
        "UNIT += 1\n"
        "B_LIMIT = UNIT\n"
    )
    assert _plant(tmp_path, src).unclassified == []


def test_detector_folds_no_stale_value_through_unpacking(tmp_path: Path) -> None:
    src = (
        "HOUR = 3600\n"
        "HOUR, MIN = get_pair()\n"
        "A_WINDOW_S = 7 * 24 * HOUR\n"
        "for HOUR in [1]:\n"
        "    pass\n"
        "B_WINDOW_S = 7 * 24 * HOUR\n"
    )
    assert _plant(tmp_path, src).unclassified == []


def test_a_function_body_shadowing_a_name_does_not_hide_the_module_constant(tmp_path: Path) -> None:
    src = "HOUR = 3600.0\ndef _helper():\n    HOUR = 1.0\nA_WINDOW_S = 7 * 24 * HOUR\n"
    assert [(c.name, c.value) for c in _plant(tmp_path, src).unclassified] == [
        ("A_WINDOW_S", 604800.0)
    ]


def test_an_except_handler_rebinding_a_name_invalidates_it(tmp_path: Path) -> None:
    src = (
        "HOUR = 3600\n"
        "try:\n"
        "    1 / 0\n"
        "except ZeroDivisionError:\n"
        "    HOUR = 1\n"
        "A_WINDOW_S = 7 * 24 * HOUR\n"
    )
    assert _plant(tmp_path, src).unclassified == []


def test_detector_ignores_what_is_not_a_tunable(tmp_path: Path) -> None:
    src = (
        "NAME = 'x'\nENABLED = True\nRETRY_LIMIT = True\nlower_limit = 3\nPLAIN = 7\n"
        "MAX_X = compute()\nLIMITS = (1, 2)\n"
        "def f():\n    LOCAL_LIMIT = 9\nclass C:\n    CLASS_LIMIT = 9\n"
    )
    assert _plant(tmp_path, src).unclassified == []


def test_a_planted_constant_is_cleared_by_either_table(tmp_path: Path) -> None:
    key = "ddflow/m.py:X_LIMIT"
    a = _plant(tmp_path, "X_LIMIT = 1\n", f'[knob]\n"{key}" = "x.limit"\n')
    assert a.unclassified == [] and [c.key for c in a.knob] == [key]
    a = _plant(tmp_path, "X_LIMIT = 1\n", f'[exempt]\n"{key}" = "protocol"\n')
    assert a.unclassified == [] and [c.key for c in a.exempt] == [key]


def test_stale_double_and_reasonless_entries_are_caught(tmp_path: Path) -> None:
    reg = '[knob]\n"ddflow/m.py:A_LIMIT" = "a.l"\n"gone.py:B_LIMIT" = "b.l"\n[exempt]\n"ddflow/m.py:A_LIMIT" = ""\n'
    a = _plant(tmp_path, "A_LIMIT = 1\n", reg)
    assert a.stale == ["gone.py:B_LIMIT"]
    assert a.both == ["ddflow/m.py:A_LIMIT"]
    assert a.unreasoned == ["ddflow/m.py:A_LIMIT"]
    assert ca.problems(a)


def test_the_script_exits_clean_on_this_tree() -> None:
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "ci" / "constants-audit")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stdout[-500:] + r.stderr[-500:]
    assert "0 unclassified" in r.stdout


@pytest.mark.parametrize(
    "path,rank", [("ddflow/api/x.py", 1), ("ddflow/services/x.py", 2), ("ddflow/core/x.py", 3)]
)
def test_visibility_ranks_user_facing_first(path: str, rank: int) -> None:
    assert ca.visibility(path) == rank
