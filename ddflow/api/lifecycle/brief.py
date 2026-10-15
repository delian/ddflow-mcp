"""`brief`: the session-start pack.

Part of `ddflow.api.lifecycle`, which re-exports the public names defined here."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

from ...core import clock
from ...core import outcome as O
from ...core import progress as PR
from ...core import provenance as PV
from ...core.budget import Budget, approx_tokens
from ...infra import worktree as W
from ...infra.store import Store
from ...services import choices as CHO
from ...services import cleanup as CL
from ...services import gates as G
from ...services import leases as L
from ...services import searchcore as SC
from ...services import shardmarks as SM
from ...services import skills as SK
from ...services import upgrade_check as UC
from ...services import upgrade_notice as UN
from ...services import upgrade_start as US
from ...services import waiting as WG
from ...services.export import select as export_select
from ...services.guidance import inject as GI
from ...services.guidance.kinds import unreconciled_with
from ...views import markdown as render_md
from .._base import _load
from ..reporting import new_reports
from .heartbeat import _waiters
from .planning import plan_for
from .ready import DEFAULT_CHECK_RECOVERY, _unknown_phase


def _waiting_on_you(repo: Path, held_ids: list[str]) -> str:
    """The brief's section on who the agent holds up, "" when nobody waits."""
    lines = [
        f"- {w['agent']} has waited {clock.fmt_age(w['waiting_s'])} for the files {iid} holds"
        + (f" (for {w['item']})" if w["item"] else "")
        for iid in held_ids
        for w in _waiters(repo, iid)
    ]
    if not lines:
        return ""
    return (
        "\n\n## Waiting on you\n\n"
        + "\n".join(lines)
        + "\n\nFirst come, first served: the oldest gets the files the moment you let go. "
        "Claim when you are ready to edit; do not hold file globs while only gates, "
        "reviews or roborev are pending -- finish and merge, or `ddflow release` it."
    )


def _waits_block(st, cfg) -> str:
    """The standing `lease.waiting` records (who waits for whom), "" when none."""
    waits = WG.rows(st, cfg, time.time())
    if not waits:
        return ""
    return (
        "\n\n## Waiting for files\n\n"
        + "\n".join(f"- {WG.line(w)}" for w in waits)
        + (
            "\n\nDo not poll and do not hold files you do not need: a waiter that cannot get "
            "its files releases the globs others wait for, or reports a cycle."
        )
    )


def _contest_block(repo: Path, cfg, log, st) -> str:
    """The contested claims, the claim THIS agent lost and the events a history rewrite took
    out of a shard (Ba24a8d32ee), "" when none (B2809f4213b).

    Leads the brief: a contested item is not work anyone may merge or complete until
    `resolve` settles it, and an agent whose claim was released must hear it before it
    builds on a branch that is no longer its item's."""
    lines = []
    for it in L.contested_items(st):
        row = L.contest_row(st, cfg, it)
        lines.append(f"- {it.id} is CONTESTED: {row['summary']}. `{row['resolve']}` settles it.")
    base = cfg.worktree.base_ref or W.default_branch(W.repo_root(repo))
    for iid, lost in sorted(L.lost_contests(log, log.agent_id).items()):
        it = st.items.get(iid)
        if it is None or it.removed or it.terminal:
            continue
        where = f"; branch {lost['branch']}" if lost["branch"] else ""
        if lost["branch"] and W.branch_exists(repo, lost["branch"]):
            r = W.git(repo, "rev-list", "--count", f"{base}..{lost['branch']}")
            n = r.out.strip() if r.ok and r.out.strip().isdigit() else "some"
            where = (
                f"; branch {lost['branch']} has {n} commit(s) not on {base}: "
                "keep it with git or port it"
            )
        lines.append(
            f"- your claim on {iid} was released: lost the contest to {lost['winner']}{where}."
        )
    out = "## Contested claims\n\n" + "\n".join(lines) + "\n\n" if lines else ""
    gone = SM.lost(repo, log)  # Ba24a8d32ee: events a history rewrite took out of a shard
    if gone:
        out += "## Lost events\n\n" + "\n".join(f"- {g.render()}." for g in gone) + "\n\n"
    return out


def _rules_block(governing) -> str:
    """The project's rules that govern the item's files, one fenced line each, "" for none."""

    rules = governing.records("rule")
    if not rules:
        return ""
    return "\n".join(
        "- "
        + PV.fence(
            "rule", r.id, f"**{r.title}** — {r.body}" if r.title else r.body, GI.origin_of(r)
        )
        for r in rules
    )


