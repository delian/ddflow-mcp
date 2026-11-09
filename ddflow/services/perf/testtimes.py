"""Per-test and per-file durations (PM-test-durations), with the growth trend's inputs.

Parses the runner's junit XML (pytest first, behind the toolrun adapter so other stacks plug
the same port) into per-test durations; folds the raw durations into the perf store's local
ring; and puts into the gate evidence ONLY the rollup -- test count, the top N slowest ids,
per-file totals, the skew (the slowest file's share of the critical-path floor) and the
serial-equivalent seconds. The critical-path floor says a wall run cannot beat the slowest
test (nor the slowest file under a file-level distribution). A probe that could not run is
unavailable, never zero. No test argument, output or path beyond the test/file id.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from xml.etree import ElementTree


@dataclass(frozen=True)
class TestCase:
    test_id: str  # the test's dotted id, never its body or output
    file: str
    duration_s: float


def parse_junit(xml_text: str) -> list[TestCase]:
    """junit XML -> one TestCase per ``<testcase>`` (a missing ``time`` is a measured 0)."""
    root = ElementTree.fromstring(xml_text)
    out: list[TestCase] = []
    for case in root.iter("testcase"):
        name = case.get("name", "")
        classname = case.get("classname", "")
        try:
            duration = float(case.get("time", 0.0))
        except ValueError:
            duration = 0.0
        out.append(
            TestCase(
                test_id=f"{classname}.{name}",
                file=classname.replace(".", "/") + ".py",
                duration_s=duration,
            )
        )
    return out


def rollup(cases: list[TestCase], workers: int, top_n: int = 10) -> dict:
    """The small rollup that rides the evidence: count, top slowest, per-file totals, skew,
    serial-equivalent seconds and the critical-path floor (what a wall run cannot beat)."""
    per_file: dict[str, float] = defaultdict(float)
    for c in cases:
        per_file[c.file] += c.duration_s
    serial = sum(c.duration_s for c in cases)
    slowest_test = max((c.duration_s for c in cases), default=0.0)
    slowest_file = max(per_file.values(), default=0.0) if per_file else 0.0
    # A file cannot parallelize within itself: under file-level distribution the floor is the
    # slowest file; under no distribution it is the slowest test. Take the larger.
    floor = max(slowest_test, slowest_file) if workers <= 1 else max(slowest_test, 0.0)
    return {
        "count": len(cases),
        "top_slowest": [
            {"test_id": c.test_id, "file": c.file, "duration_s": c.duration_s}
            for c in sorted(cases, key=lambda c: -c.duration_s)[:top_n]
        ],
        "per_file": dict(sorted(per_file.items(), key=lambda kv: -kv[1])[:top_n]),
        "skew": slowest_file / floor if floor else 0.0,
        "serial_seconds": serial,
        "critical_path_floor_s": floor,
        "workers": workers,
    }


def fold(cases: list[TestCase], repo) -> None:
    """Append one ring sample per test (the raw durations; only the rollup reaches evidence)."""
    import time

    from ddflow.services.perf import store

    for c in cases:
        store.append(
            repo,
            "test_durations",
            store.Sample(time.time(), c.file, c.duration_s, "unit_tests", c.test_id),
        )


__all__ = ["TestCase", "fold", "parse_junit", "rollup"]
