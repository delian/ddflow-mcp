"""The pull-request description is rendered from a layered template (BL-pr-evidence-template).

`[flow].pr_body_template` (a path; empty = the project's `.ddflow/prompts/pr_body.md`, else the
shipped default) renders the request body from DERIVED records only: the item, every gate with
its outcome, review rounds and triage, the bugs it fixed with their regression tests, and the
changelog entry. Outward text is secret-redacted (r-consent-outward).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import flow as FS

SECRET = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


@pytest.fixture
def item(repo):
    run_cli(repo, "init")
    log = EventLog(repo, "tester")
    log.append(
        "task.added",
        "T1",
        {"title": "Do the thing", "kind": "task", "tags": [], "body": "Body text."},
    )
    log.append("gate.passed", "T1", {"gate": "research", "reason": "ran probe", "evidence": {}})
    log.append(
        "gate.failed",
        "T1",
        {"gate": "critic", "reason": f"found {SECRET} in a log", "evidence": {}},
    )
    log.append("gate.unavailable", "T1", {"gate": "ci", "reason": "no runner", "evidence": {}})
    st = fold(log.read_all(), strict=False)
    return repo, st, st.items["T1"]


def _body(repo, st, it, **kw):
    cfg = Config.load(repo)
    return FS.pr_body(st, it, on=None, cfg=cfg, repo=repo, **kw)


def test_the_body_lists_every_gate_with_its_outcome(item):
    repo, st, it = item
    text = _body(repo, st, it)
    for gate, outcome in (("research", "passed"), ("critic", "failed"), ("ci", "unavailable")):
        assert f"| {gate} | {outcome}" in text, text
    assert "Item: T1" in text and "Body text." in text


def test_the_body_never_prints_a_secret(item):
    repo, st, it = item
    assert SECRET not in _body(repo, st, it)


def test_a_project_template_overrides_the_shipped_one(item):
    repo, st, it = item
    d = repo / ".ddflow" / "prompts"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pr_body.md").write_text(
        "PROJECT {{ item }}: {% for g in gates %}{{ g.gate }}={{ g.outcome }};{% endfor %}"
    )
    assert _body(repo, st, it).startswith("PROJECT T1: ")


def test_the_config_path_beats_the_project_template(item):
    repo, st, it = item
    custom = repo / "custom_pr.md"
    custom.write_text("CONFIG {{ title }}")
    project = repo / ".ddflow" / "prompts"
    project.mkdir(parents=True, exist_ok=True)
    (project / "pr_body.md").write_text("PROJECT {{ title }}")
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write(f"\n[flow]\npr_body_template = {json.dumps(str(custom))}\n")
    assert _body(repo, st, it) == "CONFIG Do the thing"


def test_a_missing_configured_template_is_an_error_not_a_silent_default(item):
    repo, st, it = item
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write('\n[flow]\npr_body_template = "/nonexistent/pr.md"\n')
    with pytest.raises(Exception, match="does not exist"):
        _body(repo, st, it)


def test_a_stacked_request_names_what_it_is_stacked_on(item):
    repo, st, it = item

    class _On:
        id, pr = "T0", type("P", (), {"url": "https://example.test/pr/1"})()

    cfg = Config.load(repo)
    text = FS.pr_body(st, it, on=_On, cfg=cfg, repo=repo)
    assert "Stacked on T0" in text and "https://example.test/pr/1" in text


def test_a_pipe_or_newline_in_a_gate_cell_does_not_split_its_row(item):
    repo, st, it = item
    log = EventLog(repo, "tester")
    log.append(
        "gate.failed",
        "T1",
        {"gate": "standards", "reason": "bad | see log\nnext line", "evidence": {}},
    )
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write('\n[gates]\ntask_pipeline = ["lint|bad", "standards"]\n')
    st = fold(log.read_all(), strict=False)
    text = _body(repo, st, st.items["T1"])
    rows = [ln for ln in text.splitlines() if ln.startswith("| ") and not ln.startswith("| gate")]
    assert rows, text
    standards = next(r for r in rows if r.startswith("| standards"))
    assert "bad \\| see log next line" in standards, standards  # folded, escaped, nothing dropped
    for row in rows:  # every row has exactly the three cells: 4 unescaped bars
        assert row.replace("\\\\", "").replace("\\|", "").count("|") == 4, row
    assert any(r.startswith("| lint\\|bad | not run") for r in rows), rows


def test_a_bug_without_a_recorded_regression_test_is_said_so(item):
    repo, st, it = item
    log = EventLog(repo, "tester")
    log.append("bug.found", "B1", {"summary": "race", "item": "T1"})
    st = fold(log.read_all(), strict=False)
    text = _body(repo, st, st.items["T1"])
    assert "B1: race -- NO regression test recorded" in text, text


def test_a_gate_that_has_not_run_is_listed_as_not_run(item):
    repo, st, it = item
    text = _body(repo, st, it)
    assert "| merge | not run" in text, text
    assert "| research | passed" in text


def test_pr_body_without_a_config_reads_the_project_layers(item):
    repo, st, it = item
    custom = repo / "c.md"
    custom.write_text("LAYERED {{ item }}")
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write(f"\n[flow]\npr_body_template = {json.dumps(str(custom))}\n")
    assert FS.pr_body(st, it, on=None, repo=repo) == "LAYERED T1"


def test_a_backslash_before_a_pipe_cannot_unescape_the_delimiter(item):
    repo, st, it = item
    log = EventLog(repo, "tester")
    log.append("gate.failed", "T1", {"gate": "standards", "reason": "x \\| y", "evidence": {}})
    st = fold(log.read_all(), strict=False)
    row = next(
        r for r in _body(repo, st, st.items["T1"]).splitlines() if r.startswith("| standards")
    )
    assert "x \\\\\\| y" in row, row  # backslash doubled, then the pipe escaped


def test_a_cell_is_bounded_after_escaping_by_the_knob(item):
    repo, st, it = item
    log = EventLog(repo, "tester")
    log.append("gate.failed", "T1", {"gate": "standards", "reason": "|" * 500, "evidence": {}})
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write("\n[flow]\npr_body_cell_max = 40\n")
    st = fold(log.read_all(), strict=False)
    row = next(
        r for r in _body(repo, st, st.items["T1"]).splitlines() if r.startswith("| standards")
    )
    note = row.split(" | ")[-1].rstrip(" |")
    assert len(note) <= 40 and note.endswith("…"), (len(note), row)


@pytest.mark.parametrize("cell_max", [1, 2, 5])
def test_the_smallest_bounds_are_still_bounds(cell_max):
    from ddflow.services.flow.pr_template import _cell

    assert len(_cell("hello world", cell_max)) == cell_max
    assert _cell("hi", 5) == "hi"
