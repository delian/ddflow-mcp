"""The perf report surfaces (PM-report-surfaces): one declaration, CLI + MCP action + prompt.

The falsifier is a `perf report` whose CLI and API bodies disagree, a bad format or window
that silently passes, or an svg/html written nowhere. These run the real CLI in a repo and
the shared `api`, so the two surfaces are compared, not re-implemented.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli
from lanes_fixture import T0, Log
from metrics_fixture import finished, frozen

from ddflow import api
from ddflow.api.perfreport import _charts
from ddflow.services.metrics.facts import collect
from ddflow.services.metrics.providers import Context
from ddflow.services.perf.report import build


def _report_with_gate():
    log = Log()
    finished(log, "A", T0, 2)
    log.add("gate.started", "A", "a1", T0 + 60, gate="ci")
    log.add("gate.passed", "A", "a1", T0 + 360, gate="ci", by="a1", evidence={"elapsed_s": 300})
    state, cfg, now = frozen(log)
    facts = collect(list(log.events), state, now=now, cfg=cfg)
    return build(Context(facts, cfg, now, phase="", since=None), minimum=1)


def _seed(repo):
    """One claimed item with a passed ci gate, so the report has data to fold."""
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "P")
    run_cli(repo, "task", "add", "T1", "--title", "T", "--phase", "P1", "--globs", "a.py")
    run_cli(repo, "claim", "T1")
    run_cli(repo, "gate", "record", "T1", "ci", "--outcome", "passed", "--evidence", "elapsed_s=10")


def test_report_json_is_machine_readable_and_names_not_collected(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "perf", "report", "--format", "json")
    assert code == 0, err
    data = json.loads(out)
    assert set(data) >= {"gates", "cycle", "waits", "rework", "comparisons", "not_collected"}
    assert "complexity" in data["not_collected"]  # named, never rendered as an empty table


def test_report_text_names_not_collected_and_is_bounded(repo):
    _seed(repo)
    code, out, err = run_cli(repo, "perf", "report", "--format", "text")
    assert code == 0, err
    assert "not collected" in out
    assert max(len(line) for line in out.splitlines()) <= 76  # the report_width knob


def test_a_bad_window_is_refused_not_passed(repo):
    run_cli(repo, "init")
    code, _out, err = run_cli(repo, "perf", "report", "--window", "garbage")
    assert code == 3
    assert "garbage" in err


def test_an_unknown_report_filter_is_refused(repo):
    run_cli(repo, "init")
    code, _out, err = run_cli(repo, "perf", "report", "--filters", "tag=x")
    assert code == 3
    assert "tag=x" in err


def test_report_svg_writes_a_file_and_names_the_path(repo, tmp_path):
    _seed(repo)
    out_file = tmp_path / "report.svg"
    code, out, err = run_cli(repo, "perf", "report", "--format", "svg", "--out", str(out_file))
    assert code == 0, err
    assert out_file.exists()
    assert str(out_file) in out  # the answer names the path and the exact next calls


def test_report_html_is_one_self_contained_page(repo, tmp_path):
    _seed(repo)
    out_file = tmp_path / "report.html"
    code, _out, err = run_cli(repo, "perf", "report", "--format", "html", "--out", str(out_file))
    assert code == 0, err
    assert out_file.read_text().startswith("<!doctype html>")


def test_charts_assemble_a_gate_p90_bar_when_a_duration_exists():
    charts = _charts(_report_with_gate())
    assert "gate_p90" in charts
    assert charts["gate_p90"].startswith("<svg")


def test_the_report_action_answers_the_same_over_cli_and_api(repo):
    run_cli(repo, "init")
    code, cli_out, err = run_cli(repo, "--json", "perf", "report", "--format", "json")
    assert code == 0, err
    cli_norm = json.loads(json.loads(cli_out)["text"])
    api_norm = api.run_perf_report(repo, format="json").data["report"]
    assert cli_norm == api_norm


def test_the_json_report_is_deterministic_for_the_same_log(repo):
    run_cli(repo, "init")
    _c1, a, _e1 = run_cli(repo, "perf", "report", "--format", "json")
    _c2, b, _e2 = run_cli(repo, "perf", "report", "--format", "json")
    assert a == b


def test_report_honours_a_phase_filter(repo):
    _seed(repo)  # P1 with T1, whose ci gate passed
    run_cli(repo, "phase", "add", "P2", "--title", "Q")
    code, out, err = run_cli(repo, "perf", "report", "--format", "json", "--filters", "phase=P1")
    assert code == 0, err
    assert json.loads(out)["gates"], "the known phase's own gate is inside the report"
    run_cli(repo, "task", "add", "T2", "--title", "U", "--phase", "P2", "--globs", "b.py")
    code, out, _err = run_cli(repo, "perf", "report", "--format", "json", "--filters", "phase=P2")
    assert code == 0, _err
    assert json.loads(out)["gates"] == []  # P2 has no outcomes: the filter scopes, not accepts
    code, _out, err = run_cli(repo, "perf", "report", "--format", "json", "--filters", "phase=NOPE")
    assert code == 3
    assert "NOPE" in err
