"""The defensive self-assessment pass (dx-autofile-defects): default-on, files each defect
an assessment text names and honours `[self_assessment].auto_file = false`.

Rule `r-ddflow-self-assessment` was advisory; before this pass nothing in the default
pipeline scanned an assessment and filed its named defects. `extract_defects` is the pure
reader (this file), `auto_file` is the filing pass (`ddflow.api.knowledge.self_assessment`).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import state as _state

from ddflow.services.self_assessment import Defect, extract_defects, report_line


def test_extract_defects_names_each_list_item_and_ignores_none():
    text = "\n".join(
        [
            "- the `ddflow waves` verb refused a flag I guessed",
            "* nothing lists standing approvals",
            "none",
            "",
        ]
    )
    defects = extract_defects(text)
    assert [d.title for d in defects] == [
        "the `ddflow waves` verb refused a flag I guessed",
        "nothing lists standing approvals",
    ]
    assert all(isinstance(d, Defect) for d in defects)
    # An assessment that found nothing names no defect, and neither does a blank one.
    assert extract_defects("none found") == []
    assert extract_defects("No defects") == []
    assert extract_defects(None) == []


def test_report_line_names_filed_and_skipped_ids():
    assert report_line("- a defect", filed=["B1"], skipped=["B2"]) == (
        "filed: B1, skipped (already tracked): B2"
    )
    assert report_line("none found", filed=[], skipped=[]) == "none found"


def test_auto_file_files_each_named_defect_as_a_ddflow_scoped_bug(repo):
    run_cli(repo, "init")
    from ddflow.api.knowledge.self_assessment import auto_file

    out = auto_file(
        repo,
        text="- `ddflow show` refused a valid id\n- nothing lists standing approvals",
        trigger="assessment",
        agent="tester",
        no_task=True,
    )
    assert out.ok, out.reason

    bugs = _state(repo).bugs
    assert len(bugs) == 2, sorted(bugs)
    assert all(bug.scope == "ddflow" for bug in bugs.values())


def test_auto_file_files_nothing_when_the_knob_is_off(repo):
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text("[self_assessment]\nauto_file = false\n")
    from ddflow.api.knowledge.self_assessment import auto_file

    out = auto_file(repo, text="- something misbehaved", trigger="assessment", agent="tester")
    assert out.ok, out.reason
    assert out.data["how"] == "off"
    assert _state(repo).bugs == {}
