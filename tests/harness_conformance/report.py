"""The fixture gap report and the ratchet.

``rows()`` says, per agent and declared check, whether a fixture is verified (recorded), synthetic
(labelled, not captured) or unverified (missing: a gap, never a pass). ``gaps.json`` is the
committed set of known gaps: the suite fails on a gap that is not in it (a new descriptor
without fixtures) and on an entry that is no longer a gap, so the set only shrinks.
Regenerate with ``python tests/harness_conformance/report.py --write``; print with no args.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

for _up in (1, 2):  # tests/ (harness_conformance) and the repo root (ddflow)
    sys.path.insert(0, str(Path(__file__).resolve().parents[_up]))

from ddflow.services import harnessreg as HR  # noqa: E402
from harness_conformance import declared as D  # noqa: E402

BASELINE = Path(__file__).with_name("gaps.json")
#: A hook normalizer or emitter named by fewer descriptors than this is bespoke Python.
SHARE_MIN = 2


def rows() -> list[dict]:
    out = []
    for hid, kind, name in D.all_checks():
        fx = D.load(hid, kind, name)
        meta = (fx or {}).get("_meta")
        meta = meta if isinstance(meta, dict) else {}
        out.append(
            {
                "agent": hid,
                "check": f"{kind}:{name}" if name else kind,
                "status": D.status(fx),
                "recorded": meta.get("recorded", False),
                "version": meta.get("version", "-"),
            }
        )
    return out


def gaps() -> list[str]:
    return sorted(f"{r['agent']}:{r['check']}" for r in rows() if r["status"] == D.UNVERIFIED)


def baseline() -> list[str]:
    return sorted(json.loads(BASELINE.read_text())["gaps"]) if BASELINE.is_file() else []


def zero_python() -> dict:
    """The D-DRV-2 metric. An agent needs per-agent Python when its hook normalizer or emitter
    is used by no other descriptor; one with no command hooks needs none. A proxy, stated."""
    hs = HR.load()
    use: Counter = Counter()
    for h in hs:
        if h.hooks is not None:
            use.update({("n", h.hooks.normalizer), ("e", h.hooks.emitter)})
    bespoke = sorted(
        h.id
        for h in hs
        if h.hooks is not None
        and any(
            use[k] < SHARE_MIN for k in (("n", h.hooks.normalizer), ("e", h.hooks.emitter)) if k[1]
        )
    )
    return {
        "agents": len(hs),
        "zero_python": len(hs) - len(bespoke),
        "rate": round((len(hs) - len(bespoke)) / len(hs), 3) if hs else 0.0,
        "bespoke": bespoke,
    }


def render() -> str:
    rs = rows()
    lines = ["| agent | check | status | recorded | version |", "|---|---|---|---|---|"]
    lines += [
        f"| {r['agent']} | {r['check']} | {r['status']} | {str(r['recorded']).lower()} "
        f"| {r['version']} |"
        for r in rs
    ]
    c = Counter(r["status"] for r in rs)
    z = zero_python()
    lines.append(
        f"\nchecks: {dict(c)}; agents with no declared check: "
        f"{sorted({h.id for h in HR.load()} - {r['agent'] for r in rs})}"
    )
    lines.append(f"zero-Python agents: {z['zero_python']}/{z['agents']} ({z['rate']})")
    return "\n".join(lines)


if __name__ == "__main__":
    if "--write" in sys.argv:
        BASELINE.write_text(json.dumps({"gaps": gaps()}, indent=1) + "\n")
    print(render())
