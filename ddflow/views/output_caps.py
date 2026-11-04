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

Bounded by construction (r-bounded-everything): the row limit only halves, down to 1, and
the text cut is one slice. The text and the rows are each held to `max_chars`, so a `--json` body that
carries both (board) stays under twice it.
"""

from __future__ import annotations

import copy
import json
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
_BOARD_KEYS = ("phases", "unphased", "critical_path")
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
        info["next_cursor"] = start + len(kept)
    data["phases"] = kept
    for key in ("unphased", "critical_path"):
        if _rows(data.get(key)) > limit:
            info[key] = len(data[key])
            data[key] = data[key][:limit]


def _cut_lists(command: str, data: dict[str, Any], limit: int, start: int) -> dict[str, int]:
    """Cut `data`'s lists in place; return {list: real length} (+ board extras)."""
    info: dict[str, int] = {}
    paged = COMMANDS[command][3]
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
                info["next_cursor"] = off + limit
    by_tag = data.get("by_tag")
    if isinstance(by_tag, dict) and _rows(by_tag.get("values")) > limit:
        info["by_tag"] = len(by_tag["values"])
        data["by_tag"] = {**by_tag, "values": by_tag["values"][:limit]}
    render = data.get("_render")
    if isinstance(render, dict):
        for key, value in list(render.items()):
            if isinstance(value, list) and len(value) > limit:
                off = start if key == paged else 0
                render[key] = _slice(key, value, limit, off)
                info.setdefault(f"_render.{key}", len(value))
    return info


def _calls(command: str, flag: str, mcp_arg: str) -> str:
    cli, tool, _n, _p = COMMANDS[command]
    return f"`{cli} {flag}` (MCP: `{tool} {mcp_arg}`)"


def footer(
    command: str, info: dict[str, Any], rows: int, cut_chars: int, full_refused: bool
) -> str:
    """The one-line footer naming the exact next calls."""
    _cli, _tool, narrow, paged = COMMANDS[command]
    parts: list[str] = []
    lists = {k: v for k, v in info.items() if k not in ("next_cursor", "task_rows_shown")}
    if lists:
        parts.append("lists cut: " + ", ".join(f"{k} {v} total" for k, v in sorted(lists.items())))
    if cut_chars:
        parts.append(f"{cut_chars} more character(s) not shown")
    msg = "; ".join(parts) or "output cut"
    nxt: list[str] = []
    if info.get("next_cursor") is not None and paged:
        nxt.append(
            "next page "
            + _calls(command, f"--cursor {info['next_cursor']}", f"cursor={info['next_cursor']}")
        )
    if narrow:
        nxt.append("narrow with " + _calls(command, narrow[0], narrow[1]))
    if full_refused:
        nxt.append("--full is off here ([output].allow_full = false)")
    else:
        nxt.append("everything: " + _calls(command, "--full", "full=true"))
    return f"[{msg} (cap [output].max_rows={rows}). " + "; ".join(nxt) + "]"


def _next_field(command: str, info: dict[str, Any], full_refused: bool) -> dict[str, str]:
    cli, tool, narrow, paged = COMMANDS[command]
    out: dict[str, str] = {}
    if info.get("next_cursor") is not None and paged:
        n = info["next_cursor"]
        out["cursor_cli"], out["cursor_mcp"] = f"{cli} --cursor {n}", f"{tool} cursor={n}"
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


def _fit(
    command: str, original: dict[str, Any], rows: int, start: int, budget: int
) -> tuple[dict[str, Any], int, dict[str, int]]:
    """A cut copy of ``original``: rows halve (down to 1) until the body fits ``budget``
    characters (0: no character cap). Returns (data, rows shown, what was cut)."""
    limit = rows
    while True:
        work = _copy_data(original)
        info = _cut_lists(command, work, limit, start)
        size = _size({k: v for k, v in work.items() if k != "text"})
        if not budget or limit <= 1 or size <= budget:
            return work, limit, info
        limit = max(1, limit // 2)


def _cut_text(text: Any, budget: int) -> tuple[Any, int]:
    """``text`` cut on a line boundary to ``budget`` characters; (text, characters cut)."""
    if not budget or not isinstance(text, str) or len(text) <= budget:
        return text, 0
    keep = text.rfind("\n", 0, budget)
    keep = keep if keep > 0 else budget
    return text[:keep], len(text) - keep


def _conf(repo: Any, cfg: Any) -> Any:
    return (cfg if cfg is not None else Config.load(repo)).output


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
    conf = _conf(repo, cfg)
    full_refused = bool(full) and not conf.allow_full
    if (full and conf.allow_full) or not isinstance(out.data, dict) or not out.data:
        return out
    # A locked organisation (`allow_full = false`) is not undone by a 0 in the environment.
    rows = conf.max_rows or (50 if not conf.allow_full else 10**9)
    chars = conf.max_chars or (30000 if not conf.allow_full else 0)
    # A `--json` body carries the prose AND the rows: each gets half, so the body is held to
    # `max_chars` (an MCP text tool or a terminal shows the text alone, whole).
    budget = chars // 2 if wire and chars else chars
    start = max(0, int(cursor or 0)) if COMMANDS[command][3] else 0
    work, limit, info = _fit(command, out.data, rows, start, budget if chars else 0)
    text, cut_chars = _cut_text(work.get("text"), budget if chars else 0)
    if not info and not cut_chars and not (start and COMMANDS[command][3]):
        return out
    out.data = _annotate(work, command, info, limit, (text, cut_chars), full_refused)
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
            k: v for k, v in info.items() if k != "next_cursor" and not k.startswith("_render.")
        },
        "shown": limit,
        "chars_cut": cut_chars,
    }
    if not full_refused:
        work["truncated"]["all"] = (
            f"`{COMMANDS[command][0]} --full` (MCP: `{COMMANDS[command][1]} full=true`) "
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


def cut_text(repo: Any, text: str, command: str, a: Any, *, cfg: Any = None) -> str:
    """A prose answer built by the CLI itself (`status`), cut as `apply` cuts a `text`."""
    out = apply(repo, Outcome(command, {"text": text}), command, cfg=cfg, **_full_cursor(a))
    return out.data["text"]


def _full_cursor(a: Any) -> dict[str, Any]:
    get = a.get if isinstance(a, dict) else (lambda k, d=None: getattr(a, k, d))
    return {"full": bool(get("full", False)), "cursor": 0}


def footer_of(out: Outcome) -> str:
    """The footer a CLI prose renderer prints after its lines (empty when nothing was cut)."""
    return str(out.data.get("_footer", "")) if isinstance(out.data, dict) else ""
