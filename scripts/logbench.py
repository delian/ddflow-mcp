#!/usr/bin/env python3
"""Time the read paths of a ddflow event log: `logbench.py [--events DIR | --synth N] ...`.

Copies (or generates) the log into a scratch project and times each command in a fresh
process, printing one JSON object per line so a run can be diffed against another.
Used by B-gap-log-scale; `tests/test_log_scale.py` pins the same fixtures.
"""

from __future__ import annotations

import argparse
import json
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

COMMANDS = (["status"], ["next"], ["brief"], ["heartbeat", "B-synth-1"], ["hooks", "session-start"])


def project(events: Path | None, synth: int, tmp: Path, modern: bool = False) -> Path:
    from ddflow.services import logfixture

    (tmp / ".ddflow").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    if events is not None:
        shutil.copytree(events, tmp / ".ddflow" / "events")
    else:
        logfixture.generate(
            tmp / ".ddflow" / "events", synth, blobs=modern, coalesce_s=300 if modern else 0
        )
    return tmp


def timed(cmd: list[str], cwd: Path) -> tuple[float, float]:
    """(wall seconds, CPU seconds) of one run in a fresh process. CPU is the number to compare
    on a busy machine: wall time includes waiting for other people's work."""
    argv = [sys.executable, "-m", "ddflow", "--agent", "bench", *cmd]
    env = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin", "HOME": str(cwd)}
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    t0 = time.perf_counter()
    subprocess.run(argv, cwd=cwd, env=env, capture_output=True, check=False)
    wall = time.perf_counter() - t0
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    return wall, (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--events", type=Path, help="copy this events directory")
    ap.add_argument("--synth", type=int, default=0, help="generate this many synthetic events")
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--modern", action="store_true", help="synthetic log as a current ddflow writes it")
    a = ap.parse_args()
    if not a.events and not a.synth:
        ap.error("give --events or --synth")
    with tempfile.TemporaryDirectory() as d:
        proj = project(a.events, a.synth, Path(d) / "p", a.modern)
        size = sum(f.stat().st_size for f in (proj / ".ddflow" / "events").glob("*.jsonl"))
        print(json.dumps({"bytes": size, "events": sum(1 for f in (proj / ".ddflow" / "events").glob("*.jsonl")
                                                         for _ in f.open())}))
        for cmd in COMMANDS:
            runs = [timed(cmd, proj) for _ in range(a.repeat)]
            print(json.dumps({"command": " ".join(cmd), "wall_s": round(min(r[0] for r in runs), 3),
                              "cpu_s": round(min(r[1] for r in runs), 3)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
