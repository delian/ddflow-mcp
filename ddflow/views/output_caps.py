"""Default output caps of the top-level read commands (UX-output-caps, D-scope-focus-2026-11).

`board`, `doctor`, `recover`, `next` and `status` used to print everything: `board` was
1.8 MB (about 446k tokens) on a big log, `doctor` about 37k tokens, and an MCP client
refuses a tool result past 25k tokens. Each now prints a bounded default and ends in a
footer naming the EXACT next call (CLI and MCP form), and `--full` / `full=true` lifts the
caps for that call. The limits are the `[output]` knobs (`max_rows`, `max_chars`,
`allow_full`).

One function, `apply`, serves both surfaces (the declared command lambdas and the CLI
handlers call it), so the two cannot disagree. A `--json` body keeps its schema: lists are
shorter, and `truncated` and `next` say what was cut and how to get it.

Bounded by construction (r-bounded-everything): a row's heavy nested fields go first, the
row limit only halves, down to 1, and the text cut is one slice. The text and the rows are each held to `max_chars`, so a `--json` body that
carries both (board) stays under twice it.
"""

from __future__ import annotations

import copy
import json
import shlex
from contextvars import ContextVar
from typing import Any

from ..config import Config
from ..core.outcome import Outcome

#: command -> (CLI form, MCP tool, the narrowing flag/arg pair or None, the list that pages
#: with `cursor` or "").
COMMANDS: dict[str, tuple[str, str, tuple[str, str] | None, str]] = {
    "board": ("ddflow board", "ddflow_board", ("--phase ID", "phase=ID"), ""),
    "doctor": ("ddflow doctor", "ddflow_doctor", None, ""),
    "recover": ("ddflow recover", "ddflow_recover", ("--item ID", "item=ID"), "found"),
    "next": ("ddflow next", "ddflow_next", ("--phase ID", "phase=ID"), "blocked"),
    "status": ("ddflow status", "ddflow_status", ("--by-tag KEY", "by_tag=KEY"), ""),
}
#: The board lists `_cut_board` cuts itself.
#: The shortest a long string is clipped to when one row alone passes the cap.
MIN_CLIP = 80
#: A nested field of a row may hold this share of the budget before it goes (1/8: a ready
#: item's recorded gate evidence is tens of KB, its own fields a few hundred characters).
FIELD_SHARE = 8
#: Keys of the cut record that are not a list's real length.
NOT_A_LIST = ("next_row", "clipped_strings", "fields_cut")
_BOARD_KEYS = ("phases", "unphased", "critical_path")
#: The prose list that is the same rows as a paged list, under another name (`recover --plan`).
RENDERED_PAGE = {"found": "actions"}
#: Lists whose NEWEST rows are the ones a reader wants (kept from the end).
TAIL_KEYS = frozenset({"completed_tasks"})

#: The footer of the last capped call, for a tool whose wire body is a bare array (recover):
#: the MCP layer reads it once with `take_note` and sends it as a second content block.
_NOTE: ContextVar[str] = ContextVar("ddflow_output_note", default="")


def take_note() -> str:
    """The footer the last `apply` made, once (empty when nothing was cut)."""
    note = _NOTE.get()
    _NOTE.set("")
    return note


def _size(data: dict[str, Any]) -> int:
    return len(
        json.dumps({k: v for k, v in data.items() if not k.startswith("_")}, default=str, indent=2)
    )


