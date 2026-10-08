"""Derived views of the log: `ddflow board`, `render`, `replay` and `rebuild`."""

from __future__ import annotations

from pathlib import Path

from ...core import outcome as O
from ...core import progress as PR
from .._base import _load


def rebuild(repo: Path, *, agent: str = "") -> O.Outcome:
    """Rebuild the sqlite projection from the log. The log is the source of truth; this
    is a cache, and saying how long it took is how you notice it has stopped being one."""
    import time

    from ...infra.store import Store

    log, cfg, _ = _load(repo, agent)
    t0 = time.time()
    st = Store(repo, cfg).rebuild(log)
    return O.ok(
        "rebuild",
        events=st.event_count,
        items=len(st.items),
        lessons=len(st.lessons),
        seconds=round(time.time() - t0, 2),
    )


def board(repo: Path, *, phase: str = "", agent: str = "") -> O.Outcome:
    """The queue as a board: the markdown document (`text`, what MCP and a terminal
    show) and the same rows as data (`phases`, `critical_path`) for `--json` -- which
    used to print the markdown (B1f1d4f9f54)."""
    from ...core.schedule import critical_path
    from ...services.gates import pipeline_for
    from ...views import markdown as render_md
    from ..lifecycle import _unknown_phase

    _log, cfg, st = _load(repo, agent)
    unknown = _unknown_phase(st, phase, phases_only=True)
    if unknown:  # as `next` refuses it, not an empty board (Bc2acd426f4)
        return O.failed("board", unknown, phase=phase, text="")

    # The same rows the markdown is rendered from (core.progress.board_rows), so the two
    # cannot disagree. Tasks under no phase (Bc896ea5d16) are their own section, never
    # silently dropped, and not shown when the board is narrowed to one phase.
    sections = PR.board_rows(st, lambda t: pipeline_for(t, cfg), phase)
    phases = [
        {
            "id": sec.phase.id,
            "title": sec.phase.title,
            "state": sec.phase.state,
            "needs": list(sec.phase.needs),
            "holder": sec.phase.lease.holder if sec.phase.lease else "",
            "tasks": sec.rows,
        }
        for sec in sections
        if sec.phase is not None
    ]
    loose = [r for sec in sections if sec.phase is None for r in sec.rows]
    return O.ok(
        "board",
        text=render_md.board(st, cfg, phase=phase),
        phase=phase,
        phases=phases,
        unphased=loose,
        critical_path=critical_path(st, phase),
    )


#: `render --show <name>` targets, and the function that produces each.
#:
#: `--show` exists so the MCP `resources/read` handler can serve these through one code
#: path like everything else. It used to fold the log itself -- a second data path in a
#: module whose whole premise is "one implementation, two doors".
_RENDERABLE = (
    "lessons",
    "lessons-summary",
    "research",
    "board",
    # The record kinds the MCP serves as `ddflow://bugs|decisions|sessions` (B196): a
    # resource is one of these documents, served through this one code path.
    "bugs",
    "decisions",
    "sessions",
)

#: Where `render` writes its views when no directory is given.
DEFAULT_RENDER_DIR = "docs/ddflow"


def render(
    repo: Path, *, show: str = "", out_dir: str = DEFAULT_RENDER_DIR, agent: str = ""
) -> O.Outcome:
    """One named view as a document, or every view written to disk.

    Two shapes by design, and both are pre-existing contracts: `--show` returns the
    document, and without it the answer is the list of files written.
    """
    from ...views import markdown as render_md

    log, cfg, st = _load(repo, agent)
    if show:
        if show not in _RENDERABLE:
            return O.failed(
                "render",
                f"unknown view {show!r}; known: {', '.join(sorted(_RENDERABLE))}",
                show=show,
                text="",
                files=[],
            )
        fn = {
            "lessons": render_md.lessons_md,
            "lessons-summary": render_md.lessons_summary_md,
            "research": render_md.research_md,
            "board": render_md.board,
            "bugs": render_md.bugs_md,
            "decisions": render_md.decisions_md,
            "sessions": render_md.sessions_md,
        }[show]
        # Every markdown renderer takes the config now (the views redact record text),
        # so this stays future-proof for one that does not. INSPECTED rather than
        # try/except'd, because a TypeError raised inside a renderer would otherwise be
        # caught and retried with the wrong arity.
        import inspect

        text = fn(st, cfg) if len(inspect.signature(fn).parameters) > 1 else fn(st)
        return O.ok("render", show=show, text=text, files=[])

    from ...config import Config
    from ...infra.store import Store

    # Written views are rendered from the config FILES only, never `DDFLOW_*` env
    # overrides. A file that gets committed must be reproducible from what is committed,
    # or the pre-commit check (B18) refuses a correct render made under one environment
    # and committed under another -- blaming a hand edit that never happened.
    committed_cfg = Config.load(repo, env={})
    try:
        files = render_md.write_views(
            repo, Store(repo, cfg).ensure(log), committed_cfg, subdir=out_dir
        )
    except render_md.NewerView as exc:
        return O.refused("render", str(exc))
    return O.ok("render", show="", text="", files=[str(f) for f in files])


def replay(repo: Path, *, out_dir: str = "", verify: bool = False, agent: str = "") -> O.Outcome:
    """Reconstruct the project's history from the log alone.

    `verify` re-resolves every recorded commit: a sha that no longer resolves means the
    reconstruction describes work that is not in the tree, which is the one way this
    document can be confidently wrong.
    """
    from ...services import sessions as session

    log, cfg, st = _load(repo, agent)
    steps = session.replay(log.read_all())
    problems = session.verify(st, repo, cfg) if verify else []
    if out_dir:
        files = session.bundle(st, steps, Path(out_dir), cfg, project=repo.name)
        text = "wrote:\n" + "\n".join(f"  {f}" for f in files)
        return O.ok(
            "replay", text=text, files=[str(f) for f in files], problems=problems, verified=verify
        )
    return O.ok(
        "replay",
        text=session.render_reconstruction(st, steps, project=repo.name),
        files=[],
        problems=problems,
        verified=verify,
    )
