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


def _file_of(classname: str) -> str:
    """junit ``classname`` -> file path. A class test's classname is ``module.Class``; a trailing
    CamelCase segment is a class, so it is dropped (``tests.test_a.TestSuite`` -> ``tests/test_a.py``)."""
    if not classname:
        return ""
    parts = classname.split(".")
    while parts and parts[-1] and parts[-1][0].isupper():
        parts = parts[:-1]
    return "/".join(parts) + ".py"


@dataclass(frozen=True)
class TestCase:
    test_id: str  # the test's dotted id, never its body or output
    file: str
    duration_s: float | None  # None: the junit carried no (or a non-numeric) time


def parse_junit(xml_text: str) -> list[TestCase]:
    """junit XML -> one TestCase per ``<testcase>``. A missing or non-numeric ``time`` is a
    None duration (the case is not a duration sample; it is never a measured 0)."""
    root = ElementTree.fromstring(xml_text)
    out: list[TestCase] = []
    for case in root.iter("testcase"):
        name = case.get("name", "")
        classname = case.get("classname", "")
        file = case.get("file") or _file_of(classname)
        try:
            duration = float(case.get("time", ""))
        except ValueError:
            duration = None
        out.append(TestCase(test_id=f"{classname}.{name}", file=file, duration_s=duration))
    return out


def rollup(cases: list[TestCase], workers: int, top_n: int = 10) -> dict:
    """The small rollup that rides the evidence: count, top slowest, per-file totals, skew,
    serial-equivalent seconds and the critical-path floor (what a wall run cannot beat). A
    case with no duration is counted (``count``) but contributes no time (it is not a 0)."""
    timed = [c for c in cases if c.duration_s is not None]
    per_file: dict[str, float] = defaultdict(float)
    for c in timed:
        per_file[c.file] += c.duration_s
    serial = sum(c.duration_s for c in timed)
    slowest_test = max((c.duration_s for c in timed), default=0.0)
    slowest_file = max(per_file.values(), default=0.0) if per_file else 0.0
    floor = max(slowest_test, slowest_file)
    # the file-level makespan at ``workers`` workers: a file does not split, and the workers
    # divide the rest, so the critical path is max(slowest_file, serial/workers).
    makespan = max(slowest_file, serial / workers if workers else serial)
    return {
        "count": len(cases),
        "with_duration": len(timed),
        "top_slowest": [
            {"test_id": c.test_id, "file": c.file, "duration_s": c.duration_s}
            for c in sorted(timed, key=lambda c: -c.duration_s)[:top_n]
        ],
        "per_file": dict(sorted(per_file.items(), key=lambda kv: -kv[1])[:top_n]),
        #: the slowest file's share of the CRITICAL PATH at the workers used
        "skew": slowest_file / makespan if makespan else 0.0,
        "serial_seconds": serial,
        "critical_path_floor_s": floor,
        "workers": workers,
    }


def fold(cases: list[TestCase], repo, gate: str) -> None:
    """Append one ring sample per timed test (the raw durations) to the ``gate`` series; only
    the rollup reaches evidence. A case with no duration is not a sample."""
    import time

    from ddflow.services.perf import store

    for c in cases:
        if c.duration_s is None:
            continue
        store.append(
            repo,
            "test_durations",
            store.Sample(time.time(), c.file, c.duration_s, gate, c.test_id),
        )


__all__ = ["TestCase", "fold", "parse_junit", "rollup"]
