"""The words of an add-time duplicate refusal (decision D-no-duplicates; bug Bf29855c414)."""

from __future__ import annotations

from typing import Any


def reason(rows_: list[dict[str, Any]], floor: float = 0.0) -> str:
    """The refusal text. A record listed only because the new text names its id (it scores
    under ``floor``) is shown apart, as information: it is not what makes the add refuse."""
    asking = [r for r in rows_ if not _only_named(r, floor)] or rows_
    named = [r for r in rows_ if r not in asking]
    lines = ["refused: possible duplicate. It reads like:"]
    lines += [_line(r) for r in asking]
    if named:
        lines.append("Also named by the text (information only, not why this was refused):")
        lines += [_line(r) for r in named]
    top = asking[0]["id"]
    lines.append(
        f"Answer it: new (it is a different record), extends {top} / duplicate_of {top} "
        f"(the same thing -- added to {top} while it is open and unclaimed, else filed as "
        f"a new record linked to it), or related {top} (linked both ways). A bug that a "
        f"task will fix is filed against that task (--item)."
    )
    return "\n".join(lines)


def _only_named(r: dict[str, Any], floor: float) -> bool:
    return "named" in r["flags"] and "identical" not in r["flags"] and r["score"] < floor


def _line(r: dict[str, Any]) -> str:
    why = ", ".join(r["flags"]) or ", ".join(r["shared"])
    return (
        f"  {r['id']} ({r['kind']}, {r['state']}, score {r['score']:.2f}): "
        f"{r['title'][:100]}" + (f"  [{why}]" if why else "")
    )
