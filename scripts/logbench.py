#!/usr/bin/env python3
"""Time the read paths of a ddflow event log: `logbench.py [--events DIR | --synth N] ...`.

Copies (or generates) the log into a scratch project and times each command in a fresh
process, printing one JSON object per line so a run can be diffed against another.
Used by B-gap-log-scale; `tests/test_log_scale.py` pins the same fixtures.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

COMMANDS = (["status"], ["next"], ["brief"], ["heartbeat", "B-synth-1"], ["hooks", "session-start"])


def project(events: Path | None, synth: int, tmp: Path) -> Path:
    from ddflow.services import logfixture

    (tmp / ".ddflow").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    if events is not None:
        shutil.copytree(events, tmp / ".ddflow" / "events")
    else:
        logfixture.generate(tmp / ".ddflow" / "events", synth)
    return tmp


def timed(cmd: list[str], cwd: Path) -> float:
    argv = [sys.executable, "-m", "ddflow", "--agent", "bench", *cmd]
    env = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin", "HOME": str(cwd)}
    t0 = time.perf_counter()
    subprocess.run(argv, cwd=cwd, env=env, capture_output=True, check=False)
    return time.perf_counter() - t0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--events", type=Path, help="copy this events directory")
    ap.add_argument("--synth", type=int, default=0, help="generate this many synthetic events")
    ap.add_argument("--repeat", type=int, default=2)
    a = ap.parse_args()
    if not a.events and not a.synth:
        ap.error("give --events or --synth")
    with tempfile.TemporaryDirectory() as d:
        proj = project(a.events, a.synth, Path(d) / "p")
        size = sum(f.stat().st_size for f in (proj / ".ddflow" / "events").glob("*.jsonl"))
        print(json.dumps({"bytes": size}))
        for cmd in COMMANDS:
            best = min(timed(cmd, proj) for _ in range(a.repeat))
            print(json.dumps({"command": " ".join(cmd), "best_s": round(best, 3)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
