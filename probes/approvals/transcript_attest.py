"""Probe (RA-probe-harness): can ddflow read a person's AskUserQuestion answer from the harness's
own transcript instead of from the model?

Usage: python transcript_attest.py TRANSCRIPT.jsonl            list attested answers
       python transcript_attest.py --selftest                  run on a fabricated transcript

An answer is "attested" when the transcript holds, in this order:
  1. an assistant row whose content has a tool_use named AskUserQuestion (id U, row uuid A),
  2. a later user row with a tool_result for U, `sourceToolAssistantUUID` == A,
     `permissionDecision.decision == "accept"` and a `toolUseResult.answers` mapping the
     question text to the chosen label (or typed text).
The shape was read from a real Claude Code 2.1.296 transcript (sdk-cli entrypoint, bridge
environment). Nothing here proves the file was written by the harness: a process of the same
uid can append such rows (see the doc). Standard library only.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

TOOL = "AskUserQuestion"


def attestations(path: Path) -> list[dict]:
    uses: dict[str, dict] = {}  # tool_use id -> {"uuid": assistant row, "questions": [...]}
    out: list[dict] = []
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        msg = row.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        if row.get("type") == "assistant":
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == TOOL
                ):
                    uses[block["id"]] = {
                        "uuid": row.get("uuid"),
                        "questions": [
                            q.get("question") for q in block["input"].get("questions", [])
                        ],
                    }
        elif row.get("type") == "user":
            for block in content:
                use = uses.get(block.get("tool_use_id", "")) if isinstance(block, dict) else None
                if not use or block.get("type") != "tool_result":
                    continue
                result = row.get("toolUseResult") or {}
                decision = (row.get("permissionDecision") or {}).get("decision")
                ok = (
                    decision == "accept"
                    and row.get("sourceToolAssistantUUID") == use["uuid"]
                    and isinstance(result.get("answers"), dict)
                )
                out.append(
                    {
                        "tool_use_id": block["tool_use_id"],
                        "at": row.get("timestamp"),
                        "attested": ok,
                        "decision": decision,
                        "decision_source": (row.get("permissionDecision") or {}).get("source"),
                        "answers": result.get("answers", {}),
                        "asked": use["questions"],
                    }
                )
    return out


def _selftest() -> int:
    rows = [
        {
            "type": "assistant",
            "uuid": "A1",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "U1",
                        "name": TOOL,
                        "input": {"questions": [{"question": "Q?"}]},
                    }
                ]
            },
        },
        {
            "type": "user",
            "uuid": "R1",
            "sourceToolAssistantUUID": "A1",
            "timestamp": "t",
            "permissionDecision": {"decision": "accept", "source": "user_temporary"},
            "toolUseResult": {"answers": {"Q?": "Yes"}},
            "message": {"content": [{"type": "tool_result", "tool_use_id": "U1", "content": "x"}]},
        },
        # a result nobody asked for (a different tool_use id) and one with the wrong source row
        {
            "type": "user",
            "uuid": "R2",
            "sourceToolAssistantUUID": "ZZ",
            "permissionDecision": {"decision": "accept"},
            "toolUseResult": {"answers": {"Q?": "Yes"}},
            "message": {"content": [{"type": "tool_result", "tool_use_id": "U1", "content": "x"}]},
        },
    ]
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in rows))
        got = attestations(p)
    assert [g["attested"] for g in got] == [True, False], got
    assert got[0]["answers"] == {"Q?": "Yes"}
    print("selftest ok")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        raise SystemExit(_selftest())
    for a in attestations(Path(sys.argv[1])):
        print(json.dumps(a))
