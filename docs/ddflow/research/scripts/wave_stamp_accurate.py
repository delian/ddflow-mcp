"""wave_stamp_accurate.py: stamp each open task's wave:N tag with its true dependency depth.

Queue review 2026-10-23 (qa-graph). Replaces restamp_api.py until GR-wave-derived / DX-wave-phase-prereq land.
Rule (D-wave-semantics): wave = 1 + the highest wave among OPEN prerequisites; a task inherits its phase's
needs; a phase used as a prerequisite has the wave of its LAST open task (restamp_api.py used only the
phase's own needs, so every task behind a whole-phase hold came out 6 to 28 too low); doc and research
kinds are stamped too; running or leased items are skipped.

    python3 wave_stamp_accurate.py            # report only
    python3 wave_stamp_accurate.py --apply    # writes ddflow batch files and runs them (250 per batch)
"""
import json, re, subprocess, sys, tempfile
from collections import defaultdict
from pathlib import Path

from ddflow.api._base import _load

sys.setrecursionlimit(100000)
AGENT = "qa-graph"
_, _, st = _load(Path("."), AGENT)
I = st.items
L = {i: it for i, it in I.items() if it.state not in ("done", "abandoned") and not getattr(it, "removed", False)}
children = defaultdict(list)
for i, it in L.items():
    if it.kind != "phase" and it.parent in L:
        children[it.parent].append(i)


def prereq(i):
    it = L[i]
    out = [n for n in it.needs if n in L]
    if it.kind == "phase":
        out += children[i]
    elif it.parent in L:
        out += [n for n in L[it.parent].needs if n in L]
    return list(dict.fromkeys(out))


def depths():
    depth, color, cycles = {}, {}, []
    for s in L:
        if s in depth:
            continue
        stack, best = [(s, iter(prereq(s)))], {s: -1}
        color[s] = 1
        while stack:
            u, itr = stack[-1]
            for w in itr:
                if color.get(w) == 1:
                    cycles.append((u, w))
                elif w not in depth:
                    color[w], best[w] = 1, -1
                    stack.append((w, iter(prereq(w))))
                    break
                else:
                    best[u] = max(best[u], depth[w])
            else:
                stack.pop()
                depth[u], color[u] = best[u] + 1, 2
                if stack:
                    best[stack[-1][0]] = max(best[stack[-1][0]], depth[u])
    return depth, cycles


def main(apply):
    depth, cycles = depths()
    if cycles:
        sys.exit(f"cycle(s): {cycles[:5]}; stamping refused")
    edits, skipped = [], []
    for i, it in L.items():
        if it.kind == "phase":
            continue
        if it.state == "running" or it.lease:
            skipped.append(i)
            continue
        have = [t for t in it.tags if re.fullmatch(r"wave:\d+", t)]
        want = f"wave:{depth[i]}"
        if have != [want]:
            e = {"id": i, "add_tags": [want]}
            if [t for t in have if t != want]:
                e["remove_tags"] = [t for t in have if t != want]
            edits.append(e)
    print(f"{len(edits)} to stamp, {len(skipped)} in flight skipped")
    if not apply:
        return
    for k in range(0, len(edits), 250):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(edits[k:k + 250], f)
        subprocess.run(["ddflow", "--agent", AGENT, "batch", "--file", f.name], check=True)


main("--apply" in sys.argv)
