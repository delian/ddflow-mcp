"""How `split` and `resolve` read on the command line. The operations are `api.items`; the
executor (`surfaces/cliexec.py`) prints what these return. (`phase add` and `task add` keep
their commands: they answer the duplicate check on a terminal, `surfaces/dedupe_flags.py`.)
It imports nothing that loads the api (tests/test_declared_commands.py)."""

from __future__ import annotations

import shlex


def split_text(out, a) -> str:
    created = out.data["created"]
    tail = ""
    if out.data["inherited_globs"]:
        tail = (
            f"\n  Give each its own --globs with `ddflow update <id> --globs ...` if they "
            f"write different files — they inherited {a['id']}'s, so they cannot run in "
            f"parallel until they differ."
        )
    return (
        f"{a['id']} split into {len(created)} sub-task(s): {', '.join(created)}\n"
        f"  It keeps its id and history, and now completes when they do.{tail}"
    )


def resolved_text(out, a) -> str:
    """What `resolve` kept, and -- for each definition it did not -- how to get it back."""
    d = out.data
    lines = []
    if d["kept_definition"]:
        k = d["kept_definition"]
        lines.append(f"{d['id']}: kept definition {k['event']} by {k['agent']} ({k['title']!r})")
    if d["kept_holder"]:
        lines.append(f"{d['id']}: {d['kept_holder']} keeps the lease")
        lines += [f"  released {h}'s claim" for h in d["released"]]
    refiled = d["refiled"] or [""] * len(d["lost"])
    for lost, new in zip(d["lost"], refiled, strict=True):
        if new:
            lines.append(f"  re-filed {lost['agent']}'s definition {lost['title']!r} as {new}")
            continue
        body = f" --body {shlex.quote(lost['body'])}" if lost["body"] else ""
        lines.append(f"  NOT kept: {lost['event']} by {lost['agent']}")
        lines.append(f"    title {lost['title']!r}")
        lines += [f"    | {ln}" for ln in (lost["body"] or "").splitlines()]
        lines.append(
            f"    re-file it under a new id (or resolve with --refile-as <new-id>):\n"
            f"    ddflow {d['item_kind']} add <new-id> --title {shlex.quote(lost['title'])}{body}"
        )
    return "\n".join(lines)
