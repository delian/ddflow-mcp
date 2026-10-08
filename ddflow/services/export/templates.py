"""Export templates the user owns: eject a copy, validate the selection, notice drift
(decision D-export-templates).

``eject`` copies the SHIPPED template of a kind into ``.ddflow/templates/export/`` (where
``registry.resolve_template`` looks second) behind a one-line Jinja comment recording the
digest of the shipped text it came from::

    {# ddflow-shipped: 3f2a9c1b04de -#}

The comment renders to nothing (``-#}`` also eats the newline). With it, ddflow can tell an
UNEDITED copy (its body still hashes to the recorded digest: refreshed freely) from an EDITED
one (never overwritten without ``--force``), and a copy older than the shipped default
(recorded digest differs from today's) from a current one. A ddflow upgrade never rewrites
the copy; ``validate`` and ``doctor`` only say it has drifted.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...config import Config
from ...infra.fsio import repo_rel
from ...infra.paths import templates_dir
from ..overlay import OverlayError, OverlayLoader
from . import registry as R
from .query import EXIT_REFUSED, EXIT_UNAVAILABLE, ExportError


def _loader() -> OverlayLoader:
    """The overlay of the shipped export templates (D-unify, B-uni-overlay.3):
    ``.ddflow/templates/export/<kind>.md.j2`` over the package default."""
    return OverlayLoader(
        "template",
        shipped_dir=templates_dir() / "export",
        project_subdir="templates/export",
        suffix=".md.j2",
        display=rel,
        force_flag="--force",
    )


def project_path(repo: Path, kind: str) -> Path:
    return _loader().project_path(repo, kind)


def rel(repo: Path, p: Path) -> str:
    return repo_rel(repo, p, as_given=True, strict=False) or str(p)


def split_mark(text: str) -> tuple[str, str]:
    """``(recorded shipped digest or "", the template text without the marker)``."""
    return _loader().split_mark(text)


def ejected_text(kind: str) -> str:
    """What eject writes for ``kind`` today."""
    return _wrapped(lambda: _loader().ejected_text(kind))


def is_edited(text: str, kind: str) -> bool:
    """True when ``text`` (an existing file) is neither a pristine copy of some shipped
    version (recorded digest = digest of its own body) nor the current shipped text."""
    return _wrapped(lambda: _loader().is_edited(text, kind))


def _wrapped(fn: Callable[[], Any]) -> Any:
    """``fn()`` with the loader's refusal as exit 3 and its other failures as exit 2."""
    try:
        return fn()
    except OverlayError as exc:
        raise ExportError(str(exc), EXIT_REFUSED if exc.refused else EXIT_UNAVAILABLE) from exc


@dataclass
class Ejected:
    doc: str
    path: str
    action: str  # created | updated | overwritten | unchanged
    message: str = ""
    notes: list[str] = field(default_factory=list)


def eject(repo: Path, doc: str, *, force: bool = False, cfg: Config | None = None) -> Ejected:
    """Copy the shipped template of ``doc`` into the project. Idempotent; an edited copy is
    refused (exit 3) without ``force``; an unedited older copy is refreshed."""
    R.get(doc)
    repo = Path(repo)
    done = _wrapped(lambda: _loader().eject(repo, doc, force=force))
    shown = rel(repo, done.path)
    action = done.action
    msg = {
        "created": f"ejected {doc} -> {shown}",
        "updated": f"{shown}: refreshed from the shipped default (it had no edits)",
        "overwritten": f"{shown}: replaced by the shipped default (--force)",
        "unchanged": f"{shown}: already the shipped default",
    }[action]
    notes = []
    override = (cfg.export.table(doc).get("template") if cfg else "") or ""
    if override:
        notes.append(
            f"[export.{doc}].template = {override!r} takes precedence over this copy; "
            "remove it to use the copy"
        )
    return Ejected(doc, shown, action, msg, notes)


# -- drift -----------------------------------------------------------------------------


def drift_notes(repo: Path) -> list[str]:
    """One note per ejected copy older than the shipped default (never raises)."""
    out = []
    try:
        kinds = R.names()
    except Exception:
        return out
    for kind in kinds:
        d = _loader().drift(repo, kind)
        if d is None:
            continue
        out.append(
            f"export template {rel(repo, d.path)} was ejected from an older shipped "
            f"{kind} template; "
            + (
                "it has your edits: compare it with the shipped default "
                "(`ddflow export eject " + kind + " --force` writes it to replace)"
                if d.edited
                else f"it has no edits: `ddflow export eject {kind}` refreshes it"
            )
        )
    return out


# -- validate --------------------------------------------------------------------------


def validate(repo: Path, cfg: Config, docs: list[str]) -> dict[str, Any]:
    """Render each of ``docs`` against the current data and report every template error.

    Nothing is written. ``results`` has one row per document: ``ok``, the template used
    (``template``, ``source``) and, for a failure, ``message`` (file:line when known) and
    ``code``. ``notes`` lists ejected copies older than the shipped default."""
    from . import ops

    q = ops.load(repo, cfg)  # an unreadable log is "could not run" (exit 2), not a pass
    results: list[dict[str, Any]] = []
    for doc in docs:
        row: dict[str, Any] = {"doc": doc, "ok": True, "template": "", "source": ""}
        try:
            spec = ops.spec_for(cfg, doc)
            tmpl = R.resolve_template(doc, repo, ops._overrides(cfg))
            row["template"] = (
                f"shipped {doc}.md.j2"
                if tmpl.source == "builtin"
                else rel(Path(repo), tmpl.path)
                if tmpl.path
                else doc
            )
            row["source"] = tmpl.source
            ops.render(repo, cfg, q, spec, max_bytes=0)
        except ExportError as exc:
            row.update(ok=False, code=exc.code, message=str(exc))
        except Exception as exc:  # a kind's data function failing is "could not run" too
            row.update(
                ok=False, code=EXIT_UNAVAILABLE, message=f"{doc}: {type(exc).__name__}: {exc}"
            )
        results.append(row)
    return {"results": results, "notes": drift_notes(repo)}
