"""`ddflow export` / `ddflow_export`: documents generated from the event log.

Thin: the work is `services.export.ops`; this layer turns it into one `Outcome` per call so
the CLI and the MCP tool describe the same result (D-export, D-export-selection).

``export`` acts on ONE document (``doc``) or on the selected set (``all_docs``) and does one
of: print (the default, capped), ``diff`` or ``check`` against the target, or write
(``update`` to the configured target, ``out`` to another repo-relative path). Nothing is
written unless ``update`` or ``out`` is given; the MCP tool maps ``write=true`` + ``path`` to
``out``. Exit codes: 0 done / fresh, 1 stale (``check``), 2 could not run or nothing selected,
3 refused (hand-edited file, unsafe path, unknown kind, filter the kind does not take).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import Config
from ..core import outcome as O
from ..services.export import ops
from ..services.export import registry as R
from ..services.export.query import EXIT_REFUSED, EXIT_UNAVAILABLE, ExportError

#: ``confirm(rel_path, unified_diff) -> bool``: the CLI asks on a terminal before --update.
Confirm = Callable[[str, str], bool]


def export_list(repo: Path) -> O.Outcome:
    """Every document kind with its target, mode and state."""
    try:
        cfg = Config.load(repo)
        rows = ops.listing(repo, cfg)
    except ExportError as exc:
        return O.Outcome("export.list", {}, exc.code, str(exc))
    except ValueError as exc:  # a config that does not load
        return O.Outcome("export.list", {}, EXIT_UNAVAILABLE, f"could not read the config: {exc}")
    return O.ok(
        "export.list",
        documents=rows,
        selected=ops.selection(cfg),
        redact=cfg.export.redact,
        redaction_applied=ops.REDACTION_APPLIED,
        max_bytes=cfg.export.max_bytes,
        refresh=cfg.export.refresh,
    )


def _filters(
    *, since: str, version: str, phase: str, status: str, limit: int, tag: str, session: str
) -> R.Filters:
    """The core Filters from the flags. ``--version X`` is the ``tag`` filter (the changelog
    kind reads a version from it: ``X`` or ``vX``, or ``unreleased``); asking for both a
    different ``--version`` and ``--tag`` is refused."""
    if version and tag and version != tag:
        raise ExportError("--version and --tag are the same filter; give one", EXIT_REFUSED)
    given = {
        "since": since,
        "phase": phase,
        "status": status,
        "limit": limit,
        "tag": tag or version,
        "session": session,
    }
    return R.Filters(**{k: v for k, v in given.items() if v not in ("", 0, None)})


def export(  # noqa: PLR0913 -- one keyword per CLI flag and MCP argument; the filters are the core vocabulary
    repo: Path,
    doc: str = "",
    *,
    all_docs: bool = False,
    since: str = "",
    version: str = "",
    phase: str = "",
    item: str = "",
    status: str = "",
    limit: int = 0,
    tag: str = "",
    session: str = "",
    max_bytes: int | None = None,
    template: str = "",
    diff: bool = False,
    check: bool = False,
    update: bool = False,
    out: str = "",
    force: bool = False,
    confirm: Confirm | None = None,
    base: Path | None = None,
    ceiling: int = 0,
) -> O.Outcome:
    """Print, diff, check or write one document or the selected set (see the module doc)."""
    try:
        if bool(doc) == all_docs:
            raise ExportError(
                "name one document (ddflow export <doc>) or pass --all for the selected set",
                EXIT_REFUSED,
            )
        # --out is also the TARGET of a --diff or --check (compare against that file), so it
        # only conflicts with the two comparisons being asked for at once, or with --update.
        if (diff and check) or ((diff or check) and update) or (update and out):
            raise ExportError(
                "--diff, --check and --update are alternatives (--out names the file for "
                "any of them)",
                EXIT_REFUSED,
            )
        if all_docs and (out or template):
            raise ExportError("--out and --template name one document, not --all", EXIT_REFUSED)
        if template and (update or (out and not (diff or check))):
            raise ExportError(
                "--template renders once for review (print, --diff, --check) and never writes; "
                "put the template in [export.<doc>].template to write with it",
                EXIT_REFUSED,
            )
        flt = _filters(
            since=since, version=version, phase=phase or item, status=status,
            limit=limit, tag=tag, session=session,
        )  # fmt: skip
        if all_docs and flt.given():
            raise ExportError(
                "filters apply to one document; set [export.<doc>].filters for --all",
                EXIT_REFUSED,
            )
        cfg = Config.load(repo)
        cap = cfg.export.max_bytes if max_bytes is None else max_bytes
        if ceiling:  # a surface's hard bound; 0 (no cap) is bounded too
            cap = ceiling if cap <= 0 else min(cap, ceiling)
        docs = [doc] if doc else ops.selection(cfg)
        if not docs:
            return O.Outcome(
                "export",
                {"results": [], "selected": []},
                O.NOTHING,
                "no documents are selected ([export].documents is empty), so --all has nothing "
                "to do. Print one with `ddflow export <doc>`; list the kinds with `ddflow export`.",
            )
        q = ops.load(repo, cfg)
        tmpl = ops.adhoc_template(doc, template, base or Path.cwd()) if template else None
        results = []
        for d in docs:
            try:
                spec = ops.spec_for(
                    cfg, d, filters=flt, writing=bool(diff or check or update or out)
                )
                if not (diff or check or update or out):
                    results.append(ops.print_doc(repo, cfg, q, spec, max_bytes=cap, template=tmpl))
                else:
                    results.append(_act(repo, cfg, q, spec, tmpl, diff, check, out, force, confirm))
            except ExportError as exc:
                results.append(
                    ops.Result(
                        d,
                        action="refused" if exc.code == EXIT_REFUSED else "failed",
                        code=exc.code,
                        message=str(exc),
                        text=getattr(exc, "diff", ""),
                    )
                )
    except ExportError as exc:
        return O.Outcome("export", {"results": []}, exc.code, str(exc))
    except ValueError as exc:  # a config that does not load
        return O.Outcome(
            "export", {"results": []}, EXIT_UNAVAILABLE, f"could not read the config: {exc}"
        )
    data: dict[str, Any] = {"results": [r.data() for r in results], "selected": ops.selection(cfg)}
    if not ops.REDACTION_APPLIED and any(
        r.action in ("created", "updated", "print") for r in results
    ):
        data["note"] = ops.REDACTION_NOTE
    reason = "; ".join(f"{r.doc}: {r.message}" for r in results if r.code and r.message)
    return O.Outcome("export", data, ops.worst([r.code for r in results]), reason)


def _act(
    repo: Path,
    cfg: Config,
    q: Any,
    spec: ops.Spec,
    tmpl: Any,
    diff: bool,
    check: bool,
    out: str,
    force: bool,
    confirm: Confirm | None,
) -> ops.Result:
    """--diff, --check, or a write (``--update`` when ``out`` is empty, else ``--out``)."""
    if diff or check:
        w = ops.write_doc(repo, cfg, q, spec, target=out, check=check, diff=diff, template=tmpl)
        return ops.Result(
            spec.doc, w.rel, spec.mode, w.action, w.code, w.diff, message=f"{w.rel}: {w.action}"
        )
    if confirm is not None:
        peek = ops.write_doc(repo, cfg, q, spec, target=out, diff=True, template=tmpl)
        if peek.diff and not confirm(peek.rel, peek.diff):
            return ops.Result(
                spec.doc, peek.rel, spec.mode, "declined", O.NOTHING,
                message=f"{peek.rel}: not written (declined)",
            )  # fmt: skip
    w = ops.write_doc(repo, cfg, q, spec, target=out, force=force, template=tmpl)
    return ops.Result(spec.doc, w.rel, spec.mode, w.action, w.code, message=f"{w.rel}: {w.action}")


#: The most an MCP print returns, whatever `max_bytes` asks for: a tool result is read into a
#: model's context in full. A cut document carries `truncated` and a footer.
MCP_CEILING = 60_000


def export_tool(repo: Path, a: dict[str, Any]) -> O.Outcome:
    """The `ddflow_export` tool: its arguments are the CLI's flags, plus ``write`` + ``path``.

    Nothing is written unless ``write`` is true AND ``path`` names a repo-relative file;
    ``path`` without ``write`` is refused (exit 3) unless it is the target of a ``diff`` or
    ``check``, so a path is never silently ignored. ``update``, ``out``, ``force`` and an ad
    hoc ``template`` do not exist here: an agent writes with ``write`` + ``path`` (the
    path-safety rules apply), never overrides hand-edit protection and never feeds the
    renderer an arbitrary file.
    """
    doc, every = str(a.get("doc") or ""), bool(a.get("all"))
    write, path = bool(a.get("write")), str(a.get("path") or "")
    diff, check = bool(a.get("diff")), bool(a.get("check"))
    if not doc and not every:
        if write or path or diff or check:  # an argument is never silently dropped
            return O.refused(
                "export", "write, path, diff and check need a doc (or all)", results=[]
            )
        return export_list(repo)
    if write and not path:
        return O.refused(
            "export", "write=true needs path: a repo-relative file to write", results=[]
        )
    if path and not (write or diff or check):
        return O.refused(
            "export",
            "path without write=true writes nothing; pass write=true to write it, or diff/check "
            "to compare it",
            results=[],
        )
    if write and (diff or check):
        return O.refused("export", "write, diff and check are alternatives", results=[])
    if every and path:
        return O.refused("export", "path names one document, not all", results=[])
    mb = a.get("max_bytes")
    return export(
        repo,
        doc,
        all_docs=every,
        since=str(a.get("since") or ""),
        version=str(a.get("version") or ""),
        phase=str(a.get("phase") or ""),
        item=str(a.get("item") or ""),
        status=str(a.get("status") or ""),
        limit=int(a.get("limit") or 0),
        tag=str(a.get("tag") or ""),
        session=str(a.get("session") or ""),
        max_bytes=None if mb is None else int(mb),
        diff=diff,
        check=check,
        update=write and not path,
        out=path,
        ceiling=MCP_CEILING,
    )