def _rows(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _slice(key: str, rows: list[Any], limit: int, start: int) -> list[Any]:
    if key in TAIL_KEYS:
        return rows[-limit:] if limit else rows
    return rows[start : start + limit]


def _cut_board(data: dict[str, Any], limit: int, start: int, info: dict[str, int]) -> None:
    """Phases from `start`, with at most `limit` task rows in all (a phase may be cut)."""
    phases = data.get("phases")
    if not isinstance(phases, list):
        return
    total_tasks = sum(_rows(p.get("tasks")) for p in phases if isinstance(p, dict))
    kept: list[Any] = []
    budget = limit
    for phase in phases[start:]:
        if budget <= 0:
            break
        p = dict(phase)
        tasks = p.get("tasks") or []
        p["tasks"] = tasks[:budget]
        budget -= len(p["tasks"])
        kept.append(p)
        if len(tasks) > len(p["tasks"]):
            break
    shown = sum(_rows(p.get("tasks")) for p in kept)
    if len(kept) < len(phases) or shown < total_tasks:
        info["phases"] = len(phases)
        info["task_rows"] = total_tasks
        info["task_rows_shown"] = shown
        info["next_row"] = start + len(kept)
    data["phases"] = kept
    for key in ("unphased", "critical_path"):
        if _rows(data.get(key)) > limit:
            info[key] = len(data[key])
            data[key] = data[key][:limit]


def _cut_lists(command: str, data: dict[str, Any], limit: int, start: int) -> dict[str, int]:
    """Cut `data`'s lists in place; return {list: real length} (+ board extras)."""
    info: dict[str, int] = {}
    paged = _entry(command)[3]
    if command == "board":
        _cut_board(data, limit, start, info)
    skip = _BOARD_KEYS if command == "board" else ()
    for key, value in list(data.items()):
        if key.startswith("_") or not isinstance(value, list) or key in skip:
            continue
        off = start if key == paged else 0
        if len(value) > limit or off:
            info[key] = len(value)
            data[key] = _slice(key, value, limit, off)
            if key == paged and off + limit < len(value):
                info["next_row"] = off + limit
    by_tag = data.get("by_tag")
    if isinstance(by_tag, dict) and _rows(by_tag.get("values")) > limit:
        info["by_tag"] = len(by_tag["values"])
        data["by_tag"] = {**by_tag, "values": by_tag["values"][:limit]}
    render = data.get("_render")
    if isinstance(render, dict):
        for key, value in list(render.items()):
            off = start if key in (paged, RENDERED_PAGE.get(paged)) else 0
            if isinstance(value, list) and (len(value) > limit or off):
                render[key] = _slice(key, value, limit, off)
                info.setdefault(f"_render.{key}", len(value))
    return info


def _entry(command: str) -> tuple[str, str, tuple[str, str] | None, str]:
    """`COMMANDS` row of ``command``. ``next --lane L`` is `next` for that lane: the calls it
    names keep the lane, and its answer (one item and a note) pages no list."""
    base, _, lane = command.partition(" --lane ")
    cli, tool, narrow, paged = COMMANDS[base]
    if not lane:
        return cli, tool, narrow, paged
    return f"{cli} --lane {shlex.quote(lane)}", f"{tool} lane={lane}", narrow, ""


def _page_calls(command: str, row: int) -> tuple[str, str]:
    """The exact CLI and MCP calls that resume a cut list at ``row``."""
    return f"{_entry(command)[0]} --cursor {row}", f"{_entry(command)[1]} cursor={row}"


def _calls(command: str, flag: str, mcp_arg: str) -> str:
    cli, tool, _n, _p = _entry(command)
    return f"`{cli} {flag}` (MCP: `{tool} {mcp_arg}`)"


def footer(
    command: str, info: dict[str, Any], rows: int, cut_chars: int, full_refused: bool
) -> str:
    """The one-line footer naming the exact next calls."""
    _cli, _tool, narrow, paged = _entry(command)
    parts: list[str] = []
    lists = {
        k: v
        for k, v in info.items()
        if k not in NOT_A_LIST and k != "task_rows_shown" and not k.startswith("_render.")
    }
    if lists:
        parts.append("lists cut: " + ", ".join(f"{k} {v} total" for k, v in sorted(lists.items())))
    if info.get("fields_cut"):
        parts.append(f"{info['fields_cut']} nested field(s) cut")
    if info.get("clipped_strings"):
        parts.append(f"long values cut to {info['clipped_strings']} characters")
    if cut_chars:
        parts.append(f"{cut_chars} more character(s) not shown")
    msg = "; ".join(parts) or "output cut"
    nxt: list[str] = []
    if info.get("next_row") is not None and paged:
        nxt.append(
            "next page " + "`{}` (MCP: `{}`)".format(*_page_calls(command, info["next_row"]))
        )
    if narrow:
        nxt.append("narrow with " + _calls(command, narrow[0], narrow[1]))
    if full_refused:
        nxt.append("--full is off here ([output].allow_full = false)")
    else:
        nxt.append("everything: " + _calls(command, "--full", "full=true"))
    return f"[{msg} (cap [output].max_rows={rows}). " + "; ".join(nxt) + "]"


def _next_field(command: str, info: dict[str, Any], full_refused: bool) -> dict[str, str]:
    cli, tool, narrow, paged = _entry(command)
    out: dict[str, str] = {}
    if info.get("next_row") is not None and paged:
        out["cursor_cli"], out["cursor_mcp"] = _page_calls(command, info["next_row"])
    if narrow:
        out["narrow_cli"], out["narrow_mcp"] = f"{cli} {narrow[0]}", f"{tool} {narrow[1]}"
    if not full_refused:
        out["full_cli"], out["full_mcp"] = f"{cli} --full", f"{tool} full=true"
    return out


def _copy_data(original: dict[str, Any]) -> dict[str, Any]:
    work = copy.deepcopy({k: v for k, v in original.items() if k != "_render"})
    if "_render" in original:
        work["_render"] = {
            k: (list(v) if isinstance(v, list) else v) for k, v in original["_render"].items()
        }
    return work


def _body_size(work: dict[str, Any]) -> int:
    return _size({k: v for k, v in work.items() if k != "text"})


def _marker(value: Any) -> Any:
    """What stands for a cut nested field: same type, so a reader's `row[field]` still works."""
    note = f"{len(value)} entries cut"
    return {"cut": note} if isinstance(value, dict) else [note]


def _cut_fields(work: dict[str, Any], info: dict[str, int], budget: int) -> None:
    """The nested fields of list rows that alone take over 1/FIELD_SHARE of ``budget`` become
    a marker: a row's identity is worth more than its recorded evidence."""
    limit = max(MIN_CLIP, budget // FIELD_SHARE)
    for key, rows in work.items():
        if key.startswith("_") or not isinstance(rows, list):
            continue
        for row in rows:
            for field, value in row.items() if isinstance(row, dict) else ():
                if isinstance(value, (dict, list)) and len(json.dumps(value, default=str)) > limit:
                    row[field] = _marker(value)
                    info["fields_cut"] = info.get("fields_cut", 0) + 1


def _fit(
    command: str, original: dict[str, Any], rows: int, start: int, budget: int
) -> tuple[dict[str, Any], int, dict[str, int]]:
    """A cut copy of ``original``: a row's heavy nested fields go first, then rows halve
    (down to 1) until the body fits ``budget`` characters (0: no character cap). Returns
    (data, rows shown, what was cut). The board's nested tasks are its rows: `_cut_board`
    cuts those."""
    limit = rows
    while True:
        work = _copy_data(original)
        info = _cut_lists(command, work, limit, start)
        if budget and command != "board" and _body_size(work) > budget:
            _cut_fields(work, info, budget)
        if not budget or _body_size(work) <= budget:
            return work, limit, info
        if limit <= 1:
            return _clip_rows(work, info, budget), limit, info
        limit = max(1, limit // 2)


def _clip_strings(value: Any, n: int) -> Any:
    """``value`` with every string longer than ``n`` characters cut (a marker ends it)."""
    if isinstance(value, str):
        return value if len(value) <= n else value[:n] + " [...]"
    if isinstance(value, list):
        return [_clip_strings(v, n) for v in value]
    if isinstance(value, dict):
        return {k: _clip_strings(v, n) for k, v in value.items()}
    return value


def _mark_oversized(clipped: dict[str, Any], info: dict[str, int], budget: int) -> None:
    """Last resort, still over ``budget`` (a row of hundreds of fields): the largest lists
    are replaced by one marker row each, until it fits; a list that fits keeps its rows."""
    lists = [k for k, v in clipped.items() if isinstance(v, list) and v and not k.startswith("_")]
    for k in sorted(lists, key=lambda k: -len(json.dumps(clipped[k], default=str))):
        if _body_size(clipped) <= budget:
            break
        info.setdefault(k, len(clipped[k]))
        clipped[k] = [{"cut": f"{info[k]} row(s) too large for [output].max_chars"}]


def _clip_rows(work: dict[str, Any], info: dict[str, int], budget: int) -> dict[str, Any]:
    """One row (or scalar) that alone passes ``budget``: long strings in the lists are cut,
    halving, down to 80 characters. Bounded: one pass per bit of ``budget``."""
    n = max(MIN_CLIP, budget)
    for _ in range(budget.bit_length()):
        n = max(MIN_CLIP, n // 2)
        clipped = {
            k: (v if k.startswith("_") or k == "text" else _clip_strings(v, n))
            for k, v in work.items()
        }
        if _body_size(clipped) <= budget or n == MIN_CLIP:
            break
    if clipped != work:
        info["clipped_strings"] = n
    _mark_oversized(clipped, info, budget)
    return clipped


def _affordable(text: str, budget: int, weight: int) -> int:
    """How many leading characters of ``text`` fit ``budget``, a non-ASCII one costing
    ``weight``. One pass, stopping at the budget."""
    if text.isascii() or weight <= 1:
        return min(len(text), budget)
    spent = 0
    for i, char in enumerate(text):
        spent += 1 if char.isascii() else weight
        if spent > budget:
            return i
    return len(text)


def _cut_text(text: Any, budget: int, weight: int = 1) -> tuple[Any, int]:
    """``text`` cut on a line boundary to ``budget``; (text, characters cut)."""
    if not budget or not isinstance(text, str):
        return text, 0
    fits = _affordable(text, budget, weight)
    if fits >= len(text):
        return text, 0
    keep = text.rfind("\n", 0, fits + 1)
    keep = keep if keep > 0 else fits
    return text[:keep], len(text) - keep


def _reasons(rows: Any) -> dict[str, int]:
    """How many rows carry each ``reason`` (the whole list, before the cut)."""
    counts: dict[str, int] = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict):
            counts[str(row.get("reason", ""))] = counts.get(str(row.get("reason", "")), 0) + 1
    return counts


def _limits(cfg: Any) -> tuple[int, int]:
    """(rows, characters) in force. With `allow_full = false` a 0 lifts neither cap."""
    shipped = type(cfg.output)()
    if cfg.output.allow_full:
        return cfg.output.max_rows or 10**9, cfg.output.max_chars
    return cfg.output.max_rows or shipped.max_rows, cfg.output.max_chars or shipped.max_chars


def _conf(repo: Any, cfg: Any) -> Any:
    return cfg if cfg is not None else Config.load(repo)


def apply(
    repo: Any,
    out: Outcome,
    command: str,
    *,
    full: bool = False,
    cursor: int = 0,
    cfg: Any = None,
    wire: bool = False,
) -> Outcome:
    """`out` with the default caps applied (mutated and returned).

    Nothing changes when the answer is within both caps, or when ``full`` lifts them
    (unless `[output].allow_full` is false: then the capped answer says so). A cut answer
    has ``truncated`` and ``next`` in its data, a footer in ``_footer`` (and at the end of
    ``text`` when it has one), and the footer in `take_note()` for the MCP layer."""
    _NOTE.set("")
    cfg = _conf(repo, cfg)
    full_refused = not cfg.output.allow_full  # no call can lift the cap, asked for or not
    if (full and cfg.output.allow_full) or not isinstance(out.data, dict) or not out.data:
        return out
    rows, chars = _limits(cfg)
    # A `--json` body carries the prose AND the rows: each gets half, so the body is held to
    # `max_chars` (an MCP text tool or a terminal shows the text alone, whole).
    budget = max(1, chars // 2) if wire and chars else chars
    start = max(0, int(cursor or 0)) if _entry(command)[3] else 0
    work, limit, info = _fit(command, out.data, rows, start, budget if chars else 0)
    text, cut_chars = _cut_text(
        work.get("text"), budget if chars else 0, cfg.output.non_ascii_weight
    )
    reasons = _reasons(out.data.get("blocked")) if "blocked" in info else {}
    if not info and not cut_chars and not (start and _entry(command)[3]):
        return out
    out.data = _annotate(work, command, info, limit, (text, cut_chars), full_refused)
    if reasons:  # `next`: the cut blocked list is still counted by reason, as `bound_next` does
        out.data["truncated"]["blocked_by_reason"] = reasons
    return out


def _annotate(
    work: dict[str, Any],
    command: str,
    info: dict[str, int],
    limit: int,
    cut: tuple[Any, int],
    full_refused: bool,
) -> dict[str, Any]:
    """``work`` with the cut text, `truncated`, `next` and the footer written into it."""
    text, cut_chars = cut
    note = footer(command, info, limit, cut_chars, full_refused)
    old = work.get("truncated") if isinstance(work.get("truncated"), dict) else {}
    work["truncated"] = {
        **old,
        "lists": {
            k: v for k, v in info.items() if k not in NOT_A_LIST and not k.startswith("_render.")
        },
        "shown": limit,
        "chars_cut": cut_chars,
        **({"clipped_to": info["clipped_strings"]} if "clipped_strings" in info else {}),
        **({"fields_cut": info["fields_cut"]} if "fields_cut" in info else {}),
    }
    work["truncated"].pop("all", None)
    if not full_refused:
        work["truncated"]["all"] = (
            f"`{_entry(command)[0]} --full` (MCP: `{_entry(command)[1]} full=true`) "
            "lists every entry"
        )
    work["next"] = _next_field(command, info, full_refused)
    work["_footer"] = note
    if isinstance(text, str):
        work["text"] = f"{text}\n\n{note}"
    _NOTE.set(note)
    return work


def from_args(repo: Any, out: Outcome, command: str, a: Any, *, wire: bool = False) -> Outcome:
    """`apply` for a call whose arguments are ``a`` (a dict of the MCP/declared arguments, or
    an argparse namespace): reads its ``full`` and ``cursor``."""
    get = a.get if isinstance(a, dict) else (lambda k, d=None: getattr(a, k, d))
    try:
        cursor = int(get("cursor", 0) or 0)
    except (TypeError, ValueError):
        cursor = 0
    return apply(repo, out, command, full=bool(get("full", False)), cursor=cursor, wire=wire)


def status_args(repo: Any, a: Any) -> dict[str, Any]:
    """What `status` itself is asked for: always the whole body, which `apply` then cuts
    (the newest completions kept) the way it cuts every other command."""
    return {"full": True}


def cut_text(
    repo: Any, text: str, command: str, a: Any, *, cfg: Any = None, footer: str = ""
) -> str:
    """A prose answer built by the CLI itself (`status`), cut as `apply` cuts a `text`;
    ``footer`` (the row cut's, from `footer_of`) is appended when the text itself fits."""
    out = apply(repo, Outcome(command, {"text": text}), command, cfg=cfg, **_full_cursor(a))
    cut = out.data["text"]
    return f"{cut}\n\n{footer}" if footer and cut == text else cut


def _full_cursor(a: Any) -> dict[str, Any]:
    get = a.get if isinstance(a, dict) else (lambda k, d=None: getattr(a, k, d))
    return {"full": bool(get("full", False)), "cursor": 0}


def footer_of(out: Outcome) -> str:
    """The footer a CLI prose renderer prints after its lines (empty when nothing was cut)."""
    return str(out.data.get("_footer", "")) if isinstance(out.data, dict) else ""
