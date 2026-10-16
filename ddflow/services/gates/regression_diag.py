"""Why a regression-test run on the fixed (or pre-fix) tree did not pass (bug B1a74291621).

`complete --regression-test` answered "does not pass on the fixed tree (failed)" and nothing
else: no exit code, no output, and no way to tell a test that really failed from one that
missed its own timeout on a machine at load average 200. The verdict, the exit code, the
last lines of output and the machine's load now go into both the refusal and the recorded
evidence, and a timeout is named as one -- it is never a verdict on the test.
"""

from __future__ import annotations

import os
from typing import Any

from ...config import Config
from .. import flakes as FL


def load_snapshot(cfg: Config) -> dict[str, Any]:
    """The machine's load (`flakes.load_average`: 1-minute, per CPU), the CPU count, and
    whether it is over `lessons.verify_load_ratio` per CPU. `{}` where there is none."""
    per_cpu = FL.load_average()
    if per_cpu is None:
        return {}
    cpus = os.cpu_count() or 1
    return {
        "load_1m": round(per_cpu * cpus, 1),
        "cpus": cpus,
        "under_load": per_cpu > cfg.lessons.verify_load_ratio,
    }


def _tail_lines(ev: dict[str, Any], n: int) -> list[str]:
    lines = [ln.rstrip() for ln in str(ev.get("tail") or "").splitlines() if ln.strip()]
    return lines[-n:] if n > 0 else []


def describe_run(
    outcome: str, ev: dict[str, Any], cfg: Config, *, where: str
) -> tuple[str, dict[str, Any]]:
    """(reason, evidence) for a run on ``where`` ("the fixed tree") that did not pass.

    ``failed``: the test's verdict -- exit code, the suite's own summary, the last
    `lessons.verify_output_lines` lines. ``unavailable``: it never produced a verdict (a
    timeout, a missing runner) and says so. Either way the load is stated, and a loaded
    machine is told to re-run before the result is read as one."""
    load = load_snapshot(cfg)
    lines = _tail_lines(ev, cfg.lessons.verify_output_lines)
    extra: dict[str, Any] = {
        k: ev[k] for k in ("exit", "elapsed_s", "summary") if ev.get(k) is not None
    }
    extra.update(load)
    if lines:
        extra["output_tail"] = lines
    why = str(ev.get("reason") or "")
    timed_out = bool(ev.get("timed_out"))
    if outcome == "unavailable":
        head = f"the named test produced no verdict on {where}: {why or 'it could not run'}"
        if timed_out:
            head += " (a timeout, not a test failure)"
    else:
        bits = [f"exit {ev['exit']}"] if ev.get("exit") is not None else []
        if ev.get("elapsed_s") is not None:
            bits.append(f"after {ev['elapsed_s']}s")
        head = f"the named test does not pass on {where} ({outcome}" + (
            f": {' '.join(bits)}" if bits else ""
        )
        head += ")"
        if ev.get("summary"):
            head += "; " + " / ".join(str(s) for s in ev["summary"])
        if why:
            head += f"; {why}"
    parts = [head]
    if lines:
        parts.append("last output: " + " | ".join(lines))
    if load.get("under_load") or timed_out:
        at = f" (load average {load['load_1m']} on {load['cpus']} CPUs)" if load else ""
        parts.append(
            f"the machine is under load{at}: a slow test can miss its own timeout, so "
            "re-run when it is quieter before reading this as a verdict on the test"
            if load.get("under_load")
            else "re-run: a timeout is not a verdict on the test"
        )
    return "; ".join(parts), extra
