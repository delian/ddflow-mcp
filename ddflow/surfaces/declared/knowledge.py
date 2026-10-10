"""The knowledge commands, declared once: lessons, recall, similar, dupes, link and decisions.

`surfaces/parsers/knowledge.py` registers their command-line half and
`surfaces/tools/` takes their MCP entries (D-unify 4, B-uni-cmd-migrate.4-adapters). `link`
names the relation with one flag each (a required, mutually exclusive group of CLI-only
parameters); its tool takes `relation` and `target`. A command with a `render` runs on the CLI
executor (`surfaces/cliexec.py`); the others keep a `cmd_*` function in `surfaces/commands/`.
"""

from __future__ import annotations

from ...core.budget import RECALL_MAX_CHARS
from ...core.model import LINK_RELATIONS
from ..registry import Command, Param, by_tool
from ..tools._common import _answer, _api
from .answer import ANSWER_FLAG_EXEMPT, ANSWER_PARAMS, candidate_lines

#: `ddflow link` names the relation with a flag each; the tool takes `relation` and `target`.
_RELATION_FLAG_REASON = "the relation is MCP `relation` + `target`, not one flag per relation"
LINK_FLAG_EXEMPT = dict.fromkeys(
    ("--extends", "--duplicate-of", "--related", "--distinct"), _RELATION_FLAG_REASON
)


def _decision_text(out, a) -> str:
    d = out.data["decision"]
    head = f"{d['id']} — {d['title']}\n  status {d['status']}"
    if d.get("superseded_by"):
        head += f" (superseded by {d['superseded_by']})"
    if d.get("decided_by"):
        head += f" · decided by {d['decided_by']}"
    lines = [head]
    for label, key in (
        ("Context", "context"),
        ("Decision", "decision"),
        ("Consequences", "consequences"),
        ("Alternatives rejected", "alternatives"),
    ):
        if d.get(key):
            lines.append(f"\n{label}:\n  {d[key]}")
    if d.get("globs"):
        lines.append(f"\nGoverns: {', '.join(d['globs'])}")
    return "\n".join(lines)


def _applicable_text(out, a) -> str:
    lines = [f"  [{d['id']}] {d['title']}\n      {d['decision']}" for d in out.data["applicable"]]
    lines += [
        f"  [{d['id']}] {d['title']}  (project-wide)\n      {d['decision']}"
        for d in out.data["project_wide"]
    ]
    return "\n".join(lines)


def _lesson_hits(out, a) -> str:
    cut = out.data["snippet_chars"]
    return "\n".join(f"- {h['title']}\n    {(h.get('rule') or '')[:cut]}" for h in out.data["hits"])


def _dupe_pairs(out, a) -> str:
    d = out.data
    if not d["pairs"]:
        return ""  # nothing to settle: the executor says why instead
    lines = []
    for p in d["pairs"]:
        lines.append(f"{p['score']:.2f}  {p['a']} ({p['a_kind']}) ~ {p['b']} ({p['b_kind']})")
        lines.append(f"      {p['a_title']}")
        lines.append(f"      {p['b_title']}")
    lines.append(
        f"{d['count']} unsettled pair(s) at floor {d['floor']:g}. Settle each: "
        f"`ddflow link <a> --duplicate-of <b>` (or --extends/--related), or "
        f"`--distinct` to dismiss it for good."
    )
    return "\n".join(lines)


#: Help text of each relation flag; a relation added to the model falls back to a generic one.
_LINK_HELP = {
    "extends": "subject adds to ID",
    "duplicate_of": "subject is the same thing as ID",
    "related": "subject is related to ID",
    "distinct": "subject is NOT a duplicate of ID: dismiss the pair for good",
}


def _link_call(repo, a, agent):
    """`link_record` from MCP's ``relation`` + ``target`` or the CLI's one flag per relation
    (the first of LINK_RELATIONS given)."""
    relation, target = a.get("relation", "") or "", a.get("target", "") or ""
    if not relation:
        for rel in LINK_RELATIONS:
            if a.get(rel):
                relation, target = rel, a[rel]
                break
    return _api().link_record(
        repo,
        a.get("subject", "") or "",
        relation,
        target,
        reason=a.get("reason", "") or "",
        agent=agent,
    )


def _linked(out, a) -> str:
    d = out.data
    merged = d.get("merged") or {}
    tail = f"; {merged['superseded']} superseded by {merged['by']}" if merged else ""
    return f"{d['subject']} {d['relation']} {d['target']}{tail}"


