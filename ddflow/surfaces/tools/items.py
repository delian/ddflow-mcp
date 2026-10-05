"""MCP tools: one item: show, update, abandon, remove, release, wait, block.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ._common import (
    MCP_WAIT_DEFAULT_S,
    MCP_WAIT_MAX_S,
    _api,
    _list_or_none,
    _wait_timeout,
)

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_show": {
        "description": (
            "Everything known about one phase, task or bug (a bug id works too): state, dependencies, declared "
            "globs, the lease and who holds it, the worktree path you can cd to, and "
            "every gate's outcome with its evidence. Use it to check your own work "
            "before calling ddflow_complete."
        ),
        "properties": {"id": ("string", "Item id (phase, task) or bug id.", True)},
        "api": lambda repo, a, agent: _api().show(repo, a["id"], agent=agent),
        "payload": "item",
    },
    "ddflow_update": {
        "description": (
            "Change an item's fields. MOST IMPORTANT USE: widening `globs` when your "
            "work turns out to touch files outside what you claimed. Do that BEFORE "
            "writing them: the conflict detector and commit hook work from the declared "
            "globs, so an undeclared file is unprotected and the commit is refused."
        ),
        "properties": {
            "id": ("string", "Item id.", True),
            "globs": (
                "string",
                "Path globs this item writes, comma-separated or a JSON array. REPLACES "
                "the list (a claimed item's lease too); the result names what it dropped.",
                False,
            ),
            "needs": (
                "string",
                "Comma-separated ids it depends on. Pass an EMPTY string to clear them "
                "— that is how you break a dependency cycle the loop detector found.",
                False,
            ),
            "title": ("string", "New title.", False),
            "body": ("string", "New detail / acceptance criteria.", False),
            "tags": ("string", "Comma-separated tags.", False),
            "priority": ("integer", "Lower is offered first (default 100).", False),
            "line": ("string", "Move it to another release line.", False),
            "resources": (
                "string",
                "Physical resources the work RUNS on, beside its files: 'gpu:4,vllm-fleet'. `next` withholds and `claim` refuses while live claims use up the capacity ([schedule] resources). Declare for a GPU job, model server or long run. Empty clears.",
                False,
            ),
            "worktree": (
                "string",
                "Rebind the item and your live lease to this linked worktree (absolute, or repo-relative) and its branch: the way out of a wrong-tree binding, since re-claiming keeps the recorded tree and merge lands that tree's branch.",
                False,
            ),
        },
        # Typed, and the argv lambda that used to sit here is GONE rather than kept
        # "in case". The `api` branch runs first, so it was unreachable -- a second
        # encoding of the same operation that no test could have caught drifting,
        # because nothing executed it. That is the duplicate-then-drift shape, and
        # keeping a dead fallback is how it starts.
        #
        # `None` means leave alone and `[]` means clear, which is what
        # `_opt(clearable=True)` existed to rebuild after argv flattened both to an
        # empty string. Here the distinction is simply the values themselves.
        "api": lambda repo, a, agent: _api().update(
            repo,
            a["id"],
            _api().ItemEdit(
                title=a.get("title"),
                body=a.get("body"),
                needs=_list_or_none(a, "needs"),
                # Raw, for the api's single read: split on commas here first, a JSON
                # array (or a glob holding a comma inside one) was lost (roborev).
                globs=(
                    None
                    if a.get("globs") is None
                    else list(a["globs"])
                    if isinstance(a["globs"], list)
                    else [a["globs"]]
                ),
                tags=_list_or_none(a, "tags"),
                priority=a.get("priority"),
                line=a.get("line"),
                resources=_list_or_none(a, "resources"),
                worktree=a.get("worktree"),
            ),
            agent=agent,
        ),
    },
    "ddflow_abandon": {
        "description": (
            "Stop work on an item without completing it, with a reason. Use when a "
            "task turns out to be unnecessary or impossible. DIFFERENT from blocking: "
            "a blocked item is waiting and will resume; an abandoned one will not, and "
            "so it stops holding its phase open — which an unfinished task otherwise "
            "does forever, since nothing can ever finish it."
        ),
        "properties": {
            "id": ("string", "Item id.", True),
            "reason": ("string", "Why it is being dropped.", True),
            "force": (
                "boolean",
                "Abandon although a sub-task is still open. Those sub-tasks are NOT abandoned with it: decide about each, or they sit under a parent nobody will finish.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().abandon(
            repo, a["id"], reason=a.get("reason", "") or "", force=bool(a.get("force")), agent=agent
        ),
        "payload": ("id", "reason"),
    },
    "ddflow_remove": {
        "description": (
            "Take an item out of the queue. The log is append-only, so this RECORDS a "
            "removal rather than erasing anything — the item stays in the history and "
            "in replay, which keeps the record honest about work that was planned and "
            "then dropped. Refuses if another item depends on it."
        ),
        "properties": {
            "id": ("string", "Item id.", True),
            "reason": ("string", "Why.", False),
            "force": (
                "boolean",
                "Remove although it still has open children or dependents. Both leave the queue inconsistent in a way the scheduler reports; read the refusal before overriding.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().remove_item(
            repo, a["id"], reason=a.get("reason", "") or "", force=bool(a.get("force")), agent=agent
        ),
        "payload": ("id",),
    },
    "ddflow_release": {
        "description": (
            "Give up a lease without completing the item — when you are handing off, "
            "stopping, or recovering someone else's abandoned work after inspecting it. "
            "The note is recorded in the log and is often the only lasting explanation "
            "of why a claim was broken."
        ),
        "properties": {
            "id": ("string", "Item id.", True),
            "note": ("string", "Why you are releasing it.", False),
        },
        "api": lambda repo, a, agent: _api().release_item(
            repo, a["id"], note=a.get("note", "") or "", agent=agent
        ),
        "payload": ("released", "woke"),
    },
    "ddflow_wait": {
        "description": (
            "Sleep until an item can be claimed (or, with no item, until anything is ready) and return the moment it can. Use it instead of polling or asking the operator when a claim was refused for another agent's lease or overlapping files, or an unfinished dependency someone is working on. Exit 0: claim now (it says what freed it). Exit 2: deadline passed, or waiting cannot help (done, cycle, operator hold, a dependency nobody works on) and it says what to do. The holder is told you wait."
        ),
        "properties": {
            "item": ("string", "The item to wait for (default: anything ready).", False),
            "phase": ("string", "With no item: anything ready in this phase.", False),
            "kind": ("string", "'task' (default) or 'phase'.", False),
            "globs": (
                "string",
                "With item: the globs you will claim with (comma-separated or a JSON "
                "array), so ready means that claim will not be refused for them.",
                False,
            ),
            "timeout": (
                "number",
                f"Seconds to wait (default {MCP_WAIT_DEFAULT_S}, at most {MCP_WAIT_MAX_S}: "
                f"a client may time a tool call out, so call again to keep waiting). 0 asks "
                f"without waiting.",
                False,
            ),
            "poll": ("number", "Seconds between log checks (default 2).", False),
        },
        "api": lambda repo, a, agent: _api().wait_item(
            repo,
            item=a.get("item", "") or "",
            phase=a.get("phase", "") or "",
            kind=a.get("kind") or _api().DEFAULT_NEXT_KIND,
            globs=a.get("globs") or None,
            timeout_s=_wait_timeout(a),
            poll_s=a.get("poll"),
            agent=agent,
        ),
        "payload": "",
    },
    "ddflow_block": {
        "description": (
            "Mark an item blocked on something outside the queue — a decision, "
            "an upstream outage, an operator question. Better than leaving it "
            "claimed: a blocked item states its reason. A DONE or ABANDONED item needs "
            "reopen."
        ),
        "properties": {
            "id": ("string", "Item id.", True),
            "reason": ("string", "What it is waiting on.", True),
            "reopen": (
                "boolean",
                "Allow blocking a DONE or ABANDONED item.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().block(
            repo,
            a["id"],
            reason=a.get("reason", "") or "",
            reopen=bool(a.get("reopen", False)),
            agent=agent,
        ),
        "payload": ("id",),
    },
    "ddflow_external_sync": {
        "description": (
            "Observe the items in SIBLING repositories that this queue depends on "
            "(`needs = ['run_nemo_run:132.D']`, repositories named in [schedule] repos), "
            "and record what changed in this log. An external dependency is met only "
            "once it has been observed done here, so run this before `ddflow_next` when "
            "work waits on another project. Reads the other repository; never writes it."
        ),
        "properties": {},
        "api": lambda repo, a, agent: _api().external_sync(repo, agent=agent),
        "payload": "observed",
    },
}
