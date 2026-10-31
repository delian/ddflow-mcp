"""The concrete ``*_md`` views and the one VIEWS map that renders them."""

from __future__ import annotations

import re
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path

from ...config import Config
from ...core import clock
from ...core import progress as PR
from ...core.model import ABANDONED, BLOCKED, DONE, OUTCOME_MARK, REVIEW, RUNNING, State
from ...infra.fsio import FORMAT_LEVEL, replace_text
from ...services.gates import pipeline_for
from ...services.redact_report import redactor
from .head import GENERATED, NewerView, split_view, stamp_view

# Marks come from model.OUTCOME_MARK -- the board and the gate-status view must agree,
# and two copies of a lookup table are two copies that drift.


def _bar(done: int, total: int, width: int = 18) -> str:
    if total == 0:
        return "─" * width
    filled = round(width * done / total)
    return "█" * filled + "░" * (width - filled)


# The board's structure lives in core.progress (B-uni-tallies): the markdown here and the
# `board --json` rows are rendered from the one `board_rows`. Re-exported under the old
# names, which other modules import.
_depth = PR.depth
_nested = PR.nested
unphased = PR.unphased


def board(state: State, cfg: Config | None = None, *, phase: str = "") -> str:
    """The whole queue as a markdown board.

    The gate column is built from the CONFIGURED pipeline. It used to re-type the ten
    default gate ids, so a project that trimmed `gates.task_pipeline` to three got ten
    columns, seven of them permanently blank, under a caption naming gates it does not
    run. Passing `cfg=None` falls back to the defaults for callers that genuinely have
    no config to hand.
    """
    out = [GENERATED, "", "# Work queue", ""]
    default = list(Config().gates.task_pipeline)
    sections = PR.board_rows(
        state, (lambda t: pipeline_for(t, cfg)) if cfg is not None else (lambda t: default), phase
    )
    if not sections:
        out.append("_No phases yet. `ddflow phase add <id> --title '...'`_")
        return "\n".join(out)

    def table(rows: list[dict]) -> None:
        out.append("| | Task | State | Needs | Globs | Gates | Owner |")
        out.append("|---|---|---|---|---|---|---|")
        for r in rows:
            mark = {DONE: "x", RUNNING: "~", REVIEW: "r", BLOCKED: "!", ABANDONED: "-"}.get(
                r["state"], " "
            )
            gates = "".join(OUTCOME_MARK.get(o, " ") for o in r["gates"].values())
            indent = "&nbsp;&nbsp;&nbsp;&nbsp;" * r["depth"]
            # Every free-text cell goes through _cell (B2eaa1e5e8e): a `|` in a title
            # invented a column and a newline split the row.
            needs = ", ".join(_cell(n, cfg) for n in r["needs"]) or "—"
            globs = ", ".join(f"`{_cell(g, cfg)}`" for g in r["globs"]) or "—"
            owner = _cell(r["owner"], cfg) if r["owner"] else "—"
            out.append(
                f"| [{mark}] | {indent}**{_id_cell(r['id'], cfg)}** {_cell(r['title'], cfg)} | "
                f"{r['state']} | {needs} | {globs} | `{gates}` | {owner} |"
            )
        out.append("")
        caption = " · ".join(rows[0]["gates"])
        out.append(
            f"<sub>Gate column order: {caption}. "
            "`x` passed `!` failed `?` unavailable `~` partial `-` skipped</sub>"
        )
        out.append("")

    for sec in sections:
        # Ordered so a sub-task follows its parent, and indented by depth: a flat list
        # of "P1.T1, P1.T1a, P1.T1b" hides that two of them are halves of the first.
        # Abandoned tasks are SETTLED, not outstanding: out of the total (core.progress).
        n = sec.tally
        ph = sec.phase
        if ph is None:
            out.append("## Unphased — tasks under no phase")
            out.append("")
            bits = []
        else:
            out.append(f"## {ph.id} — {ph.title or '(untitled)'}")
            out.append("")
            bits = [f"`{ph.state}`"]
        bits.append(f"{_bar(n.done, n.live)} {n.done}/{n.live} tasks")
        if n.abandoned:
            bits.append(f"{n.abandoned} abandoned")
        if ph is not None and ph.needs:
            bits.append("needs " + ", ".join(f"`{x}`" for x in ph.needs))
        if ph is not None and ph.lease:
            bits.append(f"held by **{ph.lease.holder}**")
        out.append(" · ".join(bits))
        out.append("")
        if ph is not None and ph.body:
            out.append(textwrap.indent(ph.body.strip(), "> "))
            out.append("")
        if sec.rows:
            table(sec.rows)
    cp = sys.modules[__package__].critical_path(state, phase, cfg)
    if len(cp) > 1:
        out.append(
            f"**Critical path** ({len(cp)} deep — this, not the task count, "
            f"sets the wall-clock floor): " + " → ".join(f"`{c}`" for c in cp)
        )
        out.append("")
    return "\n".join(out)


