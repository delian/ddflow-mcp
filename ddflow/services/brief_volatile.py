"""The volatile status lines, kept OUT of the MCP handshake (B-mcp-instructions-volatile-prefix).

The `initialize` handshake is injected into the model's context on connect, and is the one
thing that must be BYTE-STABLE: two lanes that start minutes apart share a prefix cache,
which is lost as soon as the injected bytes differ between sessions. The shipped
`templates/prompts/mcp_instructions.md` used to render per-session state inside it — the
recoverable-tree count, the queue-loop count, rules drift, companion states, un-served
gate gaps and open setup gaps — so the prefix differed per session and the cache sharing
was lost.

Those lines are STATE, not the contract. They render here, as one renderer the brief
reads (`ddflow brief` / `ddflow_brief`), where the words are expected to change per
session. The handshake template renders NONE of them, so its bytes are the static
contract plus config, and only the brief's first lines move when the state does.
"""

from __future__ import annotations

from typing import Any


def _rules_line(v: dict[str, Any]) -> str:
    details = [
        str(r["detail"])
        for r in v.get("rules_drift") or []
        if isinstance(r, dict) and r.get("detail")
    ]
    if not details:  # no drift: the brief omits the warning, as the template's `{% if %}` did
        return ""
    text = " ".join(details)
    return (
        f"This project's rules file needs the operator: {text} Ask the operator before "
        "fixing it; if they agree, `ddflow_setup` replaces only the managed block and "
        "leaves everything else untouched."
    )


def _companions_line(v: dict[str, Any]) -> str:
    ids = ", ".join(str(c["id"]) for c in v["actionable_companions"] if isinstance(c, dict))
    return (
        'Companions not wired up ("not checked" is not "missing"; '
        f"`ddflow_companions` probes, has install commands): {ids}. Propose to the "
        "operator; install nothing unasked."
    )


def _gaps_line(v: dict[str, Any]) -> str:
    gaps = ", ".join(f"`{g}`" for g in v["gate_gaps"])
    return (
        f"Gates with no tool behind them: {gaps}; judgement you do yourself is fine "
        "and expected, but record `unavailable` for a tool or reviewer that could not "
        "run, never imply one ran."
    )


def _import_line(v: dict[str, Any]) -> str:
    return (
        f"The import was never finished ({v.get('imported_no_globs', 0)} task(s) "
        f"without globs, {v.get('imported_shipped_drift', 0)} phase(s) claiming shipped "
        "over open work): `ddflow_import_verify` before handing out imported work."
    )


def volatile_lines(v: dict[str, Any]) -> str:
    """The volatile status lines for one fold's instruction vars (``v``): the exact lines
    `mcp_instructions.md` used to render inside the handshake, moved out so the handshake
    stays byte-stable. The wording and remedy calls are the same ones the handshake used.
    """

    parts: list[str] = []
    if v.get("recoverable"):
        parts.append(
            f"{v['recoverable']} crashed worktree(s) may hold work found nowhere else: "
            "`ddflow_recover` BEFORE new work."
        )
    if v.get("loops"):
        parts.append(
            f"{v['loops']} queue loop finding(s): `ddflow_loops` names each and its remedy."
        )
    rules = _rules_line(v)
    if rules:
        parts.append(rules)
    if v.get("actionable_companions"):
        parts.append(_companions_line(v))
    if v.get("gate_gaps"):
        parts.append(_gaps_line(v))
    for t in v.get("setup_todo") or []:
        parts.append(f"Setup still needed: * {t}")
    if v.get("imported_no_globs") or v.get("imported_shipped_drift"):
        parts.append(_import_line(v))
    if v.get("queue_is_empty") and v.get("importable"):
        parts.append(
            f"The queue is empty but {v['importable']} history file(s) were found: "
            "`ddflow_import` (writes nothing), then put it to the operator."
        )
    return "\n".join(parts)
