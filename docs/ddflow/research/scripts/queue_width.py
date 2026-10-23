"""queue_width.py: parallel width, critical path and lanes of the open queue.

Queue review 2026-10-23 (qa-graph). Read-only. Reuses the depth rule of wave_stamp_accurate.py.
Run from the repo root:  python3 docs/ddflow/research/scripts/queue_width.py [--what-if P-a,P-b:P-x ...]
`--what-if PHASE:NEED` drops phase-level need NEED from PHASE (comma lists allowed) and re-measures.
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path

here = Path(__file__).parent
# wave_stamp_accurate runs main() on import; reuse its pieces by exec of the top part instead
src = (here / "wave_stamp_accurate.py").read_text().split("\ndef main(")[0]
ns = {"__name__": "ws"}
exec(compile(src, "wave_stamp_accurate.py", "exec"), ns)
L, prereq, depths, children = ns["L"], ns["prereq"], ns["depths"], ns["children"]

for arg in sys.argv[1:]:
    if arg.startswith("--"):
        continue
    ph, need = arg.split(":")
    for n in need.split(","):
        if n in L[ph].needs:
            L[ph].needs.remove(n)

D, cycles = depths()
tasks = [i for i, it in L.items() if it.kind != "phase"]
print(f"open tasks {len(tasks)}; cycles {len(cycles)}; max wave {max(D[t] for t in tasks)}; total depth {sum(D[t] for t in tasks)}")
print("wave sizes:", [(w, sum(1 for t in tasks if D[t] == w)) for w in range(0, 12)])
end = max(tasks, key=lambda t: D[t])
path = [end]
while D[path[-1]] > 0:
    path.append(next(p for p in prereq(path[-1]) if D[p] == D[path[-1]] - 1))
print("critical path:", " <- ".join(path))


def chain_levels(ts):
    """Longest intra-phase chain level of each task of one phase."""
    memo = {}

    def lv(t):
        if t not in memo:
            memo[t] = 0
            memo[t] = max([lv(n) + 1 for n in L[t].needs if n in ts] or [0])
        return memo[t]

    return [lv(t) for t in ts]


def lanes(ts):
    """Tasks joined when they share a literal file (README.md ignored); the size of each lane."""
    parent = {t: t for t in ts}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_glob = defaultdict(list)
    for t in ts:
        for g in L[t].globs:
            if g != "README.md":
                by_glob[g].append(t)
    for group in by_glob.values():
        for a in group[1:]:
            parent[find(a)] = find(group[0])
    return Counter(find(t) for t in ts)


print("\nphase tasks minwave maxwave chain width ready")
for p in sorted(children, key=lambda p: (min(D[t] for t in children[p]), p)):
    ts = set(children[p])
    lvs = chain_levels(ts)
    print(p, len(ts), min(D[t] for t in ts), max(D[t] for t in ts), max(lvs) + 1, max(Counter(lvs).values()),
          sum(1 for t in ts if D[t] == 0))
print("\nlanes (tasks of one wave joined when they share a literal file; README.md ignored)")
for w in (0, 1, 2):
    ts = [t for t in tasks if D[t] == w and L[t].state in ("open", "blocked")]
    comp = lanes(ts)
    print(f"wave {w}: {len(ts)} tasks, {len(comp)} lanes, largest {sorted(comp.values(), reverse=True)[:6]}")