def _flag_unreconciled(d, rivals: dict[str, list[str]]):
    """B0dcccd8594: a decision written without seeing another on the same files is not yet a
    settled rule, and the brief says so where it says 'binding'. A copy, so the state is not
    touched; the flag rides in the title, which the view already prints."""
    if d.id not in rivals:
        return d
    flag = (
        f"[UNRECONCILED with {', '.join(rivals[d.id])}: written without seeing each other; "
        "ask which holds, then `ddflow decision supersede <old> --by <new>`] "
    )
    return replace(d, title=flag + d.title)


def _governing(cfg, st, item: str, rules: str) -> tuple[list, str]:
    """The decisions that govern the item, ranked by the one injection path (pinned first,
    then enforcement, scope specificity and priority), and the rules text with the project's
    own rules that govern its files LEADING it (the pointer line follows, so a trim from
    the bottom reaches the pointer first).

    The brief stays inside ``session.brief_max_tokens`` (B1472311a63), so what the other
    doors guarantee -- pinned guidance never trimmed -- holds here as order: pinned leads its
    section, and a section is cut from the bottom."""
    if not (item and item in st.items):
        return [], rules
    found = GI.for_item(cfg, st, item)
    block = _rules_block(found)
    rivals = unreconciled_with(st)
    decisions = [_flag_unreconciled(st.decisions[r.id], rivals) for r in found.records("decision")]
    return decisions, (block + "\n\n" + rules).strip() if block else rules


_REFUTED_SHOWN = 5


def _refuted_line(st) -> str:
    """The gates of unfinished items passed on refutation (D-unify 5), or "": a pass the
    operator may spot-check is not left for the agent to find out. Finished items are listed
    by `ddflow gate list --refuted`, not repeated in every brief."""
    rows = [r for r in G.refuted_passes(st) if r["state"] not in ("done", "abandoned")]
    if not rows:
        return ""
    shown = ", ".join(f"{r['item']}.{r['gate']}" for r in rows[:_REFUTED_SHOWN])
    more = f" (+{len(rows) - _REFUTED_SHOWN} more)" if len(rows) > _REFUTED_SHOWN else ""
    return "\n" + (
        f"Passed on refutation, not yet completed: {shown}{more}. Flagged for the operator's "
        f"spot-check: `ddflow gate list --refuted`."
    )


def _upgrade_notices(repo: Path, log, cfg, st, agent: str) -> str:
    """The upgrade lines that lead a brief, "" when none is due. The notice is first said
    once per version on this machine (D-upgrade-auto-check, D-self-upgrade 2) and then
    repeated by each brief until the project is upgraded: FIRST, so the hook, `ddflow brief`
    and `ddflow_brief` all lead with it whichever is asked first. This ddflow was upgraded
    (or the start report), then a newer release is out (proposed, never installed)."""
    notice = US.take_for_brief(repo, st)
    if not notice:
        notice = UN.line(repo, log, cfg, st, agent=agent)
        US.keep_notice(repo, notice)  # a hook-only path has no start report: repeat while behind
    release = UC.proposal(repo, cfg, consume=True)
    return "\n\n".join(x for x in (notice, release) if x)


def _sweep_finished(repo: Path, cfg, log, st, callers: tuple[Path | None, ...]) -> None:
    """Remove the leftover worktrees of done, abandoned and removed items finished long enough
    ago (B4757c428f4): `merge`, `abandon` and `remove` keep a tree their caller stood in. Best effort:
    a session start must never fail on housekeeping."""
    try:
        CL.sweep_finished(repo, cfg, log, st, callers=callers)
    except Exception:
        pass


