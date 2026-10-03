"""B169/B166 benchmark: cold read and warm read (one append) of an event log of 20k/100k
events. Not a test (no test_ prefix). Run: PYTHONPATH=. python tests/bench_log_read.py
"""

import dataclasses
import os
import pathlib
import statistics
import subprocess
import tempfile
import time

from ddflow.core.events import Event
from ddflow.infra.log import EventLog, clear_parse_cache

EventLog.stamp = False


def ev(a, lam):
    e = Event(
        kind="task.added",
        subject=f"T{lam}",
        data={"title": "t", "kind": "task"},
        agent=a,
        lamport=lam,
        ts="2026-10-03T00:00:00Z",
    )
    return dataclasses.replace(e, id=e.compute_id())


for n in (20000, 100000):
    d = pathlib.Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", str(d)])
    from ddflow.config import LogConfig

    log = EventLog(d, "x", log_cfg=LogConfig(max_cached_events=1_000_000))
    log.dir.mkdir(parents=True, exist_ok=True)
    for a in "ab":
        with (log.dir / f"{a}.jsonl").open("wb") as f:
            for i in range(1, n // 2 + 1):
                f.write((ev(a, i).to_json() + "\n").encode())

    def cold_ms(cfg, d=d):
        out = []
        for _ in range(3):
            clear_parse_cache()  # a fresh process: nothing parsed, nothing loaded
            t = time.perf_counter()
            EventLog(d, "x", log_cfg=cfg).read_all()
            out.append((time.perf_counter() - t) * 1000)
        return statistics.median(out)

    os.environ["DDFLOW_SNAPSHOT"] = "0"  # ignored by a ddflow without the snapshot
    nosnap = cold_ms(LogConfig(max_cached_events=1_000_000))
    del os.environ["DDFLOW_SNAPSHOT"]
    cold_ms(LogConfig(max_cached_events=1_000_000))  # the first read with the snapshot on writes it
    snap = cold_ms(LogConfig(max_cached_events=1_000_000))
    log = EventLog(d, "x", log_cfg=LogConfig(max_cached_events=1_000_000))
    log.read_all()
    warm = []
    for k in range(7):
        with (log.dir / "a.jsonl").open("ab") as f:
            f.write((ev("a", n + k + 1).to_json() + "\n").encode())
        t = time.perf_counter()
        log.read_all()
        warm.append((time.perf_counter() - t) * 1000)
    print(
        f"{n}: cold(no snapshot) {nosnap:.1f} ms  cold(snapshot) {snap:.1f} ms  "
        f"warm(1 append) {statistics.median(warm):.1f} ms"
    )
