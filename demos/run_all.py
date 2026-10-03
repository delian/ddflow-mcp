#!/usr/bin/env python3
"""Run every ddflow demo scenario against freshly invented projects.

python3 demos/run_all.py             # all scenarios
python3 demos/run_all.py crash       # substring-matched subset
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
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


def demo_base() -> pathlib.Path:
    """A directory for THIS run only (B184).

    This was a fixed /tmp/ddflow-demos that every run rmtree'd first, so two runs on one
    machine (two sessions, or a run beside CI) deleted each other's repositories
    mid-scenario: `no such item 'P1.T1'` after a successful `task add`. Reproduced by
    starting two `run_all.py parallel-phase` 20 s apart. `DDFLOW_DEMOS_DIR` pins a path
    when you want to inspect one (use a dedicated directory; two runs of the SAME
    scenario into one pinned directory still collide); otherwise each run gets a fresh
    mkdtemp.
    """
    pinned = os.environ.get("DDFLOW_DEMOS_DIR")
    if pinned:
        return pathlib.Path(pinned)
    return pathlib.Path(tempfile.mkdtemp(prefix="ddflow-demos-"))


def main(argv: list[str]) -> int:
    wanted = [s for s in SCENARIOS if not argv or any(a in s[0] for a in argv)]
    if not wanted:
        print(f"no scenario matches {argv}; known: {[s[0] for s in SCENARIOS]}")
        return 2
    base = demo_base()
    print(f"demo workspace: {base}")
    results = []
    for name, fn in wanted:
        # Only this scenario's own subdirectory is ever cleared: a pinned DDFLOW_DEMOS_DIR
        # may hold other things (and another run's scenarios), and is never wiped whole.
        shutil.rmtree(base / name, ignore_errors=True)
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
    rc = summarise(results)
    if rc == 0 and not os.environ.get("DDFLOW_DEMOS_DIR"):
        shutil.rmtree(base, ignore_errors=True)  # a green run leaves nothing behind
    else:
        print(f"demo workspace kept for inspection: {base}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
