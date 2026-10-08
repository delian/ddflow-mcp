"""`ddflow gate ...` — the human surface for `api.gates`.

`cmd_gate` was 192 lines and carried the rule-set: order enforcement and its three
policies, the human- and agent-gate refusals, evidence assembly, the out-of-order event
and the outcome-to-exit mapping. All of that is in `api/gates.py` now; what is left here
is a dispatcher and four renderings.
"""

from __future__ import annotations

import json
import sys

from ...api import gates as A
from ..context import FAIL, NOTHING, OK, REFUSED, Ctx


def _gate_status(a, c: Ctx) -> int:
    out = A.status(c.repo, a.id, agent=c.requested_agent)
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return FAIL
    if c.json:
        print(json.dumps(out.body("status"), indent=2, default=str))
        return OK
    print(out.data["text"])
    return OK


def _gate_list(a, c: Ctx) -> int:
    out = A.list_gates(c.repo, refuted=a.refuted, agent=c.requested_agent)
    if c.json:
        print(json.dumps(out.body(("refuted", "count", "passes", "gates")), indent=2, default=str))
        return OK
    print(out.data["text"])
    return OK


def _gate_verify(a, c: Ctx) -> int:
    """`gate verify` — can this gate go red at all?

    Its own function because it is the only gate subcommand that WRITES to the working
    tree and restores it, and burying that in a chain of `if` arms is how a reader
    misses it.
    """
    out = A.verify(c.repo, a.id, a.gate, agent=c.requested_agent)
    if c.json:
        print(json.dumps(out.body(("gate", "reason", "results", "verified")), indent=2))
        return out.exit
    if out.data.get("reason"):
        print(out.data["reason"], file=sys.stderr)
        return out.exit
    for r in out.data.get("results", []):
        mark = "OK  " if (r["detected"] and r["applied"]) else "FAIL"
        print(f"  {mark} {r['file']}: {'detected' if r['detected'] else r['detail']}")
    if out.data.get("verified"):
        print(f"\n{a.gate} CAN fail: every registered mutation was caught.")
        return OK
    print(f"\n{out.reason}", file=sys.stderr)
    return out.exit


def _gate_run(a, c: Ctx) -> int:
    out = A.run(c.repo, a.id, a.gate, agent=c.requested_agent, called_from=c.called_from)
    if out.exit == FAIL and not out.data.get("outcome"):
        print(out.reason, file=sys.stderr)
        return FAIL
    if out.exit == NOTHING and not out.data.get("outcome"):
        # A human or agent gate: the REASON is the instruction, and it is the whole point
        # of the call. stderr, because nothing was recorded.
        print(out.reason, file=sys.stderr)
        return NOTHING
    if out.exit == REFUSED:
        print(out.reason, file=sys.stderr)
        return REFUSED
    ev = out.data.get("evidence") or {}
    tail = ev.get("tail", "")
    if not c.json and tail:
        print(tail[-1200:])
    c.out(
        f"{a.gate}: {out.data['outcome'].upper()}"
        + (f" — {ev.get('reason', '')}" if ev.get("reason") else ""),
        out.body(("gate", "outcome", "evidence")),
    )
    return out.exit


def _gate_record(a, c: Ctx, *, skip: bool) -> int:
    out = A.record(
        c.repo,
        a.id,
        a.gate,
        outcome=getattr(a, "outcome", "") or "",
        skip=skip,
        reason=a.reason or "",
        evidence=A.Evidence(
            note=a.evidence or "",
            command=a.command or "",
            exit_code=a.exit_code,
            model=getattr(a, "reviewer_model", "") or a.model or "",
            model_is_reviewer=bool(getattr(a, "reviewer_model", "")),
            reviewed_sha=getattr(a, "reviewed_sha", "") or "",
            output_file=a.output_file or "",
        ),
        agent=c.requested_agent,
        called_from=c.called_from,
    )
    if out.exit != OK:
        print(out.reason, file=sys.stderr)
        return out.exit
    # stderr, and BEFORE the result: it is a caveat on what is about to be printed, and
    # on stdout it would corrupt `--json`.
    if out.data.get("warning"):
        print(out.data["warning"], file=sys.stderr)
    c.out(f"{a.id}.{a.gate} = {out.data['outcome']}", out.body(("gate", "outcome")))
    return OK


def cmd_gate(a, c: Ctx) -> int:
    if a.gate_cmd == "status":
        return _gate_status(a, c)
    # BEFORE the pipeline-order check, deliberately — see `api.gates.verify`.
    if a.gate_cmd == "list":
        return _gate_list(a, c)
    if a.gate_cmd == "verify":
        return _gate_verify(a, c)
    if a.gate_cmd == "run":
        return _gate_run(a, c)
    if a.gate_cmd in ("record", "skip"):
        return _gate_record(a, c, skip=a.gate_cmd == "skip")
    return FAIL
