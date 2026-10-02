"""What the surfaces do with the export core: select, render, compare, write.

`ddflow export` (CLI) and `ddflow_export` (MCP) are two views of the functions here, so
they cannot disagree (the api layer wraps these into Outcomes). Nothing in this module
prints or reads argv.

Selection (D-export-selection): ``[export].documents`` is EMPTY by default and names the
documents ``--all`` acts on. Printing or writing ONE document on demand works for any kind
whether or not it is selected.

Resolution of one document's settings (``spec_for``), first hit wins:
call-site argument -> ``[export.<doc>]`` table -> the kind's default. Filters merge the same
way, field by field. The size cap (``[export].max_bytes``) bounds what is printed or
returned; a file that is written is never capped.

SEAM for B-export-redact-fence: every body that leaves this module passes ``render`` below.
Redaction (``[export].redact``, already parsed into ``Spec.redact``) and the MCP fencing of
free text belong there, BEFORE the body is framed, so the header digest covers redacted
bytes. Neither is applied yet; ``REDACTION_APPLIED`` says so and the surfaces tell the user.

Refresh modes beyond ``off`` (B-export-refresh) are accepted by config and not acted on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from ...config import Config
from ..prompts import Template
from . import frame as F
from . import query as Q
from . import registry as R
from . import write as W
from .query import EXIT_REFUSED, EXIT_UNAVAILABLE, ExportError

#: Redaction is not wired in yet (B-export-redact-fence). Surfaces say so instead of
#: implying the documents are scrubbed.
REDACTION_APPLIED = False
REDACTION_NOTE = (
    "note: [export].redact is not applied yet (B-export-redact-fence): read the document for "
    "private addresses and hostnames before committing it"
)

STATES = ("not selected", "fresh", "stale", "hand-edited", "missing")

_TRUNC = re.compile(r"\[truncated: (\d+) more; use --since/--limit\]")


@dataclass(frozen=True)
class Spec:
    """One document's effective settings."""

    doc: str
    path: str
    mode: str
    template: str  # `[export.<doc>].template`, "" = resolve normally
    filters: R.Filters
    redact: bool
    refresh: str
    selected: bool


@dataclass
class Result:
    """What one document's run did."""

    doc: str
    path: str = ""
    mode: str = ""
    action: str = ""  # print | diff | check | created | updated | unchanged | fresh | stale | ...
    code: int = 0
    text: str = ""  # the framed document (print) or the unified diff (diff)
    truncated: bool = False
    truncated_more: int = 0
    total_bytes: int = 0
    message: str = ""
    redacted: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def data(self) -> dict[str, Any]:
        out = {
            "doc": self.doc,
            "path": self.path,
            "mode": self.mode,
            "action": self.action,
            "code": self.code,
            "message": self.message,
            "text": self.text,
            "truncated": self.truncated,
            "bytes": len(self.text.encode("utf-8")),
        }
        if self.truncated:
            out["truncated_more"] = self.truncated_more
        return out


# -- settings -------------------------------------------------------------------------


def filters_of(table: dict[str, Any], given: R.Filters | None = None) -> R.Filters:
    """The ``filters`` of an ``[export.<doc>]`` table, overridden field by field by
    ``given`` (a call-site filter wins when it is set)."""
    base: dict[str, Any] = {}
    names = {f.name: f for f in fields(R.Filters)}
    for k, v in (table.get("filters") or {}).items():
        if k not in names:
            raise ExportError(
                f"[export.*].filters has {k!r}, which is not a filter. Known: {', '.join(sorted(names))}",
                EXIT_REFUSED,
            )
        try:
            base[k] = int(v) if k == "limit" else str(v)
        except (TypeError, ValueError):
            raise ExportError(
                f"[export.*].filters.{k} = {v!r} is not valid", EXIT_REFUSED
            ) from None
    if given is not None:
        base.update({k: getattr(given, k) for k in given.given()})
    return R.Filters(**base)


def spec_for(
    cfg: Config, doc: str, *, path: str = "", mode: str = "", filters: R.Filters | None = None
) -> Spec:
    """Settings for ``doc`` (refused, exit 3, for an unknown kind: the message lists them)."""
    kind = R.get(doc)
    t = cfg.export.table(doc)
    m = mode or str(t.get("mode") or kind.update_mode)
    if m not in R.UPDATE_MODES:
        raise ExportError(f"mode {m!r} is not one of {', '.join(R.UPDATE_MODES)}", EXIT_REFUSED)
    if m == R.APPEND and kind.update_mode != R.APPEND and appender(doc) is None:
        raise ExportError(
            f"document {doc!r} renders whole documents and cannot be appended to: use mode "
            f"whole or region",
            EXIT_REFUSED,
        )
    return Spec(
        doc=doc,
        path=path or str(t.get("path") or kind.default_target),
        mode=m,
        template=str(t.get("template") or ""),
        filters=filters_of(t, filters),
        redact=bool(t.get("redact", cfg.export.redact)),
        refresh=str(t.get("refresh") or cfg.export.refresh),
        selected=doc in cfg.export.documents,
    )


