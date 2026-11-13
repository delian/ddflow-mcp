"""The session-start brief: every section, budgeted and trimmed to fit."""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

from ...config import Config
from ...core import clock
from ...core import provenance as PV
from ...core.model import State
from ...core.records import GateOutcome
from ...core.schedule import Plan
from ...core.textcut import clip
from ...core.tier import tier_of
from ...infra.worktree import load_path
from ...services import jobs as J
from ...services.completion import changed_paths, readme_report
from ...services.gates import (
    load_gates,
    local_command_notes,
    not_applicable,
    pipeline_for,
    required_gates,
)
from ...services.pipeline_diff import effective_pipeline
from .report import _CAPPED, _HOLDING, _RECOVERY_SHOWN, _UNHELD, _UNMEASURED, recovery_band


def _own_recovery(bands: dict[str, list], item: str) -> list:
    """The focused item's recovery entries, in band order (usually one: a stale_running
    item is exactly the crash this brief resumes)."""
    own = []
    for what, band in bands.items():
        for r in band:
            if r.item == item:
                own.append((what, r))
    return own


def _append_entry(out: list[str], what: str, r, state: State | None, cfg: Config | None) -> None:
    """One recovery entry's name line and, with a live state, its resume status line."""
    out.append(f"- **{r.item}** ({r.kind}, was {r.holder}): {r.advice}")
    if state is not None:
        out.append(_resume_line(state, cfg, r, what))


def _brief_recovery(
    out: list[str],
    recovery: list,
    *,
    state: State | None = None,
    cfg: Config | None = None,
    item: str = "",
) -> list[str]:
    """The RESUME block: the recoverable entries, with what was interrupted and whether
    ddflow has resumed it -- reserved from the section-trimming pass, but capped to half the
    budget by `brief()` (B027549013a), which names the overflow here with a `ddflow recover`
    note (folded B-res-brief-recovery-link). Returns only the lines THIS call appended (not
    the lines ``out`` already held: duck #1).

    When a brief is ABOUT one item AND that item is itself among the recoverable
    entries, the item's own entry renders FIRST and every OTHER entry collapses to one
    count line, so reserving the slice cannot crowd the focused item's own "Current"
    detail into a ``[N more cut]`` (B-brief-recover-eats-budget). An item that has no
    recovery entry of its own keeps the per-band list: those are OTHER items' crashes,
    and warning about them first is the slice's job (DX-recover-brief-slice)."""
    start = len(out)
    bands: dict[str, list] = {_HOLDING: [], _UNMEASURED: [], _UNHELD: []}
    quiet = 0
    capped = 0
    for r in sorted(recovery, key=lambda r: (r.kind, r.item)):
        band = recovery_band(r)
        if band is _CAPPED:
            # Past the measurement cap, after every higher-signal tree was measured: it is
            # still unknown (may hold uncommitted work, never clean), so it must NOT read
            # "nothing to salvage" -- but it is also not the "could not be measured" alarm.
            # Counted as its own note, and `recover` measures every tree.
            capped += 1
        elif band is not None:
            bands[band].append(r)
        else:
            quiet += 1
    count = (
        f"{quiet} leftover(s) with nothing to salvage: `ddflow recover` lists them."
        if quiet
        else ""
    )
    capped_note = (
        f"{capped} tree(s) past the measurement cap (unmeasured — may hold uncommitted "
        f"work): `ddflow recover` measures them."
        if capped
        else ""
    )
    quiet_notes = [n for n in (count, capped_note) if n]
    if not any(bands.values()):
        for note in quiet_notes:
            out += [f"_{note}_", ""]
        return out[start:]
    out += [
        "## ⚠ RESUME: recoverable work found",
        "",
        "Handle these BEFORE starting anything new — a crashed agent's worktree often",
        "holds finished work that exists nowhere else.",
        "",
    ]
    own = _own_recovery(bands, item)
    if own:
        # A brief about the crashed item itself: its own entry first, the rest counted.
        # The item's own entries are capped too (a stray/repeated scan could yield several
        # for one item): the same _RECOVERY_SHOWN bound the every-band loop applies.
        for what, r in own[:_RECOVERY_SHOWN]:
            _append_entry(out, what, r, state, cfg)
        # "other" = entries for a DIFFERENT item: subtract every own entry, not the shown
        # slice, so a repeated own scan is never relabelled as an unrelated recovery.
        others = sum(len(band) for band in bands.values()) - len(own)
        if others:
            label = "entry" if others == 1 else "entries"
            out.append(f"- _{others} other recoverable {label}: `ddflow recover` lists them._")
    else:
        for what, band in bands.items():
            for r in band[:_RECOVERY_SHOWN]:
                _append_entry(out, what, r, state, cfg)
            if len(band) > _RECOVERY_SHOWN:
                more = len(band) - _RECOVERY_SHOWN
                out.append(f"- _{more} more {what}: `ddflow recover` lists them._")
    for note in quiet_notes:
        out.append(f"- _{note}_")
    out.append("")
    return out[start:]


