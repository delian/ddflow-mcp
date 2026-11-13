"""The taskmetrics scaffold, the code the scenario's agents write, and the commit helper
(split from scenario_mcp_orchestration.py, r-small-files)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from harness import Fail, Scenario

SCAFFOLD = {
    ".gitignore": "__pycache__/\n*.pyc\n.pytest_cache/\n.ddflow-worktrees/\n",
    "README.md": "# taskmetrics\n\nDuration parsing and task statistics.\n",
    "taskmetrics/__init__.py": "",
    "tests/__init__.py": "",
}

# --- the code the "agents" write, kept here so the scenario reads as a narrative ------

PARSE_PY = '''
"""Parse human duration strings into seconds."""

import re

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_TOKEN = re.compile(r"(\\d+(?:\\.\\d+)?)\\s*([smhd])", re.I)


def parse_duration(text):
    """'1h30m' -> 5400.0. Raises ValueError on anything it cannot parse."""
    if not isinstance(text, str):
        raise TypeError("duration must be a string")
    stripped = text.strip().lower()
    if not stripped:
        raise ValueError("empty duration")
    total, consumed = 0.0, 0
    for m in _TOKEN.finditer(stripped):
        total += float(m.group(1)) * _UNITS[m.group(2)]
        consumed += len(m.group(0))
    if consumed != len(stripped.replace(" ", "")):
        raise ValueError(f"cannot parse duration: {text!r}")
    return total
'''

PARSE_TEST = """
import pytest

from taskmetrics.parse import parse_duration


@pytest.mark.parametrize("text,want", [
    ("30s", 30.0), ("5m", 300.0), ("1h", 3600.0), ("2d", 172800.0),
    ("1h30m", 5400.0), ("1h 30m 15s", 5415.0), ("1.5h", 5400.0),
])
def test_parses_known_forms(text, want):
    assert parse_duration(text) == want


@pytest.mark.parametrize("bad", ["", "  ", "1x", "abc", "1h!", "h"])
def test_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_duration(bad)


def test_rejects_non_string():
    with pytest.raises(TypeError):
        parse_duration(90)
"""

STATS_PY = '''
"""Summary statistics over a sequence of durations."""

import math


def summarise(values):
    """Return count/total/mean/median/p95 for a sequence of numbers.

    An EMPTY sequence returns zeros rather than raising: a report over a day with no
    tasks is a legitimate report, and making the caller special-case it pushes the same
    branch into every call site.
    """
    nums = [float(v) for v in values]
    if not nums:
        return {"count": 0, "total": 0.0, "mean": 0.0, "median": 0.0, "p95": 0.0}
    ordered = sorted(nums)
    n = len(ordered)
    mid = n // 2
    median = ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    # Nearest-rank p95, clamped: for n < 20 the rank rounds past the end.
    rank = max(0, min(n - 1, math.ceil(0.95 * n) - 1))
    return {"count": n, "total": sum(ordered), "mean": sum(ordered) / n,
            "median": median, "p95": ordered[rank]}
'''

STATS_TEST = """
from taskmetrics.stats import summarise


def test_empty_is_zeros_not_an_error():
    assert summarise([])["count"] == 0
    assert summarise([])["mean"] == 0.0


def test_single_value():
    s = summarise([10])
    assert s["count"] == 1 and s["mean"] == 10.0 and s["p95"] == 10.0


def test_odd_and_even_medians():
    assert summarise([1, 2, 3])["median"] == 2
    assert summarise([1, 2, 3, 4])["median"] == 2.5


def test_p95_never_runs_off_the_end():
    for n in range(1, 25):
        s = summarise(list(range(n)))
        assert s["p95"] <= n - 1


def test_total_and_mean():
    s = summarise([2, 4, 6])
    assert s["total"] == 12 and s["mean"] == 4
"""

CLI_PY = '''
"""Command line: read durations, print a summary."""

import argparse
import json
import sys

from taskmetrics.parse import parse_duration
from taskmetrics.stats import summarise


def main(argv=None):
    ap = argparse.ArgumentParser(prog="taskmetrics")
    ap.add_argument("durations", nargs="*", help="e.g. 1h30m 45m 2h")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        seconds = [parse_duration(d) for d in args.durations]
    except (ValueError, TypeError) as exc:
        print(f"taskmetrics: {exc}", file=sys.stderr)
        return 2
    stats = summarise(seconds)
    if args.json:
        print(json.dumps(stats, indent=2))
    else:
        print(f"count  {stats['count']}")
        print(f"total  {stats['total']:.0f}s")
        print(f"mean   {stats['mean']:.0f}s")
        print(f"median {stats['median']:.0f}s")
        print(f"p95    {stats['p95']:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

CLI_TEST = """
from taskmetrics.cli import main


def test_runs_and_reports(capsys):
    assert main(["1h", "30m"]) == 0
    out = capsys.readouterr().out
    assert "count  2" in out and "total  5400s" in out


def test_json_output(capsys):
    import json
    assert main(["1h", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 1


def test_bad_input_exits_two(capsys):
    assert main(["not-a-duration"]) == 2
    assert "cannot parse" in capsys.readouterr().err


def test_no_arguments_is_an_empty_summary(capsys):
    assert main([]) == 0
    assert "count  0" in capsys.readouterr().out
"""


def _commit_in(sc: Scenario, worktree: Path, message: str, agent: str = "") -> None:
    """Commit inside a worktree, exactly as an agent would.

    No DDFLOW_AGENT is set unless the caller asks for one: the enforcement hook must
    recognise the lease that created THIS worktree without being told who we are.
    """
    import os as _os

    env = {**_os.environ}
    env.pop("DDFLOW_AGENT", None)
    if agent:
        env["DDFLOW_AGENT"] = agent
    subprocess.run(["git", "-C", str(worktree), "add", "-A"], check=True, timeout=120)
    r = subprocess.run(
        ["git", "-C", str(worktree), "commit", "-qm", message],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    if r.returncode != 0:
        raise Fail(f"commit in {worktree} refused:\n{r.stderr[:600]}")
