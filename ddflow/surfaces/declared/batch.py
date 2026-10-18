"""`batch` and `task fold`, declared once (DX-task-fold): many edits as one transaction.

One engine (`api/batch.py`) behind both: `fold` is a batch whose last step abandons the
duplicate. A new family file, found by convention (`declared.family_modules`); only the
parser registration (`parsers/queue.py`) names it.
"""

from __future__ import annotations

import importlib
import json
import sys

from ...core import outcome as O
from ..registry import CliPolicy, Command, Param

OK = O.OK


def _engine():
    """`api/batch.py`, loaded at call time: the tool table must import without the api."""
    return importlib.import_module("ddflow.api.batch")


def _entries(a: dict) -> list | O.Outcome:
    """The batch's entries from ``edits`` (JSON text or a list) or ``file`` (a path, `-` stdin)."""
    kind = "item.batch"
    raw = a.get("edits")
    if not raw and a.get("file"):
        path = a["file"]
        try:
            if path == "-":
                raw = sys.stdin.read()
            else:
                with open(path, encoding="utf-8") as fh:
                    raw = fh.read()
        except OSError as err:
            return O.failed(kind, f"cannot read {path}: {err}")
    if not raw:
        return O.failed(kind, "give the edits: a JSON array of {id, field: value, ...} objects")
    try:
        entries = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError as err:
        return O.failed(kind, f"the edits are not valid JSON: {err}")
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        return O.failed(kind, "the edits must be a JSON array of objects, each with an 'id'")
    return entries


def _batch(repo, a, agent):
    entries = _entries(a)
    if isinstance(entries, O.Outcome):
        return entries
    return _engine().apply(repo, entries, dry_run=bool(a.get("dry_run")), agent=agent)


def _fold(repo, a, agent):
    return _engine().fold(
        repo,
        a["dup"],
        a["keep"],
        reason=a.get("reason") or "",
        dry_run=bool(a.get("dry_run")),
        agent=agent,
    )


def _rename(repo, a, agent):
    return importlib.import_module("ddflow.api.rename").rename(
        repo,
        a["old"],
        a["new"],
        parent=a.get("parent") or "",
        reason=a.get("reason") or "",
        dry_run=bool(a.get("dry_run")),
        agent=agent,
    )


def rename_text(out, a) -> str:
    d = out.data
    head = f"{d['old']} renamed to {d['new']} (under {d['parent'] or 'no phase'})" + (
        " (dry run: nothing recorded)" if d["dry_run"] else ""
    )
    moved = f"\n  re-pointed: {', '.join(d['repointed'])}" if d["repointed"] else ""
    return head + moved


def batch_text(out, a) -> str:
    d = out.data
    verb = "would change" if d["dry_run"] else "changed"
    lines = [
        f"{verb} {len(d['edits'])} item(s)"
        + (" (dry run: nothing recorded)" if d["dry_run"] else "")
    ]
    for e in d["edits"]:
        lines.append(f"  {e['id']}: {', '.join(e['changed'])}")
    if d["unchanged"]:
        lines.append(f"  unchanged: {', '.join(d['unchanged'])}")
    if d["abandoned"]:
        lines.append(f"  abandoned: {', '.join(d['abandoned'])}")
    return "\n".join(lines)


def fold_text(out, a) -> str:
    d = out.data
    head = f"{d['dup']} folded into {d['keep']}" + (
        " (dry run: nothing recorded)" if d["dry_run"] else ""
    )
    moved = f"\n  re-pointed: {', '.join(d['repointed'])}" if d["repointed"] else ""
    return head + moved + "\n  " + batch_text(out, a).replace("\n", "\n  ")


_DRY = Param(
    "dry_run",
    type="boolean",
    help="Plan and show the diff; record nothing.",
    cli_help="plan and show what would change; record nothing",
)

COMMANDS: tuple[Command, ...] = (
    Command(
        path=("batch",),
        summary="apply many item edits in one all-or-nothing transaction",
        tool="ddflow_batch",
        tier="all",
        help_group="Shaping the work",
        description="Apply many field edits in ONE locked transaction: a JSON array of "
        "{id, needs, add_needs, remove_needs, tags, add_tags, remove_tags, globs, add_globs, "
        "remove_globs, priority, title, body, append_body, line, resources} objects, the fields "
        "of ddflow_update. All or nothing: any refusal, or a dependency cycle the batch would "
        "create, records nothing. `dry_run` returns the diff without recording.",
        call=_batch,
        payload=("dry_run", "edits", "unchanged", "abandoned"),
        render=batch_text,
        cli=CliPolicy(shown=(OK,)),
        params=(
            Param(
                "edits",
                help="JSON array of edit objects, each with an 'id'.",
                cli_help="the edits as JSON text (or use --file)",
                default="",
            ),
            Param(
                "file",
                help="",
                cli_help="read the edits from this JSON file ('-' for stdin)",
                cli_only=True,
                default="",
            ),
            _DRY,
        ),
        flag_exempt={"--file": "CLI only: over MCP the edits are the `edits` argument"},
    ),
    Command(
        path=("task", "fold"),
        summary="fold a duplicate task into a survivor, re-pointing its dependents",
        tool="ddflow_task_fold",
        tier="all",
        help_group="Shaping the work",
        description="Fold the duplicate `dup` into the survivor `keep` in one transaction: dup's "
        "body is appended to keep's, its globs unioned in, every live item that needs dup now "
        "needs keep, and dup is abandoned with the reason. Refused when dup is claimed, either "
        "is finished, or the re-pointing would create a cycle. `dry_run` lists what it would do.",
        call=_fold,
        payload=("dup", "keep", "repointed", "dry_run", "edits", "unchanged", "abandoned"),
        render=fold_text,
        cli=CliPolicy(shown=(OK,)),
        params=(
            Param("dup", help="The duplicate to abandon.", cli_help="", positional=True),
            Param("keep", help="The survivor.", cli_help="", positional=True),
            Param("reason", help="Why (recorded on the abandonment).", cli_help="", default=""),
            _DRY,
        ),
    ),
    Command(
        path=("rename",),
        summary="move an item to a new id (and phase), re-pointing every needs edge",
        tool="ddflow_rename",
        tier="all",
        help_group="Shaping the work",
        description="Re-file the unclaimed task `old` as `new` (under `parent` when given): "
        "title, body, globs, resources, tags, priority and needs are kept, every live item "
        "that needs old now needs new, and old stays as an abandoned tombstone naming new. "
        "One locked step. Refused when old is claimed, finished, a phase or has sub-tasks, or "
        "new is taken. `dry_run` lists what it would do.",
        call=_rename,
        payload=("old", "new", "parent", "repointed", "dry_run"),
        render=rename_text,
        cli=CliPolicy(shown=(OK,)),
        params=(
            Param("old", help="The task to rename.", cli_help="", positional=True),
            Param("new", help="Its new id.", cli_help="", positional=True),
            Param("parent", help="Move it under this phase or task.", cli_help="", default=""),
            Param("reason", help="Why (recorded on the tombstone).", cli_help="", default=""),
            _DRY,
        ),
    ),
)