def _last_prompt_of(state: State, rec) -> str:
    """The interrupted holder's last recorded prompt that NAMES THIS ITEM (a prompt on
    another item is that item's context, never borrowed here: duck #1), clipped to one
    line; "" when the holder recorded none for it."""
    holder = rec.holder if rec.holder and rec.holder != "(none)" else ""
    if not holder:
        return ""
    prompts = [
        p
        for s in sorted(state.sessions.values(), key=lambda s: s.started_at)
        if s.agent == holder
        for p in s.prompts
        if p.get("item") == rec.item
    ]
    pick = prompts[-1] if prompts else None
    if not pick:
        return ""
    text = " ".join((pick.get("text") or "").split())
    return clip(text, 90).strip('"') or ""


def _gate_facts(state: State, cfg: Config, item: str) -> str:
    """`gates done: research, rules` and `next gate: implement` for the interrupted
    item: what was left undone is what a fresh agent must know first."""
    it = state.items.get(item)
    if it is None:
        return ""
    pipe = list(pipeline_for(it, cfg))
    needed = set(required_gates(cfg))
    done = [g for g in pipe if GateOutcome.settled(it.gate_outcome(g), g in needed)]
    next_up = next(
        (g for g in pipe if not GateOutcome.settled(it.gate_outcome(g), g in needed)), ""
    )
    facts = []
    if done:
        facts.append("gates done: " + ", ".join(done))
    if next_up:
        facts.append("next gate: " + next_up)
    return " · ".join(facts)


def _resume_line(state: State, cfg: Config, r, what: str) -> str:
    """One indented line per entry: ddflow has NOT resumed it (its safe pass records, it
    never takes over a tree that may hold work: D-orphan-recovery), and what the item
    still owes. `r.advice` already says the exact command. The status is keyed to the
    BAND the entry renders in (the same `recovery_band` truth the line above shows), so
    the words can never contradict the line they sit under whatever the raw flags say
    (duck #4/#5: a held entry cannot be described as unheld, and vice versa)."""
    bits = _gate_facts(state, cfg, r.item) if cfg is not None else ""
    prompt = _last_prompt_of(state, r)
    if prompt:
        bits = (
            (bits + ' · last prompt: "' + prompt + '"') if bits else 'last prompt: "' + prompt + '"'
        )
    if what == _UNHELD:
        # Nobody holds the RUNNING item RIGHT NOW (the schedule's own predicate): the
        # "was <holder>" above names the lost holder, "now" says no claim is live.
        status = "NOT resumed: nobody holds it now — the claim command above takes it over"
    elif what == _UNMEASURED:
        status = (
            "NOT resumed: COULD NOT MEASURE — treat it as holding work until one answer decides"
        )
    else:
        status = "NOT resumed: ddflow applies only safe actions, this one is not safe — one answer decides"
    return status + (" · " + bits if bits else "")


