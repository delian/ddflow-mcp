"""Projection helpers for ``show`` record blocks and the recovery bands."""

from __future__ import annotations

from ...core import progress as PR
from ...core.textcut import clip


def _approx_tokens(text: str) -> int:
    """Cheap token estimate: ~4 chars per token. Good enough to enforce a budget, and
    it costs no dependency — a tokenizer import here would make the brief the slowest
    command in the tool."""
    return max(1, len(text) // 4)


def _score(x: dict) -> str:
    return f", score {x['score']:.2f}" if isinstance(x.get("score"), int | float) else ""


def _clip(text: str, cap: int) -> str:
    return clip((text or "").strip(), cap, keep=cap - 1, marker="…")


def _ref(x: dict, key: str) -> str:
    """`B2 [bug open] title`: a linked record, named."""
    tag = " ".join(t for t in (x.get("kind"), x.get("state")) if t)
    return " ".join(p for p in (f"{x[key]}", f"[{tag}]" if tag else "", x.get("title") or "") if p)


def addenda_lines(a: dict) -> list[str]:
    """`show`'s block for one record: what was added to it (verbatim, who, when, score)
    and the links in both directions."""
    out: list[str] = []
    if a["additions"]:
        out.append(f"  additions ({len(a['additions'])}):")
        for x in a["additions"]:
            out.append(f"    {x['at']} by {x['who']}{_score(x)}")
            out += [f"      | {ln}" for ln in (x["text"] or "").splitlines()]
    if a["links"]:
        out.append("  links:")
        out += [
            f"    {x['relation']} {_ref(x, 'target')} (by {x['by']}, {x['at']}{_score(x)})"
            + _reason(x)
            for x in a["links"]
        ]
    if a.get("dismissals"):
        out.append("  dismissed as distinct:")
        out += [
            f"    {_ref(x, 'target')} (by {x['by']}, {x['at']})" + _reason(x)
            for x in a["dismissals"]
        ]
    if a["linked_from"]:
        out.append("  linked from:")
        out += [
            f"    {_ref(x, 'record')} {x['relation']} this (by {x['by']}, {x['at']}{_score(x)})"
            + _reason(x)
            for x in a["linked_from"]
        ]
    return out


def _reason(x: dict) -> str:
    """A link's `--reason`, as a trailing ` -- <why>`; "" when none was given."""
    why = (x.get("reason") or "").strip()
    return f" -- {why}" if why else ""


new_reports_line = PR.new_reports_line


#: At most this many reports are quoted in a brief, each at most this long: the brief has
#: a budget, and the rest is one `show` away.
_REPORTS_SHOWN = 5
_REPORT_CAP = 400


def new_reports_block(item: str, rep: dict) -> str:
    """The brief's lead for a claimed item: the count, then the additions verbatim and the
    records filed against it (because it was claimed or closed)."""
    if not rep["count"]:
        return ""
    s = "" if rep["count"] == 1 else "s"
    lines = [f"## {rep['count']} new report{s} on your item since you claimed", ""]
    entries = [(x["at"], "add", x) for x in rep["additions"]] + [
        (x["at"], "link", x) for x in rep["linked_from"]
    ]
    entries.sort(key=lambda e: e[0])
    for _at, kind, x in entries[:_REPORTS_SHOWN]:
        if kind == "add":
            lines.append(f"- added by {x['who']}{_score(x)}: {_clip(x['text'], _REPORT_CAP)}")
        else:
            lines.append(
                f"- {_ref(x, 'record')} {x['relation']} {item} (by {x['by']}{_score(x)}): "
                f"{_clip(x['text'], _REPORT_CAP)}"
            )
    if len(entries) > _REPORTS_SHOWN:
        lines.append(f"- _{len(entries) - _REPORTS_SHOWN} more: `ddflow show {item}`._")
    return "\n".join(lines) + "\n\n"


#: How many recovery entries of EACH kind the brief spells out; the rest are counted. Per
#: kind, so a run of salvageable trees cannot push a RUNNING item nobody holds, or a tree
#: that could not be measured, off the list (rubber_duck on B20e103326b).
_RECOVERY_SHOWN = 5
_HOLDING, _UNMEASURED, _CAPPED, _UNHELD = (
    "holding work",
    "could not be measured",
    "past the measurement cap",
    "RUNNING with nobody on it",
)


def recovery_band(r) -> str | None:
    """Which band of the brief a recovery entry is named in, in this order: a tree
    holding work, one that COULD NOT be measured (treat as work), an item RUNNING with
    nobody on it; None for a leftover with nothing to salvage, which is counted rather
    than listed (B20e103326b). A tree left unmeasured ONLY because the view hit its
    measurement cap is its own band: still "may hold work" for `recover`/`status`, but the
    brief counts it quietly with the other leftovers instead of raising the alarm over it
    (Bfb4b6306d1). `recover` and `status` count every listed band as "may
    contain work" (B3f8c406fea): a truthy `salvageable` dropped the unmeasured and the
    unheld. `doctor` makes the tree bands a problem and leaves a RUNNING item nobody
    holds a note, since `next` offers it to resume."""
    if r.kind == "stale_running":  # never measured: its `salvageable` says nothing
        return _UNHELD
    if r.salvageable:
        return _HOLDING
    if r.salvageable is None:
        return _CAPPED if r.budget_capped else _UNMEASURED
    return None


#: The name tests/test_brief_recovery_all.py imports; one function, not a copy.
_recovery_band = recovery_band


def may_hold_work(r) -> bool:
    """A recovery entry that may hold work nobody else has: every band but the quiet one."""
    return recovery_band(r) is not None