def selection(cfg: Config) -> list[str]:
    """The selected document names, in selection order, each once."""
    seen: list[str] = []
    for d in cfg.export.documents:
        if d not in seen:
            seen.append(d)
    return seen


def adhoc_template(doc: str, path: str, base: Path) -> Template:
    """A template read from ``path`` (``--template``), named by its file for error lines."""
    p = Path(path)
    if not p.is_absolute():
        p = base / p
    try:
        text = p.read_text("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ExportError(f"could not read template {p}: {exc}", EXIT_UNAVAILABLE) from exc
    return Template(doc, text, "adhoc", p)


# -- rendering ------------------------------------------------------------------------


def _overrides(cfg: Config) -> dict[str, str]:
    return {d: str(t["template"]) for d, t in cfg.export.tables.items() if t.get("template")}


def load(repo: Path, cfg: Config) -> Q.Query:
    return Q.load(repo, cfg.log)


def render(
    repo: Path,
    cfg: Config,
    q: Q.Query,
    spec: Spec,
    *,
    max_bytes: int = 0,
    template: Template | str | None = None,
) -> str:
    """The framed document for ``spec`` over ``q`` (SEAM: redaction and fencing go here)."""
    return R.render_document(
        spec.doc,
        q,
        spec.filters,
        repo=repo,
        overrides=_overrides(cfg),
        max_bytes=max_bytes,
        template=template,
    )


def _body(
    repo: Path,
    cfg: Config,
    q: Q.Query,
    spec: Spec,
    template: Template | str | None = None,
) -> str:
    return R.render_body(
        spec.doc, q, spec.filters, repo=repo, overrides=_overrides(cfg), template=template
    )


def print_doc(
    repo: Path,
    cfg: Config,
    q: Q.Query,
    spec: Spec,
    *,
    max_bytes: int | None = None,
    template: Template | str | None = None,
) -> Result:
    """The document as it would be shown on stdout or returned over MCP, capped."""
    cap = cfg.export.max_bytes if max_bytes is None else max_bytes
    text = render(repo, cfg, q, spec, max_bytes=cap, template=template)
    m = _TRUNC.fullmatch(text.rstrip("\n").rsplit("\n", 1)[-1])
    return Result(
        spec.doc,
        spec.path,
        spec.mode,
        "print",
        text=text,
        truncated=bool(m),
        truncated_more=int(m.group(1)) if m else 0,
        total_bytes=len(text.encode("utf-8")),
        redacted=REDACTION_APPLIED,
    )


# -- writing --------------------------------------------------------------------------


def appender(doc: str):
    """The append-mode producer factory of kind ``doc``, or None.

    A kind that can grow a log defines ``append_unreleased(query)`` in its module; it returns
    ``produce(last) -> (entries_text, new_last_event_id)`` for ``write.append_entries``
    (``kind_changelog`` is the one today). A kind without one cannot be appended to.
    """
    import sys

    R.get(doc)  # imports every kind module; an unknown kind is refused here
    mod = sys.modules.get(f"{__package__}.kind_{doc}")
    return getattr(mod, "append_unreleased", None)


def write_doc(
    repo: Path,
    cfg: Config,
    q: Q.Query,
    spec: Spec,
    *,
    target: str = "",
    check: bool = False,
    diff: bool = False,
    force: bool = False,
    template: Template | str | None = None,
) -> W.WriteResult:
    """Write (or ``check`` / ``diff``) ``spec`` to ``target`` or its own path, in its mode."""
    path = target or spec.path
    if spec.mode == R.WHOLE:
        doc = render(repo, cfg, q, spec, max_bytes=0, template=template)
        return W.write_whole(repo, path, doc, check=check, diff=diff, force=force)
    if spec.mode == R.REGION:
        body = _body(repo, cfg, q, spec, template)
        return W.write_region(repo, path, spec.doc, body, check=check, diff=diff, force=force)
    if template is not None or spec.template:
        raise ExportError(
            "append mode writes the entries the kind produces itself; a template applies to "
            "mode whole or region",
            EXIT_REFUSED,
        )
    make = appender(spec.doc)
    if make is None:  # update_mode APPEND declared but no producer: a kind bug, not a user error
        raise ExportError(f"document {spec.doc!r} has no append producer", EXIT_UNAVAILABLE)
    return W.append_entries(
        repo,
        path,
        spec.doc,
        make(q),
        check=check,
        diff=diff,
        force=force,
    )


def state_of(repo: Path, cfg: Config, q: Q.Query, spec: Spec) -> tuple[str, str]:
    """``(state, detail)`` of ``spec``'s target: fresh | stale | hand-edited | missing.

    ``hand-edited`` also covers a file that is not ddflow's at all (no header, no region):
    in both cases ddflow will not overwrite it without ``--force``.
    """
    try:
        path, _rel = W.safe_target(repo, spec.path)
    except ExportError as exc:
        return "stale", f"unsafe target: {exc}"
    if not path.exists():
        return "missing", ""
    try:
        old = W._read(path)
    except ExportError as exc:  # unreadable or not UTF-8: not a file ddflow wrote
        return "hand-edited", f"not readable as text ({exc})"
    if old is None:  # removed between the exists() above and this read
        return "missing", ""
    if spec.mode == R.REGION:
        sha = _region_edit(old, spec.doc)
        if sha == "none":
            return "hand-edited", "no ddflow region for this document in the file"
        if sha == "edited":
            return "hand-edited", "the region was edited by hand"
        if sha == "broken":
            return "hand-edited", "duplicate or unbalanced markers"
    else:
        head, _ = F.split(old)
        if head is None:
            return "hand-edited", "not a ddflow file (no header)"
        if F.hand_edited(old):
            return "hand-edited", "the body no longer matches its digest"
        if head.doc != spec.doc:
            return "hand-edited", f"generated as {head.doc!r}"
    res = write_doc(repo, cfg, q, spec, check=True)
    return ("fresh", "") if res.code == 0 else ("stale", "regenerating would change it")


def _region_edit(text: str, doc: str) -> str:
    """ok | none (no markers) | edited (digest mismatch) | broken (duplicate/unbalanced)."""
    begins = list(W._region_pattern(doc, "begin").finditer(text))
    ends = list(W._region_pattern(doc, "end").finditer(text))
    if not begins and not ends:
        return "none"
    if len(begins) != 1 or len(ends) != 1 or begins[0].end() > ends[0].start():
        return "broken"
    b, e = begins[0], ends[0]
    current = text[b.end() + 1 : e.start()]
    return "ok" if F.body_digest(current) == b.group("sha") else "edited"


def listing(repo: Path, cfg: Config) -> list[dict[str, Any]]:
    """One row per kind: target, mode, state (``not selected`` for unselected kinds)."""
    names = R.names()
    rows: list[dict[str, Any]] = []
    q: Q.Query | None = None
    for doc in [*names, *[d for d in selection(cfg) if d not in names]]:
        if doc not in names:  # selected in config but no such kind (a typo or newer release)
            rows.append(
                {
                    "doc": doc,
                    "title": "",
                    "target": "",
                    "mode": "",
                    "state": "unknown kind",
                    "detail": f"no such document kind; known: {', '.join(names)}",
                    "selected": True,
                    "filters": [],
                }
            )
            continue
        kind = R.get(doc)
        row: dict[str, Any] = {
            "doc": doc,
            "title": kind.title,
            "filters": sorted(kind.filters),
        }
        try:
            spec = spec_for(cfg, doc)
        except ExportError as exc:
            rows.append(
                {
                    **row,
                    "target": "",
                    "mode": "",
                    "state": "stale",
                    "detail": str(exc),
                    "selected": doc in cfg.export.documents,
                }
            )
            continue
        row.update(target=spec.path, mode=spec.mode, selected=spec.selected)
        if not spec.selected:
            row.update(state="not selected", detail="")
        else:
            q = q or load(repo, cfg)  # an unreadable log is "could not run" (exit 2), not a state
            try:
                state, detail = state_of(repo, cfg, q, spec)
            except ExportError as exc:
                state, detail = "stale", f"could not compare: {exc}"
            row.update(state=state, detail=detail)
        rows.append(row)
    return rows


def worst(codes: list[int]) -> int:
    """One exit code for several documents: refused (3) over could-not-run (2) over
    failed/stale (1) over ok (0)."""
    for c in (EXIT_REFUSED, EXIT_UNAVAILABLE, 1):
        if c in codes:
            return c
    return 0
