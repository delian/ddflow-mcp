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

from ..core import limits as L
from ..core.textcut import clip
from ..views.output_caps import take_note

#: Blocked items `ddflow_next` lists. The reasons are counted in full beside them.
NEXT_BLOCKED_SHOWN = 10
#: Rows `ddflow_progress` and `ddflow_decision_list` return unless `limit` says otherwise;
#: `limit=0` is everything.
ROWS_SHOWN = 25
#: Characters of a decision's `decision` text kept in the list; `ddflow_decision_show`
#: has it whole, with the context and the alternatives.
DECISION_TEXT_SHOWN = 400
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
#: Findings one gate's evidence `chunk_findings` list and one gate's triage keep in
#: `ddflow_show`; the rest are counted. Cutting every STRING was not enough: one round
#: that found hundreds still overflowed the surface budget (B-show-mcp-result-over-ceiling).
FINDINGS_SHOWN = 10
#: `ddflow_show`'s projected body is kept under this many characters once serialised. The
#: structural caps above bound the real evidence shapes; this is the guarantee no nested
#: shape slips past them (r-surface-budget: 20 KB -- the margin covers the note).
SHOW_MAX_CHARS = 16_000
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
    if args.get("full") or "truncated" in body:
        # `full=true` lifts the cut; a body `output_caps` already cut names its own footer.
        return {**body, "ready": [_lean(i) for i in body.get("ready") or []]}, None
    out = dict(body)
    blocked = body["blocked"]
    cut = [0]
    out["ready"] = [_clip(_lean(i), cut) for i in body.get("ready") or []]
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
    if cut[0]:
        out.setdefault("truncated", {})["clipped_strings"] = cut[0]
    return out, None


def _shorten(text: str, shown: int) -> str:
    """``text`` cut to ``shown`` characters and ended in `CUT_MARK`; one no longer than
    ``shown`` plus the marker stays whole (cutting would not save a byte)."""
    return clip(text, shown + len(CUT_MARK), keep=shown, marker=CUT_MARK)


def _clip(value: Any, cut: list[int]) -> Any:
    """``value`` with each string that is more than `TEXT_SHOWN` plus the marker long cut to
    `TEXT_SHOWN` and ended in `CUT_MARK` (a shorter one stays whole: cutting would not
    save a byte); ``cut[0]``
    counts them."""
    if isinstance(value, str):
        out = _shorten(value, TEXT_SHOWN)
        cut[0] += out != value
        return out
    if isinstance(value, list):
        return [_clip(v, cut) for v in value]
    if isinstance(value, dict):
        return {k: _clip(v, cut) for k, v in value.items()}
    return value


def _slim_gates(out: dict[str, Any]) -> bool:
    """Rewrite `out["gates"]` without the boilerplate; whether anything was left out."""
    stripped = False
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
    return stripped


def _drop_repeated_locations(out: dict[str, Any]) -> bool:
    """Copy `out["triage"]` (the caller's body is not edited) and drop a location equal to
    its title; whether anything was left out."""
    stripped = False
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
    return stripped


def _homogeneous(value: dict) -> bool:
    """Whether every value is the same SHAPE (all scalars, all lists, or all dicts): the
    mark of a series keyed by test/finding id, not a field struct whose value kinds mix."""
    kinds = {
        "list" if isinstance(v, list) else "dict" if isinstance(v, dict) else "scalar"
        for v in value.values()
    }
    return len(kinds) == 1


def _cap_findings(out: dict[str, Any], cap: int = FINDINGS_SHOWN) -> int:
    """Cap the repeatable evidence `ddflow_show` keeps. Every LIST inside a gate record
    (the per-finding lists -- `chunk_findings`, `baseline`, `failed_tests`, `failure_tails`,
    `failed_reasons`, `titles`, ...), every homogeneous series map (the `baseline` /
    `failed_reasons` maps keyed by test id, or a pathological evidence dict) and each gate's
    triage keep the first `cap`; the rest are counted, not echoed. A field STRUCT mixes
    value kinds and stays whole."""
    dropped = [0]

    def walk(value: Any, root: bool = False) -> Any:
        if isinstance(value, list):
            if len(value) > cap:
                dropped[0] += len(value) - cap
                value = value[:cap]
            return [walk(v) for v in value]
        if isinstance(value, dict):
            # The root `gates` map names the gates themselves and is not a series; a
            # homogeneous map keyed by test/finding id is, and is capped like a list.
            if not root and len(value) > cap and _homogeneous(value):
                dropped[0] += len(value) - cap
                value = dict(list(value.items())[:cap])
            return {k: walk(v) for k, v in value.items()}
        return value

    gates = out.get("gates")
    if isinstance(gates, dict):
        out["gates"] = walk(gates, root=True)
    triage = out.get("triage")
    if isinstance(triage, dict):
        for gate, recs in list(triage.items()):
            kept = recs
            if isinstance(recs, dict) and len(recs) > cap:
                dropped[0] += len(recs) - cap
                kept = dict(list(recs.items())[:cap])
            triage[gate] = walk(kept)
    return dropped[0]