def _brief_current(
    out: list[str],
    state: State,
    cfg: Config,
    item: str,
    repo: Path | None = None,
    *,
    held: list[str] | None = None,
    suggested: bool = False,
    loops: list | None = None,
) -> None:
    """The item this brief is about. "Current" only when it is the agent's own -- named
    with `--item` or held under its lease; the queue's top pick for an agent holding
    nothing is "Suggested next", because calling it Current told an agent it was working
    on something it never claimed (B226d8db6e8)."""
    it = state.items.get(item)
    if not it:
        return
    held = held or []
    if len(held) > 1:
        out += [
            f"You hold {len(held)} leases: " + ", ".join(f"`{h}`" for h in held) + ". "
            "The most recent claim is below; `ddflow brief --item <id>` for another.",
            "",
        ]
    heading = "Suggested next" if suggested else "Current"
    out += [f"## {heading}: {it.id} — {it.title}", ""]
    if suggested:
        out += ["_You hold no lease. This is the top ready item: `ddflow claim` it first._", ""]
    out.append(
        f"- kind `{it.kind}` · state `{it.state}`"
        + (f" · held by `{it.lease.holder}`" if it.lease else "")
    )
    if it.reopened:
        last = it.reopened[-1]
        out += [
            f"- **REOPENED by verification** ({clock.fmt_date(last['at'])}, {len(it.reopened)}x): "
            f"{last['reason']}",
            *[
                f"  - [{c.get('status', '?')}] {c.get('id', '?')}: {c.get('detail', '')}"
                for c in last["claims"][:5]
            ],
            "  _Its earlier gates were cleared: re-do the work and re-run them._",
        ]
    tier = tier_of(it.tags)
    if tier:
        out.append(f"- tier `{tier}` (advisory: the model to dispatch a subagent on)")
    if it.needs:
        out.append("- needs: " + ", ".join(f"`{n}`" for n in it.needs))
    if it.globs:
        out.append("- writes: " + ", ".join(f"`{g}`" for g in it.globs))
    if it.worktree:
        # Resolved for display: a relative path is portable in the LOG and ambiguous in
        # a brief, where the reader is about to `cd` to it.
        out.append(
            f"- worktree: `{load_path(repo, it.worktree) if repo else it.worktree}`"
            f" on `{it.branch}`"
        )
    required = required_gates(cfg)
    pipe = effective_pipeline(repo, cfg, it)
    off = (
        not_applicable(
            it,
            pipe,
            load_gates(repo, cfg),
            required=required,
            diff=lambda: changed_paths(repo, it),
        )
        if repo is not None
        else {}
    )
    todo = [g for g in pipe if g not in off and not it.gate_satisfied(g, g in required)]
    # Before `gates remaining`: a squeezed brief trims from the bottom, and this is the
    # line that must not be the first to go.
    for g, note in local_command_notes(it, [(g, it.gate_outcome(g)) for g in pipe]).items():
        out.append(f"- {g} {note}")
    out.append("- gates remaining: " + (" → ".join(todo) if todo else "none — ready to complete"))
    if repo is not None and not suggested:
        readme = readme_report(state, cfg, item, repo=repo)
        if readme:
            out.append(f"- docs: {readme}")
    for f in loops or []:
        if f.item == item and f.kind == "repeated_failure":
            out.append(f"- {f.render()}")
    if it.body:
        out += ["", textwrap.indent(it.body.strip()[:700], "> ")]
    out.append("")


def cap_held(n: int, others_ready: bool) -> str:
    """ "N more are ready but held by" -- or, with nothing else ready, "N are ready but
    held by": "more" after "Nothing ready" read as a contradiction."""
    return f"{n} {'more ' if others_ready else ''}{'is' if n == 1 else 'are'} ready but held by"


#: How many cap-held ids the brief names before "...".
_CAPPED_SHOWN = 8


