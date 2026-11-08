"""The offline conformance of ONE driver descriptor (`ddflow harness test`).

The per-descriptor checks that need no recorded fixture, no probe and no install: the
facts overlay lists only well-formed facts, and a command-hooked descriptor's normalizer
and emitter resolve. The fixture-backed checks (E1 hook payloads, E4 argv, E5 stream and
the Lane contract) run in the test suite (`tests/test_driver_conformance.py`), the other
half of DRV-conformance. Together they are the one offline conformance suite; this module
is the half the CLI runs for a single descriptor.
"""

from __future__ import annotations

from ddflow.services import facts, harnessfacts, hookio
from ddflow.services import harnessreg as HR


def run(h) -> dict:
    """Run the descriptor-only checks for ``h`` and return ``{agent, status, checks}``.

    ``status`` is ``"ok"`` when every check passed and ``"failed"`` otherwise. Each check
    is ``{status, detail}``; a check nothing applies to is left out rather than recorded a
    silent pass about something that never ran.
    """
    checks: dict[str, dict] = {}
    hid = h.id

    fact_rows = harnessfacts.rows(h, absent=True)
    fact_problems: list[str] = []
    for row in fact_rows:
        if row["status"] not in facts.STATUSES:
            fact_problems.append(f"{row['fact']}: unknown status {row['status']}")
    for key, node in h.facts.items():
        fact_problems.extend(facts.problems(f"{hid}.{key}", node))
    if fact_problems:
        checks["facts"] = {"status": "failed", "detail": "; ".join(fact_problems)}
    else:
        checks["facts"] = {"status": "ok", "detail": f"{len(fact_rows)} fact(s), all well-formed"}

    if h.hooks is not None and h.hooks.style in HR.COMMAND_HOOK_STYLES:
        # The plan is built from the descriptor in hand, not re-fetched by id: a local
        # descriptor (`ddflow harness add`) is not in the shipped registry, and an override
        # is the one under test, so `plan_for(hid)` would read the wrong (or no) descriptor.
        plan = hookio.Plan(
            hid, h.hooks.normalizer, h.hooks.emitter, h.hooks.inject, dict(h.hooks.events)
        )
        unresolved = [
            name
            for name, table in (
                ("normalizer", hookio.NORMALIZERS),
                ("emitter", hookio.EMITTERS),
            )
            if getattr(plan, name) not in table
        ]
        if unresolved:
            checks["hooks"] = {
                "status": "failed",
                "detail": f"unresolved {', '.join(unresolved)}",
            }
        else:
            checks["hooks"] = {
                "status": "ok",
                "detail": f"{plan.normalizer}/{plan.emitter}",
            }

    ok = all(c["status"] == "ok" for c in checks.values())
    return {"agent": hid, "status": "ok" if ok else "failed", "checks": checks}