def lessons_md(state: State, cfg: Config | None = None) -> str:
    out = [GENERATED, "", "# Lessons", ""]
    live = [x for x in state.lessons.values() if x.live]
    if not live:
        out.append("_None recorded._")
        return "\n".join(out)
    by_tag: dict[str, list] = {}
    for ls in sorted(live, key=lambda x: x.at, reverse=True):
        for tag in ls.tags or ["untagged"]:
            by_tag.setdefault(tag, []).append(ls)
    for tag in sorted(by_tag):
        out.append(f"## {_redact(tag, cfg)}")
        out.append("")
        for ls in by_tag[tag]:
            out.append(f"### {_redact(ls.title, cfg)}")
            out.append("")
            out.append(f"**Rule.** {_redact(ls.rule, cfg)}")
            if ls.why:
                out.append("")
                out.append(f"**Why.** {_redact(ls.why, cfg)}")
            if ls.how:
                out.append("")
                out.append(f"**How to apply.** {_redact(ls.how, cfg)}")
            if ls.seen_in:
                out.append("")
                out.append(f"**Seen in:** {', '.join(_redact(x, cfg) for x in ls.seen_in)}")
            out.append("")
    return "\n".join(out)


def _fold(text: str, cfg: Config | None = None) -> str:
    """Redacted, with every CommonMark line ending (LF, CR, CRLF) folded to a space: the
    part of a table cell's escaping every cell shares."""
    return re.sub(r"\r\n?|\n", " ", _redact(text, cfg))


def _cell(text: str, cfg: Config | None = None) -> str:
    """One markdown table cell: redacted, line breaks folded, `|` escaped so a title
    cannot split the row into extra columns (B2eaa1e5e8e).

    Every CommonMark line ending is folded -- a lone CR is one too, and a raw one split
    the row just like LF. A GFM row splitter reads a doubled backslash as an escaped
    backslash, so the backslashes right before a `|` are doubled too: `a\\|b` written as
    `a\\\\|b` left the pipe a delimiter again (roborev on fix-B2eaa1e5e8e). Nothing else
    is escaped: a title is markdown (its backticks are meant), and escaping every
    backslash turned an author's `\\*x\\*` into emphasis (roborev on 72565c2d).
    """
    return re.sub(r"(\\*)\|", lambda m: m.group(1) * 2 + "\\|", _fold(text, cfg)).strip()


def _id_cell(text: str, cfg: Config | None = None) -> str:
    """An item id for the board, where the row wraps it in `**...**`. An id is a literal
    name, not markdown: a backslash or `*` in it closed or broke the bold span and
    re-parsed the rest of the row (roborev on ded56144), so those, backticks and the
    pipe are all escaped."""
    return re.sub(r"([\\*`|])", r"\\\1", _fold(text, cfg)).strip()