def _shrink_to_budget(out: dict[str, Any]) -> int:
    """The `_cap_findings` caps are a contract, not a guarantee: a nested dict-of-dicts
    (each inner map just under `FINDINGS_SHOWN`) is indistinguishable from an evidence
    record and can still overflow. Re-cap `gates` and `triage` to a shrinking cap until
    the serialised body is under `SHOW_MAX_CHARS`, counting what goes."""
    dropped = 0
    cap = FINDINGS_SHOWN
    while len(json.dumps(out)) > SHOW_MAX_CHARS and cap > 0:
        cap //= 2
        dropped += _cap_findings(out, cap)
    return dropped


def bound_show(body: Any, args: dict[str, Any]) -> tuple[Any, str | None]:
    """`ddflow_show`: the item without its empty fields; each gate record without the
    tree-identity boilerplate (`GATE_BOILERPLATE`); the gate and triage records' long
    strings cut (see `_clip`) and their findings capped (see `_cap_findings`). The outcome,
    who, when, the reason and the evidence's own words stay, and `truncated` names what
    was left out."""
    if not isinstance(body, dict) or ("state" not in body and "gates" not in body):
        return body, None
    lean = _lean(body)
    out = {k: v for k, v in lean.items() if k not in ITEM_PLUMBING}
    stripped = len(out) != len(lean)
    stripped = _slim_gates(out) or stripped
    stripped = _drop_repeated_locations(out) or stripped
    cut = [0]
    for key in ("gates", "triage"):
        if key in out:
            out[key] = _clip(out[key], cut)
    findings_cut = _cap_findings(out)
    if len(json.dumps(out)) > SHOW_MAX_CHARS:
        findings_cut += _shrink_to_budget(out)
    if stripped or cut[0] or findings_cut:
        out["truncated"] = {
            "strings_cut": cut[0],
            "findings_cut": findings_cut,
            "left_out": "tree ids, gate names, sub-second times, landed_before/after, findings beyond the first",
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
        if isinstance(text, str) and (short := _shorten(text, DECISION_TEXT_SHOWN)) != text:
            r["decision"] = short
            clipped = True
        slim.append(r)
    extra = (
        "context, consequences and alternatives are left out of each row"
        + ("; long decision texts end in [...]" if clipped else "")
        + ": ddflow_decision_show <id> returns one whole."
    )
    return slim, f"{note} {extra}" if note else extra


def bound_recall(body: Any, args: dict[str, Any]) -> tuple[Any, str | None]:
    """`ddflow_recall`: each hit's id, kind, headline and body -- not the raw record. The
    budget (`max_chars`, default 4000) is enforced once, in the API's context pack, so what
    arrives here is already within it; the outcome's reason says what that left out."""
    if not isinstance(body, dict):
        return body, None
    out = {
        k: [{f: v for f, v in hit.items() if f != "raw"} for hit in hits]
        for k, hits in body.items()
        if isinstance(hits, list)
    }
    return out, "each hit's raw record is left out (its id is in the hit)"


def bound_list(body: Any, args: dict[str, Any]) -> tuple[Any, str | None]:
    """`ddflow_list`: rows arrive already cut by `limit` (default 25) and carry their own
    `total`/`truncated`, so a cut list gets the way to the rest as a note. One session
    shown in full is cut to its newest `limit` (default 25) entries, each text clipped."""
    if not isinstance(body, dict):
        return body, None
    entries = body.get("entries")
    if isinstance(entries, list):
        kept, note = _cut_rows(entries, _limit(args), True, "session entries")
        cut = [0]
        out = {**body, "entries": _clip(kept, cut)}
        if cut[0]:
            more = f"{cut[0]} long entry text(s) end in [...]: the CLI's `ddflow session show <id>` has them whole."
            note = f"{note} {more}" if note else more
        return out, note
    if body.get("truncated") is True and isinstance(body.get("rows"), list):
        # Only the record lists page; a search or session list carries no `next`.
        cap = {"search": L.SEARCH_MAX_LIMIT, "session": L.SESSION_MAX_LIMIT}.get(
            str(body.get("record_kind")), L.LIST_MAX_LIMIT
        )
        if body.get("next") is not None:
            how = f"repeat the call with limit={body.get('limit')} offset={body['next']}"
        elif isinstance(body.get("limit"), int) and (body["limit"] == 0 or body["limit"] >= cap):
            how = None  # already at the most this kind gives: only narrowing helps
        else:
            how = "raise limit"
        return body, (
            f"truncated: showing {body.get('shown')} of {body.get('total')}; "
            f"narrow the filters{f', or {how}' if how else ''} "
            f"(limit 0 = the most a list gives: {L.LIST_MAX_LIMIT}, a search {L.SEARCH_MAX_LIMIT})."
        )
    return body, None


def bound_recover(body: Any, args: dict[str, Any]) -> tuple[Any, str | None]:
    """`ddflow_recover`: the body is a bare array, so a cut (`output_caps`) is told in a
    second content block, never by changing the array's shape."""
    return body, take_note() or None


#: tool name -> the projection its MCP body goes through.
BOUNDS = {
    "ddflow_next": bound_next,
    "ddflow_recover": bound_recover,
    "ddflow_show": bound_show,
    "ddflow_recall": bound_recall,
    "ddflow_decision_list": bound_decisions,
    "ddflow_progress": bound_progress,
    "ddflow_list": bound_list,
}