def _brief_ready(out: list[str], plan: Plan) -> None:
    out += ["## Ready now", ""]
    if plan.ready:
        for r in plan.ready:
            out.append(
                f"- **{r.id}** — {r.title}"
                + (f" [tier:{tier_of(r.tags)}]" if tier_of(r.tags) else "")
                + (f" (writes {', '.join(r.globs)})" if r.globs else "")
            )
        if len(plan.ready) > 1:
            out += [
                "",
                f"These {len(plan.ready)} are independent: they can run in parallel worktrees.",
            ]
    else:
        out.append("_Nothing ready._")
    if plan.parallel_line:
        out += ["", plan.parallel_line]
    if plan.capped:
        # Ready in every sense but the cap: listed apart from "Blocked", where six lines
        # of dependency waits buried them and a reader concluded only the ready few
        # could ever start (Bdcce70d036).
        shown = ", ".join(f"`{i}`" for i in plan.capped[:_CAPPED_SHOWN])
        more = " ..." if len(plan.capped) > _CAPPED_SHOWN else ""
        n = len(plan.capped)
        out += ["", f"{cap_held(n, bool(plan.ready))} {plan.cap_note}: {shown}{more}"]
    blocked = [b for b in plan.blocked if b.item not in set(plan.capped)]
    if blocked:
        out += ["", "## Blocked (and why)", ""]
        out += [f"- `{b.item}` — {b.reason}: {b.detail}" for b in blocked[:6]]


#: How much of a decision's text the brief quotes; `ddflow decision show` has the rest.
BRIEF_DECISION_CHARS = 280


def _brief_decisions(out: list[str], decisions: list) -> None:
    """Architectural decisions governing the current item's files.

    Placed ABOVE lessons on purpose: a decision is binding and a lesson is advice, and
    an agent skimming a brief should meet the binding thing first. Surfaced by glob
    match rather than by search, so a decision reaches whoever is about to write the
    code it governs without their having to suspect it exists.
    """
    if not decisions:
        return
    out += [
        "",
        "## Architectural decisions governing these files",
        "",
        "_Binding unless the operator says otherwise. A superseded decision names "
        "its replacement — follow the replacement._",
        "",
    ]
    if any(len(d.decision) > BRIEF_DECISION_CHARS for d in decisions):
        out += [
            f"_Each is cut at {BRIEF_DECISION_CHARS} characters: `ddflow decision show <id>` "
            "for the whole of one._",
            "",
        ]
    for d in decisions:
        # The title and decision are somebody's words: fenced as data, with who recorded
        # them. A decision keeps its status whoever wrote it (D-lean-and-trusted, 3);
        # `trust=` says whether that was the operator. Cut BEFORE fencing (B1472311a63):
        # one decision ran to 2301 characters, half the whole brief's budget.
        text = d.decision
        if len(text) > BRIEF_DECISION_CHARS:
            text = text[:BRIEF_DECISION_CHARS].rsplit(" ", 1)[0] + " …"
        line = "- " + PV.fence("decision", d.id, f"**{d.title}** — {text}", PV.decision_origin(d))
        if d.superseded_by:
            line += f"  ⚠ SUPERSEDED by {d.superseded_by}"
        out.append(line)
        if d.alternatives:
            out.append(
                "  - rejected: "
                + PV.fence("decision", d.id, d.alternatives[:160], PV.decision_origin(d))
            )


#: How many of other items' jobs on another host every brief lists in full, and how many
#: of their exited or killed jobs a brief about NO item (nothing held, nothing ready)
#: lists; the rest of each collapse to one count line. A brief about an item -- named,
#: held or suggested -- lists none of the exited or killed ones.
BRIEF_OTHER_JOBS = 5


