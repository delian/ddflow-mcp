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
        if data.get("globs_dropped"):
            msg += (
                f"\n  dropped:   {', '.join(data['globs_dropped'])}  (--globs replaces "
                f"the list; pass every glob, old and new, to keep them)"
            )
    for name in ("needs", "tags"):
        if data.get(f"{name}_dropped"):
            msg += (
                f"\n  {name} now: {', '.join(data['fields'][name]) or '(none)'}"
                f"\n  dropped:   {', '.join(data[name + '_dropped'])}  (--{name} replaces the "
                f"list; --add-{name} / --remove-{name} change it without repeating it)"
            )
    return msg