def bugs_md(state: State, cfg: Config | None = None) -> str:
    """Every bug, newest change first, with the columns a scanner wants: its state, the
    item it is against, what guards the fix and the lesson the close recorded."""
    out = [GENERATED, "", "# Bugs", ""]
    rows = sorted(
        state.bugs.values(),
        key=lambda b: b.fixed_at or b.invalid_at or b.found_at or "",
        reverse=True,
    )
    if not rows:
        out.append("_None recorded._")
        return "\n".join(out)
    out.append("| Bug | State | Title | Item | Regression tests | Lesson |")
    out.append("|---|---|---|---|---|---|")
    for b in rows:
        tests = ", ".join(_cell(t, cfg) for t in b.regression_tests) or "—"
        out.append(
            f"| {b.id} | {b.resolution or 'open'} | {_cell(b.title or b.summary, cfg)} | "
            f"{_cell(b.item, cfg) or '—'} | {tests} | {_cell(b.lesson, cfg) or '—'} |"
        )
    out.append("")
    return "\n".join(out)


def decisions_md(state: State, cfg: Config | None = None) -> str:
    """The decisions IN FORCE, oldest first -- the order they were made in, which is the
    order the architecture was decided in."""
    out = [GENERATED, "", "# Decisions", ""]
    live = sorted((d for d in state.decisions.values() if d.live), key=lambda d: d.at)
    if not live:
        out.append("_None recorded._")
        return "\n".join(out)
    for d in live:
        out.append(f"## {d.id} — {_cell(d.title, cfg)}")
        out.append("")
        out.append(_redact(d.decision, cfg))
        if d.globs:
            out.append("")
            out.append(f"**Governs:** {', '.join(_redact(g, cfg) for g in d.globs)}")
        out.append("")
    return "\n".join(out)


def sessions_md(state: State, cfg: Config | None = None) -> str:
    """The sessions, newest first, one line each: when, who, and what it was for."""
    out = [GENERATED, "", "# Sessions", ""]
    rows = sorted(
        state.sessions.values(), key=lambda s: s.ended_at or s.started_at or "", reverse=True
    )
    if not rows:
        out.append("_None recorded._")
        return "\n".join(out)
    for s in rows:
        first = s.prompts[0].get("text", "") if s.prompts else ""
        when = clock.fmt_minute(s.ended_at or s.started_at or "")
        word = "ended" if s.ended_at else "open"
        title = _cell(first, cfg)[:120] or _cell(s.model, cfg)
        out.append(f"- **{s.id}** [{word}] {when} `{s.agent}` {title}")
    out.append("")
    return "\n".join(out)


#: How much of a lesson's rule stands in for a summary it does not have. One paragraph is
#: the unit: the first one, cut at this many characters.
_SUMMARY_FALLBACK_CHARS = 400


def _summary_of(ls, cfg: Config | None = None) -> str:
    if ls.summary:
        return _redact(ls.summary, cfg)
    first = (ls.rule or "").strip().split("\n\n", 1)[0]
    first = " ".join(first.split())
    if len(first) > _SUMMARY_FALLBACK_CHARS:
        first = first[:_SUMMARY_FALLBACK_CHARS].rsplit(" ", 1)[0] + " …"
    return _redact(first, cfg)


def lessons_summary_md(state: State, cfg: Config | None = None) -> str:
    """The distilled rulebook: every live lesson in one paragraph, grouped by tag.

    The file a reader opens INSTEAD of the corpus. Each entry is the lesson's own
    `summary` when it has one, else the first paragraph of its rule -- said, because a
    reader cannot otherwise tell a curated summary from a truncation. `imported` is a
    provenance tag, not a subject, so it never becomes a heading.
    """
    out = [
        GENERATED,
        "",
        "# Lessons — summary",
        "",
        "One paragraph per live lesson; `ddflow recall` or `LESSONS.md` for the full rule. "
        "An entry marked _(first paragraph)_ has no summary of its own yet — "
        "`ddflow lesson add --id <id> --summary ...` gives it one.",
        "",
    ]
    live = [x for x in state.lessons.values() if x.live]
    if not live:
        out.append("_None recorded._")
        return "\n".join(out)
    by_tag: dict[str, list] = {}
    for ls in sorted(live, key=lambda x: x.id):
        subject = [t for t in (ls.tags or []) if t not in ("imported", "summary")]
        by_tag.setdefault(subject[0] if subject else "general", []).append(ls)
    for tag in sorted(by_tag):
        out += [f"## {_redact(tag, cfg)}", ""]
        for ls in by_tag[tag]:
            mark = "" if ls.summary else " _(first paragraph)_"
            out.append(
                f"- **{_redact(ls.title.rstrip('.'), cfg)}.** {_summary_of(ls, cfg)}{mark} `[{ls.id}]`"
            )
        out.append("")
    return "\n".join(out)