def _brief_jobs(out: list[str], state: State, item: str = "", agent: str = "") -> None:
    """Long-running jobs nobody has recorded as ended, with their LIVE status.

    Right after recovery: a job still running is the most expensive thing to restart by
    accident, and one that died unrecorded is work to collect or redo -- both are what
    the next session must know before it picks anything up.

    Bounded (B8114a8b531): a job running here, every job of the item the brief is
    about and every job the brief's own ``agent`` started (B1472311a63) are listed in
    full. Other items' jobs on another host (they may be running,
    nothing here can tell) are listed up to ``BRIEF_OTHER_JOBS``, newest first; their
    exited or killed jobs -- dozens, in a long-lived project -- are listed only by a brief
    about no item, and the rest of each kind collapse to one count line. Listed one by
    one they filled the whole token budget ahead of the item's own section, and the
    brief was cut before it said anything about the item.
    """
    pending = [j for j in state.jobs.values() if not j.ended_at]
    if not pending:
        return
    shown: list = []
    remote: list = []  # other items' jobs on another host: may be running
    stale: list = []  # other items' jobs that exited or were killed
    for j in sorted(pending, key=lambda x: x.started_at):
        s = J.status(j)
        if s.state == "running" or (item and j.item == item) or (agent and j.by == agent):
            shown.append((j, s))
        else:
            (remote if s.state == "elsewhere" else stale).append((j, s))
    keep_stale = 0 if item else BRIEF_OTHER_JOBS
    for bucket, keep in ((remote, BRIEF_OTHER_JOBS), (stale, keep_stale)):
        shown += bucket[len(bucket) - min(keep, len(bucket)) :]  # the newest `keep`
    # The item's own jobs first, then the agent's own: when the section is cut to its
    # share, the end goes first.
    shown.sort(
        key=lambda js: (
            not (item and js[0].item == item),
            not (agent and js[0].by == agent),
            js[0].started_at,
        )
    )
    out += ["## Long-running jobs", ""]
    for j, s in shown:
        tail = {
            "running": "WAIT for it; do not start it again",
            "exited": "collect its result, then `ddflow job end`",
            "gone": "it was killed: find out why before restarting",
            "elsewhere": "check it on that host",
        }.get(s.state, "")
        out.append(f"- **{j.item}** `{j.id}` — {s.state.upper()}: {s.detail}. {tail}")
    if hidden := len(remote) - min(BRIEF_OTHER_JOBS, len(remote)):
        out.append(
            f"- _{hidden} more job(s) of other items on other hosts (they may be running): "
            "`ddflow job list` shows them; check each on its host before restarting it._"
        )
    if hidden := len(stale) - min(keep_stale, len(stale)):
        whose = "other items'" if item else "older"
        out.append(
            f"- _{hidden} more unended job(s) ({whose}, exited or killed): `ddflow job list` "
            "shows them; `ddflow job end` records each one collected._"
        )
    out.append("")


def _brief_memories(out: list[str], memories: list) -> None:
    """Operational facts about this machine and repository, newest first.

    After the binding decisions and before the advice: a memory is what the source
    projects' agents were required to read FIRST every session ("this box has 8 H200s",
    "a uniform-zero eval usually means a sidecar 404"), and it costs the next agent a day
    to re-learn one. Said to be partial when it is, so a truncated list is not read as
    the whole store.
    """
    if not memories:
        return
    out += ["", "## Operational memory", "", "_Facts about this machine and repository._", ""]
    for m in memories:
        when = clock.fmt_date(m.origin_at or m.at)
        fenced = PV.fence("memory", m.id, m.text, PV.memory_origin(m))
        out.append(f"- {fenced}  `[{m.id}{' ' + when if when else ''}]`")


def _brief_lessons(out: list[str], cfg: Config, lessons: list[dict], state: State) -> None:
    if not lessons:
        return
    out += [
        "",
        "## Lessons that bear on this task",
        "",
        "_Ranked against the task text, not by recency._",
        "",
    ]
    for ls in lessons:
        rule = (ls.get("rule") or "")[: cfg.lessons.snippet_chars]
        rec = state.lessons.get(ls.get("id", ""))
        origin = PV.lesson_origin(rec) if rec else PV.Origin(PV.UNKNOWN)
        out.append(
            "- "
            + PV.fence("lesson", ls.get("id", ""), f"**{ls.get('title', '')}** — {rule}", origin)
        )


def _brief_skills(out: list[str], skills: list) -> None:
    if not skills:
        return
    out += [
        "",
        "## Project skills and rules that bear on this task",
        "",
        "_The project's own; ranked against the task text. Read the file, do not rely on this line._",
        "",
    ]
    out += [e.line() for e in skills]


