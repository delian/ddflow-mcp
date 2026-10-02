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

Safety (``safe.py``, D-export 5): every body that leaves this module passes ``render``, which
redacts the RENDERED body before it is truncated, framed or digested (``[export].redact``,
default on; ``Spec.redact``), so the header digest and ``--check`` cover redacted bytes. A
printed document served over MCP is also fenced as agent-written data (``fenced=True``).

Refresh (``refresh.py``, B-export-refresh): ``[export].refresh`` = off | merge | phase_close |
docs_gate decides when a selected whole-file document regenerates itself; the triggers call
``refresh.py``, which goes through the same ``write_doc`` and its hand-edit protection.
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
from . import safe as S
from . import write as W
from .query import EXIT_REFUSED, EXIT_UNAVAILABLE, ExportError

#: Redaction is applied by ``render`` (``safe.py``); the surfaces report it as on.
REDACTION_APPLIED = True

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
            "redacted": self.redacted,
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
    cfg: Config,
    doc: str,
    *,
    path: str = "",
    mode: str = "",
    filters: R.Filters | None = None,
    writing: bool = False,
) -> Spec:
    """Settings for ``doc`` (refused, exit 3, for an unknown kind: the message lists them).

    ``writing`` is true for anything that compares with or writes the target (``--diff``,
    ``--check``, ``--update``, ``--out``, the listing): an append-mode document then takes no
    template and no filters. Printing never appends, so it is not refused for them."""
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
    flt = filters_of(t, filters)
    if writing and m == R.APPEND and (t.get("template") or flt.given()):
        raise ExportError(
            "append mode writes the entries the kind produces itself: it takes no template "
            "and no filters",
            EXIT_REFUSED,
        )
    return Spec(
        doc=doc,
        path=path or str(t.get("path") or kind.default_target),
        mode=m,
        template=str(t.get("template") or ""),
        filters=flt,
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
    """The framed document for ``spec`` over ``q``, redacted when ``spec.redact``."""
    return R.render_document(
        spec.doc,
        q,
        spec.filters,
        repo=repo,
        overrides=_overrides(cfg),
        max_bytes=max_bytes,
        template=template,
        post=_post(cfg, repo, spec),
    )


def _post(cfg: Config, repo: Path, spec: Spec):
    """The body hook for ``render_document``: redact, and say so in the header."""

    def run(body: str) -> tuple[str, dict[str, str]]:
        if not spec.redact:
            return body, S.redaction_attrs(None)
        red = S.redact_text(body, cfg)
        return red.text, S.redaction_attrs(red.counts)

    return run


def _redacted(cfg: Config, repo: Path, spec: Spec, text: str) -> str:
    """A body that is not framed by ``render`` (region, append entries), redacted."""
    return _post(cfg, repo, spec)(text)[0]


def _body(
    repo: Path,
    cfg: Config,
    q: Q.Query,
    spec: Spec,
    template: Template | str | None = None,
) -> str:
    return _redacted(
        cfg,
        repo,
        spec,
        R.render_body(
            spec.doc, q, spec.filters, repo=repo, overrides=_overrides(cfg), template=template
        ),
    )


def print_doc(
    repo: Path,
    cfg: Config,
    q: Q.Query,
    spec: Spec,
    *,
    max_bytes: int | None = None,
    template: Template | str | None = None,
    fenced: bool = False,
) -> Result:
    """The document as it would be shown on stdout or returned over MCP, capped.

    ``fenced`` (the MCP path) wraps it in a provenance fence naming its authors; the fence
    is inside the cap."""
    cap = cfg.export.max_bytes if max_bytes is None else max_bytes
    by = S.authors(q.events) if fenced else ""
    room = cap
    if fenced and cap > 0:
        room = max(cap - S.fence_overhead(spec.doc, by), 1)
    shown = render(repo, cfg, q, spec, max_bytes=room, template=template)
    text = S.fence_document(spec.doc, shown, by) if fenced else shown
    # The fence escapes tag-like text, which grows it past what the overhead measured on an
    # empty body: tighten the room by the excess until the fenced text fits the cap.
    for _ in range(64):
        excess = len(text.encode("utf-8")) - cap if fenced and cap > 0 else 0
        if excess <= 0:
            break
        room = max(room - excess, 1)
        shown = render(repo, cfg, q, spec, max_bytes=room, template=template)
        text = S.fence_document(spec.doc, shown, by)
    else:
        raise ExportError(f"--max-bytes {cap} is too small for the fenced document", EXIT_REFUSED)
    m = _TRUNC.fullmatch(shown.rstrip("\n").rsplit("\n", 1)[-1])
    return Result(
        spec.doc,
        spec.path,
        spec.mode,
        "print",
        text=text,
        truncated=bool(m),
        truncated_more=int(m.group(1)) if m else 0,
        total_bytes=len(text.encode("utf-8")),
        redacted=spec.redact,
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
    last = W.last_exported(repo, path)
    if last and last not in {e.id or e.compute_id() for e in q.events}:
        raise ExportError(
            f"the last exported event {last} (in {path}) is not in the log any more; "
            "refusing to append, which would repeat entries already written",
            EXIT_REFUSED,
        )
    make = appender(spec.doc)
    if make is None:  # update_mode APPEND declared but no producer: a kind bug, not a user error
        raise ExportError(f"document {spec.doc!r} has no append producer", EXIT_UNAVAILABLE)
    return W.append_entries(
        repo,
        path,
        spec.doc,
        _redacting(make(q), cfg, repo, spec),
        check=check,
        diff=diff,
        force=force,
    )


def _redacting(produce, cfg: Config, repo: Path, spec: Spec):
    """An append producer whose new entries are redacted before they are written."""

    def run(last):
        entries, new_last = produce(last)
        return _redacted(cfg, repo, spec, entries), new_last

    return run


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
            spec = spec_for(cfg, doc, writing=True)
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
