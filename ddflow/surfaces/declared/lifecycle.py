"""The loop's commands, declared once: next, claim, heartbeat, release, wait, approve, gate,
complete, abandon, remove, block, unblock and merge.

`surfaces/parsers/lifecycle.py` registers their command-line half and `surfaces/tools/` takes
their MCP entries (D-unify 4, B-uni-cmd-migrate.5-lifecycle). `approve` has no tool, on
purpose: a human checkpoint an agent can clear is not a checkpoint.
"""

from __future__ import annotations

from ...core.defaults import DEFAULT_NEXT_KIND, DEFAULT_WAIT_TIMEOUT_S
from ...core.model import GATE_OUTCOMES
from ..argtypes import GLOBS_HELP, _Globs
from ..registry import Command, Param, by_tool
from ..tools._common import _api, _wait_timeout

COMMANDS: tuple[Command, ...] = (
    Command(
        path=("next",),
        summary="what may start now (exit 2 = nothing actionable, exit 1 = unknown --phase)",
        tool="ddflow_next",
        description="What may be started RIGHT NOW, and for everything that may not, the reason. "
        "Independent items in the ready set can be run in parallel worktrees by "
        "separate agents. Returns ready=[] when nothing is actionable — that is a "
        "result, not an error, and it never means 'pick something anyway'.",
        call=lambda repo, a, agent: _api().next_item(
            repo,
            kind=a.get("kind") or _api().DEFAULT_NEXT_KIND,
            phase=a.get("phase", "") or "",
            agent=agent,
        ),
        payload="",
        params=(
            Param(
                "phase",
                help="Restrict to one phase (the 'implement phase X' entry point).",
                cli_help="limit to this phase; exit 1 if it names no phase or item",
                default="",
            ),
            Param(
                "kind",
                help="'task' (default) or 'phase'.",
                cli_help="",
                default=DEFAULT_NEXT_KIND,
                choices=("task", "phase"),
            ),
        ),
    ),
    Command(
        path=("claim",),
        summary="lease an item + create its worktree (exit 3 = refused)",
        tool="ddflow_claim",
        description="Lease an item and create its isolated git worktree. Refuses (exit 3) if "
        "another agent holds it or holds an item whose file globs overlap, and names "
        "what you could take instead. NEVER steals an expired lease: a crashed "
        "agent's worktree often holds finished work.",
        call=lambda repo, a, agent, called_from=None: _api().claim(
            repo,
            a["id"],
            globs=a.get("globs", "") or "",
            note=a.get("note", "") or "",
            force=bool(a.get("force")),
            no_worktree=bool(a.get("no_worktree")),
            called_from=called_from,
            resources=a.get("resources", "") or "",
            agent=agent,
        ),
        # `rebound`/`here`: the item kept its OWN tree from an earlier claim, and whether
        # the caller is standing in it -- over MCP the payload is the only way an agent
        # learns it has to move there.
        payload=(
            "item",
            "holder",
            "worktree",
            "branch",
            "base",
            "rebound",
            "here",
            "port",
            "port_advice",
        ),
        # `claim` is the one operation that needs to know WHERE THE CALLER IS, not just
        # which repo: adoption turns on whether the caller was already standing in a
        # worktree. The dispatcher passes it only to tools that ask.
        wants_called_from=True,
        params=(
            Param("id", help="Item id to claim.", cli_help="", positional=True),
            Param(
                "globs",
                help="Comma-separated path globs this work will write.",
                cli_help="what this claim writes (recorded on the item too); " + GLOBS_HELP,
                action=_Globs,
            ),
            Param("note", help="What you intend to do.", cli_help=""),
            Param(
                "force",
                type="boolean",
                help="Override a refusal. Legitimate only to retry after `ddflow_recover` said a crashed agent's worktree holds nothing. Forcing past a dependency or live lease is how two agents write one file; recorded either way.",
                cli_help="",
            ),
            Param(
                "no_worktree",
                type="boolean",
                help="Lease the item without creating a worktree. For work that is not a code change — a research or review task.",
                cli_help="",
            ),
            Param(
                "resources",
                help="Physical resources this claim holds, e.g. 'gpu:2'; they REPLACE the item's declared ones. Refused (exit 3) when live claims already use the capacity ([schedule] resources), every holder counted.",
                cli_help="the resources this claim reserves, e.g. 'gpu:2' (recorded on the item too)",
                default="",
            ),
        ),
        tool_order=("id", "globs", "note", "no_worktree", "resources", "force"),
    ),
    Command(
        path=("heartbeat",),
        summary="renew a lease",
        tool="ddflow_heartbeat",
        description="Renew the lease on an item. Call periodically during long work, "
        "or the lease expires and another agent may take the item.",
        call=lambda repo, a, agent, called_from=None: _api().heartbeat(
            repo, a["id"], agent=agent, called_from=called_from
        ),
        payload=("renewed", "waiters", "globs_withheld"),
        # The item's own tree renews its lease whoever claimed it -- identity is derived
        # from the tree, so without WHERE the caller is this said "no lease held" from
        # exactly the tree `claim` made.
        wants_called_from=True,
        params=(Param("id", help="Item id.", cli_help="", positional=True),),
    ),
    Command(
        path=("release",),
        summary="give up a lease",
        tool="ddflow_release",
        description="Give up a lease without completing the item — when you are handing off, "
        "stopping, or recovering someone else's abandoned work after inspecting it. "
        "The note is recorded in the log and is often the only lasting explanation "
        "of why a claim was broken.",
        call=lambda repo, a, agent: _api().release_item(
            repo, a["id"], note=a.get("note", "") or "", agent=agent
        ),
        payload=("released", "woke"),
        params=(
            Param("id", help="Item id.", cli_help="", positional=True),
            Param("note", help="Why you are releasing it.", cli_help=""),
        ),
    ),
    Command(
        path=("wait",),
        summary="sleep until an item (or anything) can be claimed; exit 2 = deadline, or waiting cannot help",
        tool="ddflow_wait",
        description="Sleep until an item can be claimed (or, with no item, until anything is ready) and return the moment it can. Use it instead of polling or asking the operator when a claim was refused for another agent's lease or overlapping files, or an unfinished dependency someone is working on. Exit 0: claim now (it says what freed it). Exit 2: deadline passed, or waiting cannot help (done, cycle, operator hold, a dependency nobody works on) and it says what to do. The holder is told you wait.",
        call=lambda repo, a, agent: _api().wait_item(
            repo,
            item=a.get("item", "") or "",
            phase=a.get("phase", "") or "",
            kind=a.get("kind") or _api().DEFAULT_NEXT_KIND,
            globs=a.get("globs") or None,
            timeout_s=_wait_timeout(a),
            poll_s=a.get("poll"),
            agent=agent,
        ),
        payload="",
        params=(
            Param(
                "item",
                help="The item to wait for (default: anything ready).",
                cli_help="the item to wait for (default: anything ready)",
                default="",
            ),
            Param(
                "phase",
                help="With no item: anything ready in this phase.",
                cli_help="with no --item: anything ready in this phase",
                default="",
            ),
            Param(
                "kind",
                help="'task' (default) or 'phase'.",
                cli_help="",
                default=DEFAULT_NEXT_KIND,
                choices=("task", "phase"),
            ),
            Param(
                "globs",
                help="With item: the globs you will claim with (comma-separated or a JSON array), so ready means that claim will not be refused for them.",
                cli_help="with --item: the globs you will claim with, so READY means that claim will succeed; "
                + GLOBS_HELP,
                action=_Globs,
            ),
            Param(
                "timeout",
                type="number",
                help="Seconds to wait (default 300, at most 1800: a client may time a tool call out, so call again to keep waiting). 0 asks without waiting.",
                cli_help=f"seconds to wait (default {DEFAULT_WAIT_TIMEOUT_S}; 0 asks without waiting)",
            ),
            Param(
                "poll",
                type="number",
                help="Seconds between log checks (default 2).",
                cli_help="seconds between log checks",
            ),
        ),
    ),
    Command(
        path=("approve",),
        reason=(
            "clears a HUMAN-approval gate, and the whole point is that the agent cannot. "
            "A human checkpoint reachable from the MCP surface is not a human checkpoint "
            "\u2014 it is a second `gate record` with a longer name. This exemption is the "
            "feature, not an oversight, and `test_no_mcp_tool_can_clear_a_human_gate` "
            "asserts it holds end to end rather than resting on this line."
        ),
        summary="a PERSON clears (or rejects) a human-approval gate — no MCP equivalent",
        params=(
            Param("id", positional=True),
            Param("gate", positional=True),
            Param("note", help="what you looked at, for the record"),
            Param("reject", type="boolean", help="refuse it; --reason required"),
            Param("reason", help="why it was rejected — a 'no' nobody can act on is a stall"),
        ),
    ),
    Command(
        path=("gate", "status"),
        tool="ddflow_gate_status",
        description="Where an item stands in its quality pipeline, which gate is next, and the "
        "instruction for that gate. Gates marked '?' did not run — that is a coverage "
        "gap, never a pass.",
        kind="gate.status",
        prose=True,
        prose_reason="carries the next gate's INSTRUCTION, which is the useful half",
        defaults={"gate": ""},
        call=lambda repo, a, agent: _api().gate_status(repo, a["id"], agent=agent),
        # PROSE: the body carries the next gate's INSTRUCTION, which is the half an
        # agent acts on. `--json` gives the structured pipeline instead.
        payload="text",
        params=(Param("id", help="Item id.", cli_help="", positional=True),),
    ),
    Command(
        path=("gate", "list"),
        summary="the gates this project defines; --refuted: the gates passed on refutation",
        tool="ddflow_gate_list",
        description="The gates this project defines; with refuted=true, every gate passed ON "
        "REFUTATION, to spot-check.",
        kind="gate.list",
        prose=True,
        prose_reason="one row per gate (or per gate passed on refutation), meant to be read as lines",
        call=lambda repo, a, agent: _api().gate_list(
            repo, refuted=bool(a.get("refuted")), since=str(a.get("since") or ""), agent=agent
        ),
        payload="text",
        params=(
            Param(
                "refuted",
                type="boolean",
                help="List the gates passed on refutation instead.",
                cli_help="list every gate recorded passed ON REFUTATION (D-unify 5), to spot-check",
            ),
            Param(
                "since",
                help="With refuted: since this ISO date.",
                cli_help="with --refuted: only passes recorded at or after this ISO date or timestamp",
                default="",
            ),
        ),
    ),
    Command(
        path=("gate", "run"),
        tool="ddflow_gate_run",
        description="Execute a command gate (tests, linters) and record the result "
        "with its evidence. Agent gates cannot be run this way; they are "
        "recorded with ddflow_gate_record.",
        call=lambda repo, a, agent, called_from=None: _api().gate_run(
            repo, a["id"], a["gate"], agent=agent, called_from=called_from
        ),
        payload=("gate", "outcome", "evidence"),
        wants_called_from=True,
        params=(
            Param("id", help="Item id.", cli_help="", positional=True),
            Param("gate", help="Gate id, e.g. unit_tests.", cli_help="", positional=True),
        ),
    ),
    Command(
        path=("gate", "verify"),
        summary="break what this gate guards and require it to notice (exit 1 = it cannot)",
        tool="ddflow_gate_verify",
        description="Break what a gate guards and require it to NOTICE: applies each mutation "
        "registered on the gate, runs it, requires a non-zero exit, restores the "
        "file. A gate that cannot fail reports success on every change. Exit 1: "
        "the gate did NOT catch its mutation, or none is registered. Exit 3 on a "
        "HUMAN-approval gate (nothing to mutate). A mutation whose `old` text is "
        "absent or ambiguous is a FAILURE, not a skip: the gate ran on pristine "
        "source.",
        call=lambda repo, a, agent: _api().gate_verify(repo, a["id"], a["gate"], agent=agent),
        payload=("gate", "reason", "results", "verified"),
        params=(
            Param("id", help="Item whose worktree to mutate in.", cli_help="", positional=True),
            Param("gate", help="Gate id. Must be a command gate.", cli_help="", positional=True),
        ),
    ),
    Command(
        path=("gate", "record"),
        tool="ddflow_gate_record",
        description="Record the outcome of a gate you performed (research, a review, a bug hunt). "
        "outcome is one of passed/failed/unavailable/partial/skipped. "
        "If a reviewer or tool could not run, record 'unavailable' with a reason, "
        "never 'passed'. Pass the reviewer's model for the family check.",
        call=lambda repo, a, agent, called_from=None: _api().gate_record(
            repo,
            a["id"],
            a["gate"],
            outcome=a.get("outcome", "passed") or "passed",
            reason=a.get("reason", "") or "",
            evidence=_api().GateEvidence(
                note=a.get("evidence", "") or "",
                command=a.get("command", "") or "",
                exit_code=a.get("exit_code"),
                model=a.get("reviewer_model", "") or a.get("model", "") or "",
                model_is_reviewer=bool(a.get("reviewer_model")),
                reviewed_sha=a.get("reviewed_sha", "") or "",
                output_file=a.get("output_file", "") or "",
            ),
            agent=agent,
            called_from=called_from,
        ),
        payload=("gate", "outcome", "warning"),
        wants_called_from=True,
        params=(
            Param("id", help="Item id.", cli_help="", positional=True),
            Param("gate", help="Gate id.", cli_help="", positional=True),
            Param(
                "outcome",
                help="passed | failed | unavailable | partial | skipped",
                cli_help="failed, unavailable, partial and skipped each require --reason",
                tool_required=True,
                default="passed",
                choices=tuple(GATE_OUTCOMES),
            ),
            Param(
                "reason",
                help="Required for failed/unavailable/partial/skipped.",
                cli_help="why the gate did not pass; REQUIRED for outcomes failed, unavailable, partial and skipped (--evidence is what you observed, not a substitute)",
                default="",
            ),
            Param(
                "evidence",
                help="What you ran and what it said. Required by some gates.",
                cli_help="what you observed: output, a summary",
                default="",
            ),
            Param(
                "command",
                help="The command you ran. With `exit_code` it makes an outcome evidence; a gate in `gates.evidence_required` is rejected without them.",
                cli_help="",
                default="",
            ),
            Param("exit_code", help="That command's exit code.", cli_help="", cli_type=int),
            Param(
                "model",
                help="REVIEWER's model, e.g. 'gemini-2.5-pro'.",
                cli_help="the REVIEWER's model, for family independence (the author's is `complete --model`); an author-family name on a reviewer gate is refused",
                default="",
            ),
            Param(
                "reviewer_model",
                help="Like `model`; says it IS the reviewer.",
                cli_help="like --model, stating that this model IS the reviewer, so an author-family name is recorded rather than refused",
                default="",
            ),
            Param(
                "reviewed_sha",
                help="Commit reviewed (roborev review <sha>); must be the branch.",
                cli_help="the commit the review tool ran on (roborev review <sha>); refused when it is not the item's branch head or a commit of its branch",
                default="",
            ),
            Param(
                "output_file",
                help="Path to its full output; a digest is recorded.",
                cli_help="",
                default="",
            ),
        ),
        tool_order=(
            "id",
            "gate",
            "outcome",
            "reason",
            "evidence",
            "model",
            "reviewer_model",
            "reviewed_sha",
            "command",
            "exit_code",
            "output_file",
        ),
    ),
    Command(
        path=("gate", "skip"),
        tool="ddflow_gate_skip",
        flag_exempt={
            "--outcome": "a skip IS the outcome",
            "--evidence": "a skipped gate produced none; that is what skipped means",
            "--command": "nothing ran",
            "--exit-code": "nothing ran",
            "--output-file": "nothing ran",
            "--model": "no reviewer performed it",
        },
        description="Skip a gate ON THE RECORD, with a mandatory reason: the auditable escape hatch. `gates.require_outcome` means a silent gate BLOCKS completion, so the alternative to a skip is forcing past everything at once; a skip names the single step dropped and why, permanently in the log. A gate in `gates.required` still blocks when skipped.",
        call=lambda repo, a, agent: _api().gate_record(
            repo, a["id"], a["gate"], skip=True, reason=a.get("reason", "") or "", agent=agent
        ),
        payload=("gate", "outcome"),
        params=(
            Param("id", help="Item id.", cli_help="", positional=True),
            Param("gate", help="Gate id.", cli_help="", positional=True),
            Param(
                "outcome",
                help="failed, unavailable, partial and skipped each require --reason",
                default="passed",
                choices=tuple(GATE_OUTCOMES),
                cli_only=True,
            ),
            Param(
                "reason",
                help="Why this step does not apply HERE. 'n/a' is not a reason: the next person reads this to decide whether you were right.",
                cli_help="why the gate did not pass; REQUIRED for outcomes failed, unavailable, partial and skipped (--evidence is what you observed, not a substitute)",
                tool_required=True,
                default="",
            ),
            Param(
                "evidence", help="what you observed: output, a summary", default="", cli_only=True
            ),
            Param("command", default="", cli_only=True),
            Param("exit_code", type="integer", cli_only=True),
            Param(
                "model",
                help="the REVIEWER's model, for family independence (the author's is `complete --model`); an author-family name on a reviewer gate is refused",
                default="",
                cli_only=True,
            ),
            Param("output_file", default="", cli_only=True),
        ),
    ),
    Command(
        path=("complete",),
        summary="finish an item (exit 3 = gates not satisfied)",
        tool="ddflow_complete",
        description="Finish an item. Refuses (exit 3) when a required gate has not passed, when a "
        "phase still has open tasks, or when no reviewer came from a different model "
        "family than the author. Pass your own model as 'model'.",
        call=lambda repo, a, agent: _api().complete(
            repo,
            a["id"],
            sha=a.get("sha", "") or "",
            force=bool(a.get("force")),
            model=a.get("model", "") or "",
            changelog=a.get("changelog", "") or "",
            regression_test=a.get("regression_test", "") or "",
            agent=agent,
        ),
        payload=(
            "id",
            "sha",
            "independence",
            "forced",
            "coverage_gaps",
            "note",
            "woke",
            "bugs_closed",
            "umbrellas_completed",
            "umbrella_refused",
            "refuted_passes",
        ),
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
                help="Complete over unmet conditions; each is recorded as overridden, forever. Prefer `ddflow_gate_skip` with a reason: it drops one named step.",
                cli_help="",
            ),
            Param(
                "changelog",
                help="Optional 'Added|Changed|Deprecated|Removed|Fixed|Security: text', or skip.",
                cli_help="'Added: text' (Added|Changed|Deprecated|Removed|Fixed|Security), or skip / internal to keep it out of the changelog; optional",
                default="",
            ),
            Param(
                "regression_test",
                help="For a fix task: the test that now guards its bug(s); closes them.",
                cli_help="for a fix task: the test that now guards the bug(s) it fixes; closes them (as `bug fixed` would) and completes. Repeat for several.",
                action="append",
                default=[],
            ),
        ),
        tool_order=("id", "sha", "model", "changelog", "regression_test", "force"),
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
        call=lambda repo, a, agent: _api().abandon(
            repo, a["id"], reason=a.get("reason", "") or "", force=bool(a.get("force")), agent=agent
        ),
        payload=("id", "reason"),
        params=(
            Param("id", help="Item id.", cli_help="", positional=True),
            Param("reason", help="Why it is being dropped.", cli_help="", required=True),
            Param(
                "force",
                type="boolean",
                help="Abandon although a sub-task is still open. Those sub-tasks are NOT abandoned with it: decide about each, or they sit under a parent nobody will finish.",
                cli_help="",
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
        call=lambda repo, a, agent: _api().remove_item(
            repo, a["id"], reason=a.get("reason", "") or "", force=bool(a.get("force")), agent=agent
        ),
        payload=("id",),
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
        call=lambda repo, a, agent, called_from=None: _api().merge_item(
            repo,
            a["id"],
            message=a.get("message", "") or "",
            allow_dirty=bool(a.get("allow_dirty")),
            allow_empty=bool(a.get("allow_empty")),
            keep=bool(a.get("keep")),
            model=a.get("model", "") or "",
            branch=a.get("branch", "") or "",
            called_from=called_from,
            agent=agent,
        ),
        payload=(
            "id",
            "sha",
            "branch_head",
            "base",
            "pr",
            "branch",
            "outside_globs",
            "outside_globs_unknown",
            "merge_gate_human",
            "worktree",
            "worktree_removed",
        ),
        wants_called_from=True,
        params=(
            Param("id", help="Item id.", cli_help="", positional=True),
            Param("message", help="Merge commit message.", cli_help="", default=""),
            Param(
                "keep",
                type="boolean",
                help="Keep the worktree after merging, for inspection.",
                cli_help="",
            ),
            Param(
                "allow_dirty",
                type="boolean",
                help="Merge although the worktree has uncommitted changes; they are NOT included. Pass it only once you have looked at what is dirty and decided it is build output.",
                cli_help="",
            ),
            Param(
                "allow_empty",
                type="boolean",
                help="Land a branch with no commits ahead of its target. Refused by default: usually the item is bound to the wrong tree (rebind with ddflow_update worktree).",
                cli_help="land a branch with no commits ahead of its target (refused by default: it would record the item merged with nothing landed)",
            ),
            Param(
                "branch",
                help="For an item claimed with no_worktree: the branch to land (default: the branch checked out where this connection runs). Changes outside the item's globs come back as outside_globs.",
                cli_help="for an item claimed without a worktree: the branch to land (default: the one checked out in the worktree you are standing in)",
                default="",
            ),
            Param(
                "model",
                help="The AUTHOR's model. In PR mode the item completes later, at `ddflow_pr_sync`, and the reviewer-independence check needs it then.",
                cli_help="the AUTHOR's model. In PR mode completion happens later, at `pr sync`, and the reviewer-independence check needs it then",
                default="",
            ),
        ),
        tool_order=("id", "message", "keep", "model", "allow_dirty", "allow_empty", "branch"),
    ),
)


BY_TOOL = by_tool(COMMANDS)
