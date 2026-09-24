#!/usr/bin/env python3
"""Run every Orchard demo scenario against freshly invented projects.

python3 demos/run_all.py             # all scenarios
python3 demos/run_all.py crash       # substring-matched subset
"""

from __future__ import annotations

import pathlib
import shutil
import sys
import time
import traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

import scenario_crash_recovery  # noqa: E402
import scenario_full_lifecycle  # noqa: E402
import scenario_mcp_orchestration  # noqa: E402
import scenario_mcp_polyglot  # noqa: E402
import scenario_parallel_phase  # noqa: E402
import scenario_reconstruct  # noqa: E402
from harness import Fail, Scenario, summarise  # noqa: E402

SCENARIOS = [
    ("parallel-phase", scenario_parallel_phase.run),
    ("crash-recovery", scenario_crash_recovery.run),
    ("reconstruct-from-log", scenario_reconstruct.run),
    ("mcp-polyglot", scenario_mcp_polyglot.run),
    ("mcp-orchestration", scenario_mcp_orchestration.run),
    ("full-lifecycle", scenario_full_lifecycle.run),
]


def main(argv: list[str]) -> int:
    wanted = [s for s in SCENARIOS if not argv or any(a in s[0] for a in argv)]
    if not wanted:
        print(f"no scenario matches {argv}; known: {[s[0] for s in SCENARIOS]}")
        return 2
    base = pathlib.Path("/tmp/orchard-demos")
    if base.exists():
        shutil.rmtree(base)
    results = []
    for name, fn in wanted:
        sc = Scenario(name, base / name)
        t0 = time.time()
        try:
            fn(sc)
            ok = True
        except Fail as exc:
            ok = False
            print(f"\n\033[31mSCENARIO FAILED\033[0m: {exc}")
        except Exception:
            ok = False
            traceback.print_exc()
        results.append((name, ok, sc.steps, sc.checks, time.time() - t0))
    return summarise(results)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
