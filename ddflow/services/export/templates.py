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

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...config import Config
from ...infra import tomlcfg
from . import registry as R
from .query import EXIT_REFUSED, EXIT_UNAVAILABLE, ExportError

_MARK = re.compile(r"\A\{#\s*ddflow-shipped:\s*([0-9a-f]{12})\s*-#\}\n?")


def project_path(repo: Path, kind: str) -> Path:
    return Path(repo) / ".ddflow" / "templates" / "export" / f"{kind}.md.j2"


def rel(repo: Path, p: Path) -> str:
    try:
        return p.relative_to(repo).as_posix()
    except ValueError:
        return str(p)


def split_mark(text: str) -> tuple[str, str]:
    """``(recorded shipped digest or "", the template text without the marker)``."""
    m = _MARK.match(text)
    return (m.group(1), text[m.end() :]) if m else ("", text)


def ejected_text(kind: str) -> str:
    """What eject writes for ``kind`` today."""
    shipped = R.shipped_template(kind).text
    return f"{{# ddflow-shipped: {R.template_digest(shipped)} -#}}\n{shipped}"


def _read(p: Path) -> str | None:
    try:
        return p.read_text("utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise ExportError(f"could not read {p}: {exc}", EXIT_UNAVAILABLE) from exc


def is_edited(text: str, kind: str) -> bool:
    """True when ``text`` (an existing file) is neither a pristine copy of some shipped
    version (recorded digest = digest of its own body) nor the current shipped text."""
    recorded, body = split_mark(text)
    if recorded:
        return R.template_digest(body) != recorded
    return body != R.shipped_template(kind).text


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
    dst = project_path(repo, doc)
    base = repo / ".ddflow"
    for p in (base, base / "templates", base / "templates" / "export", dst):
        if p.is_symlink():
            raise ExportError(
                f"{rel(repo, p)} is a symlink; ddflow does not write through it", EXIT_REFUSED
            )
    new = ejected_text(doc)
    old = _read(dst)
    shown = rel(repo, dst)
    action = "created"
    if old is not None:
        if old == new:
            action = "unchanged"
        elif not is_edited(old, doc):
            action = "updated"
        elif force:
            action = "overwritten"
        else:
            raise ExportError(
                f"{shown} was edited since it was ejected; refusing to overwrite it "
                "(--force replaces it with the shipped default, losing your edits)",
                EXIT_REFUSED,
            )
    if action != "unchanged":
        dst.parent.mkdir(parents=True, exist_ok=True)
        tomlcfg.atomic_write(dst, new)
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
        p = project_path(repo, kind)
        try:
            text = _read(p)
            if text is None:
                continue
            recorded, body = split_mark(text)
            if recorded and recorded != R.shipped_digest(kind):
                edited = R.template_digest(body) != recorded
                out.append(
                    f"export template {rel(repo, p)} was ejected from an older shipped "
                    f"{kind} template; "
                    + (
                        "it has your edits: compare it with the shipped default "
                        "(`ddflow export eject " + kind + " --force` writes it to replace)"
                        if edited
                        else f"it has no edits: `ddflow export eject {kind}` refreshes it"
                    )
                )
        except ExportError:
            continue
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
