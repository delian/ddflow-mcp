"""Bug Bb049f0ae06: a triage probe that names a test must name one that exists."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_review_triage import OK, _full_review, _setup, _triage

from ddflow.infra.log import EventLog


def _triaged(repo) -> int:
    return len([e for e in EventLog(repo).read_all() if e.kind == "review.triaged"])


def test_a_confirmed_probe_naming_a_missing_test_node_is_refused(repo, tmp_path):
    _setup(repo, tmp_path)
    _full_review(repo)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_real.py").write_text(
        "class TestA:\n    def test_real(self):\n        pass\n\nclass TestB:\n    pass\n"
    )
    for probe in (
        "tests/test_real.py::test_no_such_test_exists",
        "tests/test_nowhere_at_all.py::test_x",
        "tests/test_real.py::TestB::test_real",
        "test_no_such_test_exists passes",
    ):
        out = _triage(repo, 1, "confirmed", probe)
        assert out.exit != OK, probe
        out = _triage(repo, 1, "refuted", probe)
        assert out.exit != OK and "probe names" in out.reason + out.data.get("text", ""), (
            probe,
            out,
        )
    assert _triaged(repo) == 0


def test_a_probe_naming_an_existing_test_or_plain_command_is_accepted(repo, tmp_path):
    _setup(repo, tmp_path)
    _full_review(repo)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_real.py").write_text(
        "class TestA:\n    def test_real(self):\n        pass\n"
    )
    (repo / "tests" / "test_other_module.py").write_text(
        "from base import Base\n\ndef test_fn():\n    pass\n\nclass TestKid(Base):\n    pass\n"
    )
    for probe in (
        "pytest tests/test_other_module.py::test_fn",
        "tests/test_real.py::TestA::test_real",
        "tests/test_other_module.py::TestKid::test_from_base",
        "pytest -k test_other_module",
        "pytest tests/test_real.py ok: test_real",
    ):
        assert _triage(repo, 1, "confirmed", probe).exit == OK, probe
    assert _triage(repo, 2, "refuted", "ran it: nothing there").exit == OK