def brief(
    repo: Path,
    *,
    item: str = "",
    phase: str = "",
    check_recovery: bool = DEFAULT_CHECK_RECOVERY,
    agent: str = "",
    called_from: Path | None = None,
    shell_cwd: Path | None = None,
) -> O.Outcome:
    """The budgeted reading pack: what to do next, and what governs it.

    Decisions reach the agent by GLOB rather than by search — the whole point is that they
    arrive without its having to suspect they exist.
    """

    log, cfg, _ = _load(repo, agent)
    store = Store(repo, cfg)
    st = store.ensure(log)
    _sweep_finished(repo, cfg, log, st, (called_from, shell_cwd))
    unknown = _unknown_phase(st, phase)
    if unknown:  # as `next` refuses it (Bc2acd426f4)
        return O.failed("brief", unknown, phase=phase, text="")
    p = plan_for(repo, log, cfg, st, purpose="view", phase=phase)
    # What THIS agent holds comes before what anyone may take (B226d8db6e8): the top
    # ready item was headed "Current" for an agent that had just claimed another one --
    # it is the queue's pick, not the agent's work. Most recent claim first.
    held = sorted(
        (
            (lease.acquired_at, iid)
            for iid, lease in st.active_leases(time.time(), cfg.lease.grace_s).items()
            if lease.holder == log.agent_id and not st.items[iid].removed
        ),
        reverse=True,
    )
    held_ids = [iid for _, iid in held]
    suggested = False
    if not item and held_ids:
        item = held_ids[0]
    elif not item and p.ready:
        item = p.ready[0].id
        suggested = True

    query = ""
    if item and item in st.items:
        target = st.items[item]
        query = f"{target.title} {target.body} {' '.join(target.tags)}"
    lessons = (
        SC.search_table(store, "lessons", query, cfg.session.brief_lesson_count) if query else []
    )

    project_skills = SK.relevant(repo, query) if query else []

    rules = ""
    for candidate in ("AGENTS.md", "CLAUDE.md", ".ddflow/RULES.md"):
        if (repo / candidate).is_file():
            rules = f"See `{candidate}` (loaded separately by your agent)."
            break

    recovery = L.scan(log, cfg, repo) if check_recovery else []
    decisions, rules = _governing(cfg, st, item, rules)

    live = sorted(
        (m for m in st.memories.values() if m.live),
        key=lambda m: (m.origin_at or m.at, m.at),
        reverse=True,
    )

    reports_block = ""
    if item and item in st.items and st.items[item].lease:
        reports_block = render_md.new_reports_block(
            item, new_reports(st, item, st.items[item].lease.acquired_at)
        )
        # The block is prepended to a budgeted brief: it may take at most half of it, so a
        # small `brief_max_tokens` still leaves the head of the brief itself.
        cap = Budget(cfg.session.brief_max_tokens, "tokens").chars // 2
        if len(reports_block) > cap:
            reports_block = reports_block[:cap].rsplit("\n", 1)[0] + "\n\n"

    # Prepended like the reports block, so budgeted like it (B1472311a63): uncounted, it
    # took the room the lessons were meant to have.
    undecided = CHO.brief_block(cfg)
    contest_text = _contest_block(repo, cfg, log, st)
    prepended = reports_block + (undecided + "\n" if undecided else "") + contest_text

    text = render_md.brief(
        st,
        cfg,
        p,
        loops=[f for f in PR.detect(log.read_all(), st, cfg) if f.item == item] if item else [],
        repo=repo,
        item=item,
        lessons=lessons,
        skills=project_skills,
        rules=rules,
        # All of it: the view ranks and counts (B20e103326b). Filtering on `salvageable`
        # dropped `stale_running` (False) and unmeasurable trees (None) -- the most
        # dangerous leftovers -- and the clean ones without a word.
        recovery=recovery,
        decisions=decisions,
        memories=live,
        held=held_ids,
        suggested=suggested,
        # views/markdown.py still has its own estimate until it moves onto core.budget
        # (B-uni-context-pack.2-pack): the same value, `max(1, len // 4)`.
        reserve=render_md._approx_tokens(prepended) if prepended else 0,
        agent=log.agent_id,
    )
    if undecided:
        text = undecided + "\n" + text
    text = reports_block + contest_text + text
    if p.finished:  # B28268eba1a: else only `doctor` ever said a phase was done
        text += "\n## Finished phases to close\n" + p.close_note() + "\n"
    text += _waiting_on_you(repo, held_ids)
    text += _waits_block(st, cfg)

    if line := export_select.brief_line(st, cfg):  # an agent-enabled document nobody has seen
        text += "\n" + line
    text += _refuted_line(st)
    if notice := _upgrade_notices(repo, log, cfg, st, agent):
        text = notice + "\n\n" + text
    if item and item in st.items:
        pr = st.items[item].pr
        if pr is not None and pr.review == "changes_requested" and pr.feedback:
            # FIRST, not appended: this is why the item is back, and an agent that fixes
            # something other than what the reviewer asked for starts another round.
            text = (
                f"## Review feedback on {item} (round {pr.rounds}, {pr.url})\n\n"
                f"Address this, commit, then `ddflow merge {item}` again -- it updates the "
                f"same request.\n\n{pr.feedback}\n\n" + text
            )
    return O.ok(
        "brief",
        brief=text,
        text=text,
        item=item,
        #: Whether `item` is the agent's work (False) or only the queue's pick (True).
        suggested=suggested,
        held=held_ids,
        ready=[i.id for i in p.ready],
        approx_tokens=approx_tokens(text),
    )
