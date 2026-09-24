"""Probe R2: (a) fold throughput, (b) does a real git merge of two branches conflict?"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchard.core.model import fold
from orchard.infra.log import Event

# (a) throughput ---------------------------------------------------------------------
N = 20000
evs = []
for i in range(N):
    kind, subj = (
        ("task.added", f"T{i}")
        if i % 3 == 0
        else ("gate.passed", f"T{i // 3}")
        if i % 3 == 1
        else ("session.note", f"s{i // 100}")
    )
    e = Event(
        kind=kind,
        subject=subj,
        data={"gate": "unit_tests", "text": "x" * 40},
        agent=f"a{i % 8}",
        lamport=i + 1,
        ts="2026-09-24T00:00:00.0Z",
    )
    evs.append(Event(**{**e.__dict__, "id": e.compute_id()}))
t0 = time.perf_counter()
st = fold(evs, strict=False)
dt = time.perf_counter() - t0
print(f"(a) folded {N} events in {dt:.3f}s  ({N / dt:,.0f} events/s), {len(st.items)} items")

# (b) two branches, concurrent appends, real merge ------------------------------------
d = Path(tempfile.mkdtemp())


def g(*a, cwd=d):
    return subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True)


g("init", "-q", "-b", "main", ".")
g("config", "user.email", "t@t")
g("config", "user.name", "t")
(d / "README.md").write_text("x\n")
g("add", "-A")
g("commit", "-qm", "init")
env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}


def orch(*a, agent):
    return subprocess.run(
        [sys.executable, "-m", "orchard", "--repo", str(d), "--agent", agent, *a],
        capture_output=True,
        text=True,
        env=env,
    )


orch("init", agent="seed")
orch("phase", "add", "P1", "--title", "p", agent="seed")
g("add", "-A")
g("commit", "-qm", "orchard init")

g("checkout", "-qb", "b1")
orch("task", "add", "X1", "--phase", "P1", "--globs", "a/*", agent="b1")
g("add", ".orchard/events")
g("commit", "-qm", "b1 work")
g("checkout", "-q", "main")
g("checkout", "-qb", "b2")
orch("task", "add", "X2", "--phase", "P1", "--globs", "b/*", agent="b2")
g("add", ".orchard/events")
g("commit", "-qm", "b2 work")
m = g("merge", "b1", "--no-edit")
print(f"(b) merge exit={m.returncode}: {(m.stdout or m.stderr).strip().splitlines()[0]}")
conflicts = g("diff", "--name-only", "--diff-filter=U").stdout.strip()
print(f"    conflicted files: {conflicts or '(none)'}")
orch("rebuild", agent="v")
out = orch("--json", "next", "--phase", "P1", agent="v").stdout

ready = [r["id"] for r in json.loads(out)["ready"]]
print(f"    after rebuild, both branches' tasks present: {sorted(ready)}")
