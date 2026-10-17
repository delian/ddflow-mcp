"""What `ddflow update` prints: the fields it changed, and what a replacing flag took away."""

from __future__ import annotations

from ..config import csv_list


def csv_or_none(v: str | None) -> list[str] | None:
    """`None` stays None (leave alone); a string is a comma list."""
    return None if v is None else csv_list(v)


def update_message(item: str, data: dict) -> str:
    msg = f"{item} updated: {', '.join(data['changed'])}"
    if data.get("branch"):
        msg += f"\n  now bound to {data['worktree']} on {data['branch']}"
    if "globs" in data["fields"]:
        # `--globs` REPLACES the list. Say what that took away: an agent widening its
        # claim with only the new paths otherwise drops the old ones from every
        # conflict check without a word (Bd8038b08a1).
        msg += f"\n  globs now: {', '.join(data['fields']['globs']) or '(none)'}"
        if data["globs_dropped"]:
            msg += (
                f"\n  dropped:   {', '.join(data['globs_dropped'])}  (--globs replaces "
                f"the list; pass every glob, old and new, to keep them)"
            )
    if data.get("needs_dropped"):
        msg += (
            f"\n  needs now: {', '.join(data['fields']['needs']) or '(none)'}"
            f"\n  dropped:   {', '.join(data['needs_dropped'])}  (--needs replaces the "
            f"list; --add-needs / --remove-needs change it without repeating it)"
        )
    return msg