#: Each section's share of the brief's budget, in the brief's order, when the whole does
#: not fit (B1472311a63). Cutting from the bottom alone let any long section -- other
#: items' jobs, leftovers, blocked items, eight decisions of 7309 characters -- take the
#: space of everything after it, so the rules and lessons never reached the agent. A
#: section that needs less leaves the rest to the sections that need more, in proportion
#: to their shares.
_SECTION_SHARE: dict[str, float] = {
    "recovery": 0.10,
    "jobs": 0.05,
    "current": 0.20,
    "ready": 0.12,
    "decisions": 0.25,
    "memories": 0.08,
    "rules": 0.02,
    "lessons": 0.15,
    "skills": 0.03,
}

#: Where the rest of a trimmed section is.
_SECTION_MORE: dict[str, str] = {
    "recovery": "`ddflow recover`",
    "jobs": "`ddflow job list`",
    "current": "`ddflow show {item}`",
    "ready": "`ddflow next`",
    "decisions": "`ddflow decision applicable {item}`",
    "memories": "`ddflow memory list`",
    "rules": "the rules file",
    "lessons": "`ddflow recall <topic>`",
    "skills": "`ddflow recall <topic>`",
}

#: The words a trimmed section ends with.
SECTION_TRIMMED = "cut to fit session.brief_max_tokens"

#: The least a section is allowed when trimmed: its heading and a line or two.
_SECTION_FLOOR = 160
#: How many left-out records a trimmed section names; the rest are counted.
_SECTION_NAMED = 6


def _record_id(line: str) -> str:
    """The id of the fenced record a brief line quotes, "" for none."""
    m = re.search(rf'<{PV.TAG} kind="[^"]*" id="([^"]*)"', line)
    return m.group(1) if m else ""


def _trim_section(name: str, lines: list[str], room: int, item: str) -> list[str]:
    """``lines`` cut at a line boundary to ``room`` characters, ending with one line that
    says how much is missing and where it is. Records left out are NAMED: an agent may
    not know a binding decision's text, but it must know the decision exists."""
    where = _SECTION_MORE.get(name, "`ddflow brief`").format(item=item or "<id>")
    kept: list[str] = []
    used = 0
    for i, line in enumerate(lines):
        rest = lines[i:]
        seen = {_record_id(k) for k in kept}
        ids = list(dict.fromkeys(r for r in map(_record_id, rest) if r and r not in seen))
        entries = sum(1 for ln in rest if ln.startswith("- ")) or len([ln for ln in rest if ln])
        named = f": {', '.join(ids[:_SECTION_NAMED])}" if ids else ""
        if len(ids) > _SECTION_NAMED:  # bounded: hundreds of ids would overrun the share
            named += f" and {len(ids) - _SECTION_NAMED} more"
        marker = f"- _[{entries} more {SECTION_TRIMMED}{named}; {where}]_"
        # The heading (and the blank lines around it) stays: a section cut to nothing
        # would read as a section with nothing in it.
        heading = not any(k.startswith("#") for k in kept) and (
            not line.strip() or line.startswith("#")
        )
        if used + len(line) + 1 + len(marker) + 1 > room and not heading:
            if any(ln.strip() for ln in rest):
                kept.append(marker)
            return kept
        kept.append(line)
        used += len(line) + 1
    return kept


