"""Automatic refresh of the selected documents (decisions D-export, D-export-selection).

``[export].refresh`` (and ``[export.<doc>].refresh``, which wins for that document) says WHEN
a selected document regenerates itself: ``off`` (the default: nothing here ever writes),
``merge`` (``ddflow merge``, into the item's branch so the documents land in the merge),
``phase_close`` (a phase completing) or ``docs_gate`` (the phase ``docs`` gate's export step,
which also verifies and returns the documents with their body digests as evidence).

``refresh_selected(repo, trigger)`` is the one entry point; callers decide when to call it.
Rules it keeps, whatever the trigger:

* only SELECTED documents whose effective refresh equals the trigger, in selection order;
* only whole-file documents (a region or append document is reported and skipped);
* a hand-edited, unmarked or otherwise foreign target is never touched: reported and skipped;
* a failure for one document (unreadable log, template error, unsafe path) is that document's
  outcome, never an exception: a refresh must not fail the operation that triggered it;
* nothing selected for the trigger is a no-op that says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...config import EXPORT_REFRESH_MODES, Config
from . import frame as F
from . import ops
from . import registry as R
from .query import ExportError

# Outcomes. Only these two mean the working tree changed.
WROTE = ("created", "updated")


@dataclass
class DocOutcome:
    doc: str
    path: str = ""
    action: str = ""  # created | updated | unchanged | skipped | failed
    message: str = ""
    digest: str = ""  # the body digest of the file as it stands after the refresh
    verified: bool | None = None  # docs_gate only: `export --check` is fresh

    def data(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "doc": self.doc,
            "path": self.path,
            "action": self.action,
            "message": self.message,
            "digest": self.digest,
        }
        if self.verified is not None:
            d["verified"] = self.verified
        return d


@dataclass
class Refresh:
    trigger: str
    outcomes: list[DocOutcome] = field(default_factory=list)
    note: str = ""

    @property
    def changed(self) -> list[str]:
        """Repo-relative paths this refresh wrote."""
        return [o.path for o in self.outcomes if o.action in WROTE]

    @property
    def problems(self) -> list[DocOutcome]:
        return [o for o in self.outcomes if o.action in ("skipped", "failed")]

    def summary(self) -> str:
        """One line per outcome that is worth a human's attention, or the no-op note."""
        if not self.outcomes:
            return self.note
        parts = []
        for o in self.outcomes:
            parts.append(f"{o.doc}: {o.action}" + (f" ({o.message})" if o.message else ""))
        return f"export refresh ({self.trigger}): " + "; ".join(parts)

    def evidence(self) -> dict[str, Any]:
        """The gate evidence of a ``docs_gate`` refresh: documents and body digests."""
        return {
            "trigger": self.trigger,
            "documents": [o.data() for o in self.outcomes],
            "digests": {o.doc: o.digest for o in self.outcomes if o.digest},
            "note": self.note,
        }

    def data(self) -> dict[str, Any]:
        return {**self.evidence(), "changed": self.changed}


def _digest_of(path: Path) -> str:
    try:
        head, _ = F.split(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return head.digest if head else ""


def refresh_selected(
    repo: Path,
    trigger: str,
    *,
    root: Path | None = None,
    cfg: Config | None = None,
    verify: bool | None = None,
) -> Refresh:
    """Regenerate the selected documents whose refresh mode is ``trigger``.

    The event log and config are read from ``repo``; the documents are written under ``root``
    (default ``repo``: ``merge`` passes the item's worktree so the files go into its branch).
    ``verify`` (default: true for ``docs_gate``) re-checks each written document against the
    log, as ``export --check`` does, and records the result per document.
    """
    out = Refresh(trigger)
    if trigger not in EXPORT_REFRESH_MODES or trigger == "off":
        out.note = f"export refresh: {trigger!r} is not a refresh trigger"
        return out
    verify = trigger == "docs_gate" if verify is None else verify
    try:
        cfg = cfg or Config.load(repo)
        selected = ops.selection(cfg)
        specs: list[ops.Spec] = []
        for d in selected:
            spec = ops.spec_for(cfg, d, writing=True)
            if spec.refresh == trigger:
                specs.append(spec)
    except (ExportError, ValueError) as exc:
        out.outcomes.append(
            DocOutcome("", action="failed", message=f"could not read settings: {exc}")
        )
        return out
    if not specs:
        out.note = (
            f"export refresh ({trigger}): no selected document has refresh = {trigger!r}; nothing to do"
            if selected
            else f"export refresh ({trigger}): no documents are selected ([export].documents is empty); nothing to do"
        )
        return out
    target_root = root or repo
    try:
        q = ops.load(repo, cfg)
    except (ExportError, ValueError, OSError) as exc:
        for s in specs:
            out.outcomes.append(
                DocOutcome(s.doc, s.path, "failed", f"could not read the event log: {exc}")
            )
        return out
    for spec in specs:
        out.outcomes.append(_one(target_root, cfg, q, spec, verify))
    return out


def _one(root: Path, cfg: Config, q: Any, spec: ops.Spec, verify: bool) -> DocOutcome:
    o = DocOutcome(spec.doc, spec.path)
    try:
        if spec.mode != R.WHOLE:
            o.action, o.message = "skipped", f"mode {spec.mode} is not refreshed automatically"
            return o
        state, detail = ops.state_of(root, cfg, q, spec)
        if state == "hand-edited":
            o.action, o.message = "skipped", f"hand-edited, left alone ({detail})"
            return o
        if state == "fresh":
            o.action = "unchanged"
        else:
            w = ops.write_doc(root, cfg, q, spec)
            o.action, o.path = w.action, w.rel
        o.digest = _digest_of(root / o.path)
        if verify:
            o.verified = ops.write_doc(root, cfg, q, spec, check=True).code == 0
            if not o.verified:
                o.message = o.message or "not fresh after the refresh"
    except ExportError as exc:
        o.action, o.message = "failed", str(exc)
    except Exception as exc:
        o.action, o.message = "failed", f"{type(exc).__name__}: {exc}"
    return o
