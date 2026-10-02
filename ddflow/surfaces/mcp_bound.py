"""What the MCP surface returns for the five largest reads, as a deliberate projection.

Measured over the real MCP path on this repository, before this module: `ddflow_progress`
150 KB, `ddflow_recall` 49 KB, `ddflow_decision_list` 44 KB, `ddflow_next` 39 KB (27 KB of
it the blocked list), `ddflow_show` 9-11 KB (gate evidence boilerplate). An MCP result is
read into a model's context in full, so each of those is paid for on every call.

The rule, set by `B-fix-status-bound` and kept here: counts are exact, a list that is cut
says so (`truncated`, or a second content block where the body is a bare array), and says
how to get the rest. Nothing is dropped silently. The CLI's `--json` is NOT cut: it is the
full body, so the two agree only while nothing crosses a bound -- `bound_*` is the one
place that difference lives, and `tests/test_mcp_payload_bound.py` pins it.

Each function takes the full body (what `--json` prints) and the call's arguments, and
returns ``(body, note)``: ``note`` is text for a second content block, or ``None`` when
the body carries its own `truncated` field.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

#: Blocked items `ddflow_next` lists. The reasons are counted in full beside them.
NEXT_BLOCKED_SHOWN = 10
#: Rows `ddflow_progress` and `ddflow_decision_list` return unless `limit` says otherwise;
#: `limit=0` is everything.
ROWS_SHOWN = 25
#: Characters of a decision's `decision` text kept in the list; `ddflow_decision_show`
#: has it whole, with the context and the alternatives.
DECISION_TEXT_SHOWN = 400
#: `ddflow_recall`'s answer budget when the caller names none -- the same 4000 the
#: tool has always advertised and the CLI's renderer applies.
RECALL_BUDGET = 4000
#: The evidence keys every gate record carries that identify the tree, not the outcome.
GATE_BOILERPLATE = (
    "diff_stat",
    "source_tree",
    "tree_sha",
    "diff_chars",
    "diff_source",
    "elapsed_s",
    "family",
)
_FRACTION = re.compile(r"\.\d+Z$")
#: Item fields that are merge bookkeeping (the full commit ids around the merge; `merged_sha`
#: is the answer). Left out of `ddflow_show`.
ITEM_PLUMBING = ("landed_before", "landed_after")
#: A string inside a gate record or a triage record is cut here. They are where agents and
#: reviewers wrote what they did and found; the long ones run to 700+ characters, and
#: `ddflow --json show` has them whole.
TEXT_SHOWN = 160
#: What a cut string ends in.
CUT_MARK = " [...]"


def _lean(value: Any) -> Any:
    """A mapping without its empty values (None, "", [], {}); other values untouched.
    `False` and 0 are real answers and stay."""
    if not isinstance(value, dict):
        return value
    return {k: v for k, v in value.items() if v is not None and v not in ("", [], {})}


def _limit(args: dict[str, Any], default: int = ROWS_SHOWN) -> int:
    raw = args.get("limit")
    try:
        n = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default
    # 0 is everything; a negative number is a mistake, not a request to lift the bound.
    return n if n >= 0 else default


def bound_next(body: Any, args: dict[str, Any]) -> tuple[Any, str | None]:
    """`ddflow_next`: the ready items, whole; the blocked list cut, its reasons counted."""
    if not isinstance(body, dict) or not isinstance(body.get("blocked"), list):
        return body, None
    out = dict(body)
    blocked = body["blocked"]
    out["ready"] = [_lean(i) for i in body.get("ready") or []]
    if len(blocked) > NEXT_BLOCKED_SHOWN:
        reasons: dict[str, int] = {}
        for b in blocked:
            reasons[b.get("reason", "")] = reasons.get(b.get("reason", ""), 0) + 1
        out["blocked"] = blocked[:NEXT_BLOCKED_SHOWN]
        out["truncated"] = {
            "blocked": len(blocked),
            "shown": NEXT_BLOCKED_SHOWN,
            "blocked_by_reason": reasons,
            "all": "`ddflow --json next` lists every blocked item; ddflow_show <id> says why one is",
        }
    return out, None


def _clip(value: Any, cut: list[int]) -> Any:
    """``value`` with each string that is more than `TEXT_SHOWN` plus the marker long cut to
    `TEXT_SHOWN` and ended in `CUT_MARK` (a shorter one stays whole: cutting would not
    save a byte); ``cut[0]``
    counts them."""
    if isinstance(value, str):
        # Only when the cut is a saving: the " [...]" marker is six characters.
        if len(value) <= TEXT_SHOWN + len(CUT_MARK):
            return value
        cut[0] += 1
        return value[:TEXT_SHOWN].rstrip() + CUT_MARK
    if isinstance(value, list):
        return [_clip(v, cut) for v in value]
    if isinstance(value, dict):
        return {k: _clip(v, cut) for k, v in value.items()}
    return value


def bound_show(body: Any, args: dict[str, Any]) -> tuple[Any, str | None]:
    """`ddflow_show`: the item without its empty fields; each gate record without the
    tree-identity boilerplate (`GATE_BOILERPLATE`); the gate and triage records' long
    strings cut (see `_clip`). The outcome, who, when, the reason and the evidence's
    own words stay, and `truncated` names what was left out."""
    if not isinstance(body, dict) or ("state" not in body and "gates" not in body):
        return body, None
    lean = _lean(body)
    out = {k: v for k, v in lean.items() if k not in ITEM_PLUMBING}
    stripped = len(out) != len(lean)
    gates = out.get("gates")
    if isinstance(gates, dict):
        slim: dict[str, Any] = {}
        for name, rec in gates.items():
            if not isinstance(rec, dict):
                slim[name] = rec
                continue
            one = {k: v for k, v in rec.items() if k != "gate"}
            stripped = True  # the record's own `gate` key (its name is the key) is left out
            if isinstance(one.get("at"), str):
                # Whole seconds: only a UTC stamp's fraction goes, anything else is as written.
                one["at"] = _FRACTION.sub("Z", one["at"])
            ev = one.get("evidence")
            if isinstance(ev, dict):
                kept = {k: v for k, v in ev.items() if k not in GATE_BOILERPLATE}
                stripped = stripped or len(kept) != len(ev)
                one["evidence"] = _lean(kept)
            slim[name] = _lean(one)
        out["gates"] = slim
    if "triage" in out:
        out["triage"] = copy.deepcopy(out["triage"])  # the caller's body is not edited
    for recs in (out.get("triage") or {}).values():
        for rec in recs.values() if isinstance(recs, dict) else ():
            if (
                isinstance(rec, dict)
                and rec.get("location")
                and rec["location"] == rec.get("title")
            ):
                rec.pop("location", None)  # before the cut, so it compares whole strings
                stripped = True
    cut = [0]
    for key in ("gates", "triage"):
        if key in out:
            out[key] = _clip(out[key], cut)
    if stripped or cut[0]:
        out["truncated"] = {
            "strings_cut": cut[0],
            "left_out": "tree ids, gate names, sub-second times, landed_before/after",
            "all": "ddflow --json show <id>",
        }
    return out, None


def _cut_rows(rows: list[Any], limit: int, newest_last: bool, what: str) -> tuple[list, str | None]:
    if not limit or len(rows) <= limit:
        return rows, None
    kept = rows[-limit:] if newest_last else rows[:limit]
    which = "the newest" if newest_last else "the first"
    return kept, (
        f"truncated: {what}: showing {which} {limit} of {len(rows)} (the total is exact). "
        f"Pass limit=0 for all of them."
    )


def bound_progress(body: Any, args: dict[str, Any]) -> tuple[Any, str | None]:
    """`ddflow_progress`: the rows (most effort first) cut to `limit`, default 25."""
    if not isinstance(body, list):
        return body, None
    return _cut_rows(body, _limit(args), False, "progress rows")


def bound_decisions(body: Any, args: dict[str, Any]) -> tuple[Any, str | None]:
    """`ddflow_decision_list`: the newest `limit` (default 25) decisions, each without
    its context, consequences and alternatives and with its decision text clipped;
    `ddflow_decision_show` returns one whole."""
    if not isinstance(body, list):
        return body, None
    rows, note = _cut_rows(body, _limit(args), True, "decisions")
    slim = []
    clipped = False
    for row in rows:
        r = _lean(
            {k: v for k, v in row.items() if k not in ("context", "consequences", "alternatives")}
        )
        text = r.get("decision")
        if isinstance(text, str) and len(text) > DECISION_TEXT_SHOWN:
            r["decision"] = text[:DECISION_TEXT_SHOWN].rstrip() + " [...]"
            clipped = True
        slim.append(r)
    extra = (
        "context, consequences and alternatives are left out of each row"
        + ("; long decision texts end in [...]" if clipped else "")
        + ": ddflow_decision_show <id> returns one whole."
    )
    return slim, f"{note} {extra}" if note else extra


def bound_recall(body: Any, args: dict[str, Any]) -> tuple[Any, str | None]:
    """`ddflow_recall`: each hit's id, kind, headline and body -- not the raw record --
    within `max_chars` (default 4000, counted as the JSON returned), taken one per kind in
    turn so no source is crowded out; the first hit overall is always kept."""
    if not isinstance(body, dict):
        return body, None
    try:
        budget = int(args.get("max_chars") or RECALL_BUDGET)
    except (TypeError, ValueError):
        budget = RECALL_BUDGET
    total = sum(len(v) for v in body.values() if isinstance(v, list))
    out: dict[str, list] = {k: [] for k, v in body.items() if isinstance(v, list)}
    used = 0
    depth = max((len(v) for v in body.values() if isinstance(v, list)), default=0)
    for rank in range(depth):
        for kind, hits in body.items():
            if not isinstance(hits, list) or rank >= len(hits):
                continue
            hit = {k: v for k, v in hits[rank].items() if k != "raw"}
            size = len(json.dumps(hit, default=str))
            # The budget is the size of what is returned. The very first hit is kept
            # whatever it costs, so a tiny budget still answers.
            if used and used + size > budget:
                continue
            out[kind].append(hit)
            used += size
    shown = sum(len(v) for v in out.values())
    note = "each hit's raw record is left out (its id is in the hit)"
    if shown < total:
        note = (
            f"truncated: showing {shown} of {total} hits within max_chars={budget}; {note}. "
            f"Raise max_chars for more."
        )
    return out, note


#: tool name -> the projection its MCP body goes through.
BOUNDS = {
    "ddflow_next": bound_next,
    "ddflow_show": bound_show,
    "ddflow_recall": bound_recall,
    "ddflow_decision_list": bound_decisions,
    "ddflow_progress": bound_progress,
}