def _allowances(sections: list[tuple[str, list[str]]], sizes: list[int], room: int) -> list[int]:
    """Each section's share of ``room``, raised to the floor, never more than it needs --
    and never more than ``room`` in all (critic): what the floors add is taken back from
    the sections above their floor, in proportion. Floors that cannot all fit fall back to
    the plain shares, which sum to ``room``."""
    want = [
        min(size, max(int(room * _SECTION_SHARE.get(name, 0.05)), _SECTION_FLOOR))
        for (name, _), size in zip(sections, sizes, strict=True)
    ]
    over = sum(want) - room
    if over <= 0:
        return want
    spare = [w - min(size, _SECTION_FLOOR) for w, size in zip(want, sizes, strict=True)]
    if sum(spare) < over:
        return [
            min(size, int(room * _SECTION_SHARE.get(name, 0.05)))
            for (name, _), size in zip(sections, sizes, strict=True)
        ]
    total = sum(spare)
    return [w - -(-over * sp // total) for w, sp in zip(want, spare, strict=True)]


def _fit_sections(
    sections: list[tuple[str, list[str]]], room: int, item: str
) -> tuple[list[str], bool]:
    """The sections joined, each trimmed to its share when the whole exceeds ``room``
    characters. Returns the lines and whether anything was cut."""
    sizes = [len("\n".join(lines)) + 1 if lines else 0 for _, lines in sections]
    if sum(sizes) <= room:
        return [line for _, lines in sections for line in lines], False
    allow = _allowances(sections, sizes, room)
    slack = room - sum(allow)
    # What one section leaves goes to those that still want more, by share; a section
    # that gets all it wants returns the rest to the pool for another pass.
    while slack > 0:
        wanting = [i for i, size in enumerate(sizes) if size > allow[i]]
        if not wanting:
            break
        weight = sum(_SECTION_SHARE.get(sections[i][0], 0.05) for i in wanting)
        given = 0
        for i in wanting:
            share = _SECTION_SHARE.get(sections[i][0], 0.05) / weight
            extra = min(sizes[i] - allow[i], max(1, int(slack * share)), slack - given)
            allow[i] += extra
            given += extra
        slack -= given
    out: list[str] = []
    trimmed = False
    for (name, lines), size, room_i in zip(sections, sizes, allow, strict=True):
        if size <= room_i:
            out += lines
        else:
            out += _trim_section(name, lines, room_i, item)
            trimmed = True
    return out, trimmed


def _assemble(
    prefix: list[str],
    sections: list[tuple[str, list[str]]],
    chars: int,
    note: str,
    charter: str,
    item: str,
) -> str:
    """The brief's text within ``chars``: the sections cut to what the charter (set aside
    first, appended whole) leaves, then the backstop for a heading that alone overran its
    share -- the note counts too, the backstop cuts the sections, never the charter, and
    ``prefix`` (the head and the RESUME slice) is never cut (DX-recover-brief-slice): a
    squeeze takes from the budgeted sections, never from recoverable work. When even the
    note no longer fits after the prefix (a RESUME slice that alone fills the budget),
    the slice stays and the note goes while a trimmed section's own marker still says
    where its rest is. With no marker left the note stays over the cap: the backstop can
    cut the body to nothing, and a pack that ends at its head must still say it was cut."""
    set_aside = len(charter) + 2 if charter else 0
    cap = chars - set_aside
    prefix_text = "\n".join(prefix)
    # Reserve the note's room only when the note could ever be appended (prefix + note
    # must fit below the cap): reserving it unconditionally charged the body for a note
    # that would be dropped, which starved sections that fit without it (duck/critic
    # _assemble findings).
    note_room = len(note) if len(prefix_text) + len(note) + 1 <= cap else 0
    room = chars - len(prefix_text) - note_room - 1 - set_aside
    body_lines, trimmed = _fit_sections(sections, max(1, room), item)
    body = "\n".join(body_lines)
    text = prefix_text + ("\n" + body if body else "")
    if len(text) + (len(note) if trimmed else 0) > cap:
        # The backstop cuts the BODY only: the head and the reserved RESUME slice hold.
        # With no note reserved (prefix + note alone exceed the cap), the body keeps
        # its place; nothing may cut the prefix.
        room = max(0, cap - len(prefix_text) - 1 - (len(note) if note_room else 0))
        if len(body) > room:  # a body already inside the room loses nothing here
            body = body[:room].rsplit("\n", 1)[0]
        text = prefix_text + ("\n" + body if body else "")
        trimmed = True
    if trimmed:
        text += note if len(text) + len(note) <= cap or SECTION_TRIMMED not in body else ""
    return text + ("\n\n" + charter if charter else "")


def brief(  # noqa: PLR0913 -- each section's input, all keyword-only; held/suggested decide the heading
    state: State,
    cfg: Config,
    plan: Plan,
    *,
    repo: Path | None = None,
    item: str = "",
    lessons: list[dict] | None = None,
    skills: list | None = None,
    rules: str = "",
    recovery: list | None = None,
    decisions: list | None = None,
    memories: list | None = None,
    held: list[str] | None = None,
    suggested: bool = False,
    reserve: int = 0,
    loops: list | None = None,
    agent: str = "",
    charter: str = "",
) -> str:
    """The session-start pack, under ``session.brief_max_tokens``.

    Ordered by what an agent must not miss: anything needing rescue first, then what
    it is doing, then what it may do next, then the lessons that bear on it. The RESUME
    slice (recoverable work) is reserved from the section-trimming pass but capped to half
    the budget (``B027549013a``), with the overflow named by a ``ddflow recover`` note;
    when the rest does not fit, each section is cut to its share of the budget
    (``_SECTION_SHARE``, B1472311a63) and says what it left out and where it is; what a
    short section does not use goes to the ones after it, head first, so the
    safety-critical head still survives a squeeze -- and no section ahead of them can
    cut the decisions, rules and lessons out. Cutting from the bottom remains only as
    the backstop.
    """
    sections: list[tuple[str, list[str]]] = [(name, []) for name in _SECTION_SHARE]
    # "recovery" leads _SECTION_SHARE but is no longer a trimmed section: the RESUME
    # slice is reserved and never cut by the budget (DX-recover-brief-slice), so it
    # leaves the budgeted set first (dict order is the literal order: recovery leads).
    sections.pop(0)
    part = dict(sections)
    reserved: list[str] = []
    _brief_recovery(reserved, recovery or [], state=state, cfg=cfg, item=item)
    _brief_jobs(part["jobs"], state, item, agent)
    if item:
        _brief_current(
            part["current"], state, cfg, item, repo, held=held, suggested=suggested, loops=loops
        )
    _brief_ready(part["ready"], plan)
    _brief_decisions(part["decisions"], decisions or [])
    # `memories` is every LIVE one, newest first; how many to show is this view's call.
    shown = (memories or [])[: cfg.memory.brief_items]
    _brief_memories(part["memories"], shown)
    if shown and len(memories or []) > len(shown):
        part["memories"].append(
            f"- _{len(memories or []) - len(shown)} more: `ddflow memory list`, or "
            f"`ddflow recall <topic>`._"
        )
    if rules:
        title = "## Rules for these files" if charter else "## Project rules"
        part["rules"] += ["", title, "", *rules.strip().split("\n")]
    _brief_lessons(part["lessons"], cfg, lessons or [], state)
    _brief_skills(part["skills"], skills or [])

    head: list[str] = ["# ddflow brief", ""]
    if any(PV.TAG in line for _, lines in sections for line in lines):
        head[1:1] = [f"_{PV.DATA_RULE}_", ""]
    # `reserve`: tokens a caller will PREPEND (the new-reports block), so the whole stays
    # inside the budget and it is the bottom that gives way.
    budget = max(1, cfg.session.brief_max_tokens - reserve)
    note = (
        f"\n\n_[brief truncated at {budget} tokens "
        f"(session.brief_max_tokens); `ddflow show <id>` for detail]_"
    )
    # The RESUME slice opens the pack, before even its own heading line (duck #2): a
    # session that starts after a crash must see recoverable work in the first line.
    # B027549013a: the slice is reserved from the budget, but BOUNDED to half of it -- the
    # bound the reports block keeps -- so a focused brief on a busy repo's per-band list of
    # OTHER crashes cannot starve the item's own Current/decisions/lessons down to nothing.
    # (The charter is separately clamped by CH.block's own reserve.) `ddflow recover` lists
    # every entry beyond the bound.
    bound = max(_SECTION_FLOOR, budget * 2)
    if len("\n".join(reserved)) > bound:
        kept: list[str] = []
        used = 0
        for line in reserved:
            if used + len(line) + 1 > bound:
                break
            kept.append(line)
            used += len(line) + 1
        kept.append("")
        kept.append(
            "_more recoverable entries past the slice's bound: `ddflow recover` lists them._"
        )
        kept.append("")
        reserved = kept
    return _assemble(reserved + head, sections, budget * 4, note, charter, item)