def _redact(text: str, cfg: Config | None) -> str:
    """The free text of a record, with secrets and machine-local hosts removed.

    A view is COMMITTED, so a private address a record happened to cite is somebody's
    own service leaked into every clone -- and `test_repo_is_generic` refuses it there
    (bug Bb2c7cdd441). The export documents already ran through this redaction; the
    views did not.
    """
    if not text:
        return text
    # hostname="" and home="": a committed view must be byte-identical on every machine
    # (roborev on 9b6db6bc); the rendering host's own name and $HOME are not inputs. The
    # private addresses, LAN hosts and absolute home paths a record cites are still gone.
    return redactor("view", cfg or Config()).text(text).text


def research_md(state: State, cfg: Config | None = None) -> str:
    out = [
        GENERATED,
        "",
        "# Research log",
        "",
        "Every entry carries a verdict. An entry with no CONFIRMED or REFUTED label",
        "is a literature summary, not research, and is labelled THEORETICAL.",
        "",
    ]
    if not state.research:
        out.append("_None recorded._")
        return "\n".join(out)
    for r in sorted(state.research.values(), key=lambda x: x.at, reverse=True):
        out.append(f"## {_redact(r.question or r.id, cfg)}")
        out.append("")
        out.append(f"**Verdict: {r.verdict}**" + (f" · item `{r.item}`" if r.item else ""))
        out.append("")
        for label, val in (
            ("Claim", r.claim),
            ("Mechanism", r.mechanism),
            ("Falsifier", r.falsifier),
            ("Budget", r.budget),
        ):
            if val:
                out.append(f"- **{label}.** {_redact(val, cfg)}")
        if r.probe:
            out.append("")
            out.append("**Probe:**")
            out.append("")
            out.append("```console")
            out.append(f"$ {_redact(r.probe, cfg)}")
            if r.probe_output:
                out.append(_redact(r.probe_output.rstrip(), cfg))
            out.append("```")
        if r.sources:
            out.append("")
            out.append("**Sources:** " + ", ".join(_redact(s, cfg) for s in r.sources))
        out.append("")
    return "\n".join(out)


#: Every generated view: its filename and how to render it. The ONE map. There were two
#: -- this function's and a copy in `sessions.bundle` whose docstring said it used this
#: one -- and the copy already wrote different bytes. The pre-commit check
#: (`services/enforce.py::check_views`) reads it too, so a view added here is checked
#: without anyone remembering to.
VIEWS: tuple[tuple[str, Callable[[State, Config | None], str]], ...] = (
    ("QUEUE.md", board),
    ("LESSONS.md", lessons_md),
    ("LESSONS-SUMMARY.md", lessons_summary_md),
    ("RESEARCH.md", research_md),
)


def render_views(state: State, cfg: Config | None = None) -> dict[str, str]:
    """filename -> the exact text a view file must hold for this state."""
    return {name: stamp_view(fn(state, cfg).rstrip() + "\n") for name, fn in VIEWS}


def write_views(
    root: Path, state: State, cfg: Config | None = None, *, subdir: str = "docs/ddflow"
) -> list[Path]:
    """Regenerate every human view. Same state in, same bytes out -- which the commit
    hook now HOLDS it to (B18): a staged view that differs from this is refused."""
    d = Path(root) / subdir
    d.mkdir(parents=True, exist_ok=True)
    views = render_views(state, cfg)
    # All checked before any is written: a view a newer ddflow wrote is not downgraded.
    for name in views:
        p = d / name
        head = split_view(p.read_text("utf-8", errors="replace"))[0] if p.is_file() else None
        if head is not None and head.fmt > FORMAT_LEVEL:
            raise NewerView(str(p), head.fmt)
    written = []
    for name, new in views.items():
        p = d / name
        if not p.exists() or p.read_text("utf-8") != new:
            replace_text(p, new)
        written.append(p)
    return written