COMMANDS: tuple[Command, ...] = (
    Command(
        path=("lesson", "add"),
        tool="ddflow_lesson_add",
        flag_exempt=ANSWER_FLAG_EXEMPT,
        description="Record a lesson so it is never re-learned. Use after any bug, any operator "
        "correction, any surprise. Make the rule transferable — a future agent on a "
        "different task must be able to apply it.",
        call=lambda repo, a, agent: _api().lesson_add(
            repo,
            _api().LessonDraft(
                title=a.get("title", "") or "",
                rule=a.get("rule", "") or "",
                why=a.get("why", "") or "",
                how=a.get("how", "") or "",
                summary=a.get("summary", "") or "",
                tags=a.get("tags", "") or "",
                seen_in=a.get("seen_in", "") or "",
                supersedes=a.get("supersedes", "") or "",
                pattern=a.get("pattern", "") or "",
                globs=a.get("globs", "") or "",
                id=a.get("id", "") or "",
                answer=_answer(a),
            ),
            agent=agent,
        ),
        payload=("id",),
        params=(
            Param(
                "id",
                help="Stable id you choose. Referenced by `supersedes`, by commit messages and by the reconstruction; a generated id cannot be cited in advance.",
                cli_help="",
                default="",
            ),
            Param("title", help="The rule as a one-line statement.", cli_help="", required=True),
            Param("rule", help="The rule in full.", cli_help="", default=""),
            Param("why", help="Why it is true / what went wrong.", cli_help="", default=""),
            Param("how", help="How to apply or detect it.", cli_help="", default=""),
            Param(
                "summary",
                help="The lesson in ONE paragraph, for a reader who will not open the full rule. Rendered into docs/ddflow/LESSONS-SUMMARY.md.",
                cli_help="the lesson in one paragraph; what docs/ddflow/LESSONS-SUMMARY.md is made of",
                default="",
            ),
            Param("tags", help="Comma-separated tags.", cli_help="", default=""),
            Param(
                "seen_in",
                help="Comma-separated item ids where this was hit. What makes a lesson checkable later instead of merely memorable.",
                cli_help="",
                default="",
            ),
            Param(
                "supersedes",
                help="Comma-separated lesson ids this replaces. The old one is retired, not deleted: the corpus stops growing without losing what was once believed.",
                cli_help="",
                default="",
            ),
            Param(
                "pattern",
                help="A regex naming the mistake in CODE. Scans now and stores WHICH sites match, so `ddflow_lesson_verify` can name those that reappear. Prefer it to a remembered rule when mechanical: a count says 'worse', never 'which'. Refused if it does not compile.",
                cli_help="regex this lesson forbids. Scans NOW and stores WHICH sites match, so `lesson verify` can name what reappeared — a count could only say it got worse",
                default="",
            ),
            Param(
                "globs",
                help="Comma-separated globs to scan for `pattern`. Default: every tracked file.",
                cli_help="comma-separated globs to scan (default: all tracked files)",
                default="",
            ),
            *ANSWER_PARAMS,
        ),
    ),
    Command(
        path=("lesson", "verify"),
        summary="re-scan every lesson's pattern and name the sites that reappeared",
        tool="ddflow_lesson_verify",
        prose_reason="names the sites a forbidden pattern reappeared at; the list IS the finding, and the point of B20 is that a caller reads which rather than parsing how many",
        description="Re-scan every lesson that declared a code `pattern` and report the sites where "
        "it has REAPPEARED. Exit 1 names them; exit 2 means no lesson declares a "
        "pattern, which is NOT a pass — it means this project has no mechanical ratchet "
        "on its lessons yet. Run after a change that touches code a lesson governs.",
        prose=True,
        call=lambda repo, a, agent: _api().lessons_verify(repo, agent=agent),
        payload="text",
        params=(),
    ),
    Command(
        path=("lesson", "search"),
        render=_lesson_hits,
        tool="ddflow_lesson_search",
        description="Search past lessons by relevance (BM25). Use before starting "
        "work, and whenever something surprises you.",
        call=lambda repo, a, agent: _api().lesson_search(
            repo, a.get("query", "") or "", limit=a.get("limit"), agent=agent
        ),
        payload="hits",
        params=(
            Param(
                "query", help="What you are about to do, in words.", cli_help="", positional=True
            ),
            Param(
                "limit",
                type="integer",
                help="Max results (default 5).",
                cli_help="default: [lessons].max_results",
            ),
        ),
    ),
    Command(
        path=("recall",),
        summary="'have we been here before?' — search decisions, lessons, research, bugs, tasks and past prompts at once",
        tool="ddflow_recall",
        description="'HAVE WE BEEN HERE BEFORE?' -- one search across everything this project remembers: decisions, lessons, research verdicts, operational memories, past bugs, similar tasks and the operator's earlier prompts. CALL THIS BEFORE STARTING ANY NON-TRIVIAL WORK, so nothing is said or learned twice. Results are labelled by kind (a binding decision, a transferable lesson and an old prompt change what you do differently); a superseded decision names its replacement -- follow that.",
        call=lambda repo, a, agent: _api().recall(
            repo,
            a.get("query", "") or "",
            sources=a.get("sources", "") or "",
            limit=int(a.get("limit") or 3),
            max_chars=int(a.get("max_chars") or RECALL_MAX_CHARS),
            agent=agent,
        ),
        payload="results",
        params=(
            Param(
                "query",
                help="What you are about to do, in plain words.",
                cli_help="",
                positional=True,
            ),
            Param(
                "limit",
                type="integer",
                help="Hits per source (default 3).",
                cli_help="hits per source",
                default=3,
            ),
            Param(
                "sources",
                help="Comma-separated subset: decisions,lessons,memories,research,bugs,items,prompts. Default: all.",
                cli_help="comma-separated subset: decisions,lessons,memories,research,bugs,items,prompts",
                default="",
            ),
            Param(
                "max_chars",
                type="integer",
                help="Total budget for the answer. The point of a budget is that recall is called at the START of work, where a long answer costs the context the work itself needs.",
                cli_help="",
                default=RECALL_MAX_CHARS,
            ),
        ),
        tool_order=("query", "limit", "max_chars", "sources"),
    ),
    Command(
        path=("similar",),
        render=lambda out, a: "\n".join(candidate_lines(out.data["candidates"])),
        summary="'is this already filed?' -- the existing bugs, tasks, lessons and other records most like a text, before you add it (read-only; exit 2 when none)",
        tool="ddflow_similar",
        description="'IS THIS ALREADY FILED?' -- the existing records most like a text, BEFORE you file it as a bug, task, lesson or other record. Read-only. Candidates cross kinds and include closed records (a bug that repeats a fixed one is caught); each carries id, kind, title, state, score (0-1), shared words and flags, per [dedupe] show_floor, max_candidates and kinds. A score is a prompt to LOOK, not a verdict. Nothing close: exit 2.",
        call=lambda repo, a, agent: _api().similar(
            repo, a.get("text", "") or "", kinds=a.get("kind", "") or "", agent=agent
        ),
        payload="candidates",
        params=(
            Param(
                "text",
                help="The title or summary of the record you are about to file.",
                cli_help="the title or summary of the record you are about to file",
                positional=True,
            ),
            Param(
                "kind",
                help="Comma-separated subset of the configured kinds to look in: bug,task,phase,lesson,decision,research,memory. Default: all of them.",
                cli_help="comma-separated subset of [dedupe].kinds: bug,task,phase,lesson,decision,research,memory (default: all of them)",
                default="",
            ),
        ),
    ),
    Command(
        path=("dupes",),
        render=_dupe_pairs,
        summary="'is anything filed twice?' -- the near-duplicate PAIRS already in the log, skipping pairs already linked or dismissed (read-only; exit 2 when none)",
        tool="ddflow_dupes",
        description="'IS ANYTHING FILED TWICE?' -- the near-duplicate PAIRS already in the log, "
        "skipping pairs already linked or dismissed (a `distinct` verdict never "
        "returns). Read-only. Each pair carries both ids and kinds, their titles and "
        "the score (0-1). A score is a prompt to LOOK, not a verdict; settle a pair "
        "with ddflow_link. Nothing close: exit 2.",
        call=lambda repo, a, agent: _api().dupes(
            repo,
            kinds=a.get("kind", "") or "",
            open_only=bool(a.get("open_only")),
            floor=a.get("floor"),
            limit=int(a.get("limit") or 0),
            agent=agent,
        ),
        payload=("pairs", "count", "kinds", "open_only", "floor", "limit"),
        params=(
            Param(
                "kind",
                help="Comma-separated subset of the configured kinds: bug,task,phase,lesson,decision,research,memory. Default: all of them.",
                cli_help="comma-separated subset of [dedupe].kinds: bug,task,phase,lesson,decision,research,memory (default: all of them)",
                default="",
            ),
            Param(
                "open_only",
                type="boolean",
                help="Only pairs where both records are still live (the dedupe_sweep pass).",
                cli_help="only pairs where both records are still live (the dedupe_sweep pass)",
            ),
            Param(
                "floor",
                type="number",
                help="Minimum score to report (default: [dedupe].show_floor).",
                cli_help="minimum score to report (default: [dedupe].show_floor)",
            ),
            Param(
                "limit",
                type="integer",
                help="At most N pairs (0 = all).",
                cli_help="at most N pairs (0 = all)",
                default=0,
            ),
        ),
    ),
    Command(
        path=("link",),
        summary="settle a near-duplicate pair: say how one record relates to another",
        tool="ddflow_link",
        flag_exempt=LINK_FLAG_EXEMPT,
        render=_linked,
        description="Settle a near-duplicate pair: say how record `subject` relates to record "
        "`target`. `duplicate_of` / `extends` link them -- and MERGE two lessons (the "
        "target keeps both texts' tags and seen_in; the duplicate is superseded by it); "
        "`related` links both ways; `distinct` records a DISMISSAL ('I looked, these "
        "are different') so the pair never returns. One relation per call. Nothing is "
        "closed here except a merged lesson.",
        call=_link_call,
        payload=("subject", "relation", "target", "merged"),
        params=(
            Param(
                "subject",
                help="The record being related (the duplicate, for a merge).",
                cli_help="the record being related (the duplicate, for a merge)",
                required=True,
                positional=True,
            ),
            Param(
                "relation",
                help="extends | duplicate_of | related | distinct.",
                required=True,
                mcp_only=True,
            ),
            Param("target", help="The record it is related to.", required=True, mcp_only=True),
            *(
                Param(
                    rel,
                    cli_only=True,
                    default="",
                    metavar="ID",
                    exclusive="relation",
                    exclusive_required=True,
                    cli_help=_LINK_HELP.get(rel, f"subject {rel.replace('_', ' ')} ID"),
                )
                for rel in LINK_RELATIONS
            ),
            Param(
                "reason",
                help="Why, recorded with the link.",
                cli_help="why, recorded with the link",
                default="",
            ),
        ),
    ),
    Command(
        path=("decision", "add"),
        tool="ddflow_decision_add",
        flag_exempt=ANSWER_FLAG_EXEMPT,
        description="Record an architectural decision so the project stays consistent and the reasoning survives: HOW the software is built (a representation, boundary, library, invariant), settled by you or the operator. ALWAYS set `globs` to the code it governs, so it reaches whoever works those files; record `alternatives` too, or they get re-proposed.",
        call=lambda repo, a, agent: _api().decision_add(
            repo,
            _api().decisions.Draft(
                title=a.get("title", ""),
                decision=a.get("decision", ""),
                id=a.get("id", "") or "",
                context=a.get("context", "") or "",
                consequences=a.get("consequences", "") or "",
                alternatives=a.get("alternatives", "") or "",
                globs=a.get("globs", "") or "",
                tags=a.get("tags", "") or "",
                sources=a.get("sources", "") or "",
                status=a.get("status", "") or "accepted",
                by=a.get("by", "") or "",
                item=a.get("item", "") or "",
                supersedes=a.get("supersedes", "") or "",
                answer=_answer(a),
            ),
            agent=agent,
        ),
        # `{"id": "..."}` — what `ddflow decision add --json` has always printed.
        payload=("id",),
        params=(
            Param(
                "id",
                help="Stable id, e.g. 'D1'. Choose one: a generated id cannot be cited in advance.",
                cli_help="",
                default="",
            ),
            Param(
                "title", help="The decision as a one-line statement.", cli_help="", required=True
            ),
            Param(
                "decision",
                help="What was DECIDED (not what was discussed).",
                cli_help="what was DECIDED (not what was discussed)",
                required=True,
            ),
            Param(
                "context",
                help="The forces: why a decision was needed at all.",
                cli_help="the forces: why a decision was needed",
                default="",
            ),
            Param(
                "consequences",
                help="What it costs, including what it makes harder.",
                cli_help="what it costs, incl. what it makes harder",
                default="",
            ),
            Param(
                "alternatives",
                help="What was rejected, and why.",
                cli_help="what was rejected, and why",
                default="",
            ),
            Param(
                "globs",
                help="Comma-separated paths this governs.",
                cli_help="the code this governs; without it the decision can only be found by search",
                default="",
            ),
            Param("tags", help="Comma-separated tags.", cli_help="", default=""),
            Param(
                "sources",
                help="Where this came from, comma-separated: an ADR path, a URL, a commit sha (so an audit can check it exists).",
                cli_help="where this came from: an ADR path, a URL, a commit sha (comma-separated)",
                default="",
            ),
            Param("item", help="The task it arose from.", cli_help="", default=""),
            Param(
                "by",
                help="'operator' or 'agent' or a name.",
                cli_help="operator | agent | a name",
                default="",
            ),
            Param(
                "status",
                help="proposed | accepted (default) | superseded. 'proposed' is honest about a decision the operator has not ratified.",
                cli_help="",
                default="accepted",
                choices=("proposed", "accepted", "superseded"),
            ),
            Param("supersedes", help="Comma-separated ids this replaces.", cli_help="", default=""),
            *ANSWER_PARAMS,
        ),
        tool_order=(
            "id",
            "title",
            "decision",
            "context",
            "consequences",
            "alternatives",
            "globs",
            "by",
            "supersedes",
            "item",
            "tags",
            "status",
            "sources",
            "relation",
            "check_only",
        ),
    ),
    Command(
        path=("decision", "list"),
        tool="ddflow_decision_list",
        description="Every architectural decision in force. Superseded ones are "
        "hidden unless you ask for them — they are kept, never deleted, "
        "because how the architecture got here is what a rebuild needs.",
        call=lambda repo, a, agent: _api().decision_list(
            repo,
            all=bool(a.get("all")),
            since=a.get("since", "") or "",
            limit=int(a["limit"]) if a.get("limit") else None,
        ),
        payload="rows",
        params=(
            Param("all", type="boolean", help="Include superseded decisions.", cli_help=""),
            Param(
                "since",
                help="Only those recorded at or after this ISO date.",
                cli_help="only decisions recorded at or after this ISO date",
            ),
            Param(
                "limit",
                type="integer",
                help="Newest decisions returned (default 25; 0 = all).",
                cli_help="the newest N (0 = all)",
            ),
        ),
    ),
    Command(
        path=("decision", "show"),
        render=_decision_text,
        tool="ddflow_decision_show",
        description="Read ONE architectural decision in full — its context, what was decided, "
        "the consequences, and what was rejected. `ddflow_decision_list` gives "
        "you the titles; this is what you read before working against one, and "
        "especially before proposing something it already considered.",
        call=lambda repo, a, agent: _api().decision_show(repo, a["id"]),
        payload="decision",
        params=(Param("id", help="Decision id.", cli_help="", positional=True),),
    ),
    Command(
        path=("decision", "search"),
        reason="covered by ddflow_recall, which searches decisions along with everything else the project remembers — one search beats five",
        params=(
            Param("query", positional=True),
            Param("limit", type="integer", default=5),
        ),
    ),
    Command(
        path=("decision", "applicable"),
        render=_applicable_text,
        summary="decisions governing an item's declared files",
        tool="ddflow_decision_applicable",
        description="The architectural decisions that govern a specific item's declared files. "
        "CALL THIS BEFORE IMPLEMENTING: it is how a decision reaches the person "
        "writing the code, without them having to know it exists. Returns "
        "project-wide decisions too.",
        call=lambda repo, a, agent: _api().decision_applicable(repo, a["id"]),
        payload=("applicable", "project_wide"),
        params=(Param("id", help="Item id.", cli_help="", positional=True),),
    ),
    Command(
        path=("decision", "supersede"),
        render=lambda out, a: f"{out.data['id']} superseded by {out.data['by']}",
        tool="ddflow_decision_supersede",
        description="Mark a decision replaced by a newer one. Decisions are never "
        "edited or deleted; a reversal is a new decision that names the "
        "old one.",
        call=lambda repo, a, agent: _api().decision_supersede(
            repo, a["id"], by=a.get("by", "") or "", reason=a.get("reason", "") or "", agent=agent
        ),
        payload=("id", "by"),
        params=(
            Param("id", help="The decision being replaced.", cli_help="", positional=True),
            Param(
                "by",
                help="The decision that replaces it.",
                cli_help="the decision that replaces it",
                required=True,
            ),
            Param("reason", help="Why it changed.", cli_help="", default=""),
        ),
    ),
)


BY_TOOL = by_tool(COMMANDS)
