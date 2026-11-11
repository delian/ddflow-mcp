"""How an item ends: complete, abandon, remove, block, unblock and merge."""

from __future__ import annotations

from ._shared import (
    MUTATION_FLAG_EXEMPT,
    MUTATION_ORDER,
    MUTATION_PARAMS,
    NOTHING,
    OK,
    CliPolicy,
    Command,
    L,
    Param,
    _api,
    mutation_of,
)

COMMANDS: tuple[Command, ...] = (
    Command(
        path=("complete",),
        summary="finish an item (exit 3 = gates not satisfied)",
        tool="ddflow_complete",
        flag_exempt=MUTATION_FLAG_EXEMPT,
        description="Finish an item. Refuses (exit 3) when a required gate has not passed, a phase "
        "has open tasks, or no reviewer came from a different model family than the "
        "author. Pass your own 'model'.",
        call=lambda repo, a, agent, called_from=None, shell_cwd=None: _api().complete(
            repo,
            a["id"],
            sha=a.get("sha", "") or "",
            force=bool(a.get("force")),
            model=a.get("model", "") or "",
            changelog=a.get("changelog", "") or "",
            regression_test=a.get("regression_test", "") or "",
            lint_exempt=a.get("lint_exempt", "") or "",
            mutation=mutation_of(a),
            called_from=called_from,
            shell_cwd=shell_cwd,
            agent=agent,
            approval=a.get("approval", "") or "",
        ),
        payload=(
            *L.COMPLETE_BODY[:10],
            "wake",
            *L.COMPLETE_BODY[10:],
            "umbrellas_completed",
            "umbrella_refused",
            "refuted_passes",
            "worktree_removed",
            "kept_reason",
            # Optional: a chore's exemption (no key when not a chore).
            "chore",
        ),
        cli=CliPolicy(
            shown=(OK,),
            notes=L.complete_notes,
            human=L.complete_text,
            body=L.complete_body,
            call_kwargs=L.merge_kwargs,
        ),
        wants_called_from=True,
        params=(
            Param("id", help="Item id.", cli_help="", positional=True),
            Param("sha", help="Commit sha this shipped as.", cli_help="", default=""),
            Param(
                "model",
                help="The AUTHOR's model.",
                cli_help="the AUTHOR's model, for independence check",
                default="",
            ),
            Param(
                "force",
                type="boolean",
                help="Complete over unmet conditions, each recorded as overridden forever. Prefer `ddflow_gate_skip` with a reason.",
                cli_help="",
            ),
            Param(
                "approval",
                help="An agent's way past unmet conditions: 'request' asks the operator, then the approval id they answered.",
                cli_help="an agent: 'request' files an approval request for overriding the unmet conditions (the operator answers in chat), then the approval id; a failed review gate is never lifted",
                default="",
            ),
            Param(
                "changelog",
                help="Optional 'Added|Changed|Deprecated|Removed|Fixed|Security: text', or skip.",
                cli_help="'Added: text' (Added|Changed|Deprecated|Removed|Fixed|Security), or skip / internal to keep it out of the changelog; optional",
                default="",
            ),
            Param(
                "regression_test",
                help="Fix task: the test guarding its bug(s); closes them.",
                cli_help="for a fix task: the test that now guards the bug(s) it fixes; closes them (as `bug fixed` would) and completes. Repeat for several.",
                action="append",
                default=[],
            ),
            Param(
                "lint_exempt",
                help="Fix task of a CI-filed LINT bug (a check that is not a test, e.g. ruff format --check): why it needs no regression test. As `bug fixed lint_exempt`; instead of regression_test.",
                cli_help="close the CI-filed lint bug this fix task fixes (no test to write) with this reason, recorded, instead of --regression-test",
                default="",
            ),
            *MUTATION_PARAMS,
        ),
        tool_order=(
            "id",
            "sha",
            "model",
            "changelog",
            "regression_test",
            "lint_exempt",
            *MUTATION_ORDER,
            "force",
            "approval",
        ),
    ),
    Command(
        path=("abandon",),
        summary="stop work on an item without completing it",
        tool="ddflow_abandon",
        description="Stop work on an item without completing it, with a reason. Use when a "
        "task turns out to be unnecessary or impossible. DIFFERENT from blocking: "
        "a blocked item is waiting and will resume; an abandoned one will not, and "
        "so it stops holding its phase open — which an unfinished task otherwise "
        "does forever, since nothing can ever finish it.",
        call=lambda repo, a, agent, called_from=None, shell_cwd=None: _api().abandon(
            repo,
            a["id"],
            reason=a.get("reason", "") or "",
            force=bool(a.get("force")),
            repoint_to=a.get("repoint_to", "") or "",
            drop_edges=bool(a.get("drop_edges")),
            called_from=called_from,
            shell_cwd=shell_cwd,
            agent=agent,
        ),
        payload=(
            "id",
            "reason",
            "stranded",
            "drop_edges",
            "repointed",
            "worktree_removed",
            "kept_reason",
        ),
        render=L.abandon_text,
        cli=CliPolicy(shown=(OK,), call_kwargs=L.merge_kwargs),
        wants_called_from=True,
        params=(
            Param("id", help="Item id.", cli_help="", positional=True),
            Param("reason", help="Why it is being dropped.", cli_help="", required=True),
            Param(
                "force",
                type="boolean",
                help="Abandon although a sub-task is still open. Those sub-tasks are NOT abandoned with it: decide about each, or they sit under a parent nobody will finish.",
                cli_help="",
            ),
            Param(
                "repoint_to",
                help="Re-point every live item that needs this one to need this id instead (cycles refused), so none is left waiting on an abandoned item.",
                cli_help="re-point the items that need this one to need ID instead",
                default="",
            ),
            Param(
                "drop_edges",
                type="boolean",
                help="Drop the edge to this item from every live item that needs it, so none is left waiting on an abandoned item (otherwise the abandon is refused, naming this and --repoint-to).",
                cli_help="drop the items that need this one from its needs",
            ),
        ),
    ),
    Command(
        path=("remove",),
        summary="take an item out of the queue (recorded, not erased)",
        tool="ddflow_remove",
        description="Take an item out of the queue. The log is append-only, so this RECORDS a "
        "removal rather than erasing anything — the item stays in the history and "
        "in replay, which keeps the record honest about work that was planned and "
        "then dropped. Refuses if another item depends on it.",
        call=lambda repo, a, agent, called_from=None, shell_cwd=None: _api().remove_item(
            repo,
            a["id"],
            reason=a.get("reason", "") or "",
            force=bool(a.get("force")),
            called_from=called_from,
            shell_cwd=shell_cwd,
            agent=agent,
        ),
        payload=("id", "stranded", "drop_edges", "worktree_removed", "kept_reason"),
        render=L.remove_text,
        cli=CliPolicy(shown=(OK,), call_kwargs=L.merge_kwargs),
        wants_called_from=True,
        params=(
            Param("id", help="Item id.", cli_help="", positional=True),
            Param("reason", help="Why.", cli_help="", default=""),
            Param(
                "force",
                type="boolean",
                help="Remove although it still has open children or dependents. Both leave the queue inconsistent in a way the scheduler reports; read the refusal before overriding.",
                cli_help="",
            ),
        ),
    ),
    Command(
        path=("block",),
        tool="ddflow_block",
        description="Mark an item blocked on something outside the queue — a decision, "
        "an upstream outage, an operator question. Better than leaving it "
        "claimed: a blocked item states its reason. A DONE or ABANDONED item needs "
        "reopen.",
        call=lambda repo, a, agent: _api().block(
            repo,
            a["id"],
            reason=a.get("reason", "") or "",
            reopen=bool(a.get("reopen", False)),
            agent=agent,
        ),
        payload=("id",),
        render=L.block_text,
        cli=CliPolicy(shown=(OK,)),
        params=(
            Param("id", help="Item id.", cli_help="", positional=True),
            Param("reason", help="What it is waiting on.", cli_help="", required=True),
            Param(
                "reopen",
                type="boolean",
                help="Allow blocking a DONE or ABANDONED item.",
                cli_help="block an item that is already DONE or ABANDONED (refused without)",
            ),
        ),
    ),
    Command(
        path=("unblock",),
        summary="release a blocked item back into the queue (exit 2 = not blocked)",
        tool="ddflow_unblock",
        description="Release a BLOCKED item -- and every blocked item beneath it -- back into "
        "the queue, so `next` can offer them again. The inverse of ddflow_block, and "
        "how deferred work, or a whole archived section an import landed as blocked, "
        "becomes work once the OPERATOR says so: pass a phase id to release its "
        "section. Do not release held work on your own judgement. Returns "
        "nothing-to-do (exit 2) when nothing there is blocked.",
        call=lambda repo, a, agent: _api().unblock(
            repo, a["id"], note=a.get("note", "") or "", agent=agent
        ),
        payload=("id", "was", "released"),
        render=L.unblock_text,
        cli=CliPolicy(shown=(OK, NOTHING)),
        params=(
            Param("id", help="Item id.", cli_help="", positional=True),
            Param(
                "note",
                help="Why it is work again (who decided, and when).",
                cli_help="why it is work again",
                default="",
            ),
        ),
    ),
    Command(
        path=("merge",),
        summary="merge an item's branch from the primary checkout",
        tool="ddflow_merge",
        description="Land an item's branch without ever switching a checkout's branch. With "
        "[flow].integration = 'pr' it pushes and opens (or updates) a pull request "
        "instead, releases your lease and parks the item in REVIEW — take the next "
        "item; `ddflow_pr_sync` completes it once a person merges it.",
        wants_progress=True,
        call=L.merge_call,
        payload=L.MERGE_PAYLOAD,
        render=L.merge_text,
        cli=CliPolicy(
            shown=L.merge_shown,
            reason=L.merge_reason,
            notes=L.merge_notes,
            body=L.merge_body,
            call_kwargs=L.merge_progress_kwargs,
        ),
        wants_called_from=True,
        params=(
            Param("id", help="Item id.", cli_help="", positional=True),
            Param("message", help="Merge commit message.", cli_help="", default=""),
            Param(
                "queue",
                type="boolean",
                help="Enqueue (ddflow_queue add) instead of landing.",
                cli_help="enqueue the item in the merge queue instead of landing it (`ddflow queue add`)",
            ),
            Param(
                "keep",
                type="boolean",
                help="Keep the worktree after merging, for inspection.",
                cli_help="",
            ),
            Param(
                "allow_dirty",
                type="boolean",
                help="Merge although the worktree is dirty; uncommitted changes are NOT included. Only once you know it is build output.",
                cli_help="",
            ),
            Param(
                "allow_empty",
                type="boolean",
                help="Land a branch with no commits ahead of its target (refused by default: usually the wrong tree; rebind with ddflow_update worktree).",
                cli_help="land a branch with no commits ahead of its target (refused by default: it would record the item merged with nothing landed)",
            ),
            Param(
                "force",
                type="boolean",
                help="With [merge].require_gates = refuse: land although required gates are unsettled; recorded as a merge.forced event naming them. An agent also needs `approval`.",
                cli_help="with [merge].require_gates = refuse: land although required gates are unsettled (recorded as merge.forced). An agent also passes --approval",
            ),
            Param(
                "approval",
                help="An agent's way past [merge].require_gates = refuse with `force`: 'request' asks the operator, then the approval id they answered (bound to this exact merge, single-use, not spent by a refused call).",
                cli_help="an agent: 'request' files an approval request for forcing this merge (the operator answers in chat), then the approval id",
                default="",
            ),
            Param(
                "branch",
                help="no_worktree items: the branch to land (default: the one checked out where this runs). Changes outside the item's globs come back as outside_globs.",
                cli_help="for an item claimed without a worktree: the branch to land (default: the one checked out in the worktree you are standing in)",
                default="",
            ),
            Param(
                "model",
                help="The AUTHOR's model. In PR mode the item completes at `ddflow_pr_sync`, which needs it for reviewer independence.",
                cli_help="the AUTHOR's model. In PR mode completion happens later, at `pr sync`, and the reviewer-independence check needs it then",
                default="",
            ),
        ),
        tool_order=(
            "id",
            "message",
            "queue",
            "keep",
            "model",
            "allow_dirty",
            "allow_empty",
            "force",
            "approval",
            "branch",
        ),
    ),
)
