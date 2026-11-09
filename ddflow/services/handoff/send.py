"""The `ddflow suggest` half that resolves an input to a patch + base and proposes it.

The heavier plumbing lives next door: `events.propose` bounds/scans/caps the patch, `refs.cut`
re-cuts it onto the owner tip with plumbing, and `core.handlers.suggestions` folds the event
into an inbox pointer. This module turns ``--commit``/``--diff``/``--blob`` into a patch and a
base, then calls `propose`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...config import Config
from . import events
from .events import SuggestionRefused

__all__ = ["SuggestError", "Suggested", "resolve_patch", "suggest"]


class SuggestError(ValueError):
    """A suggest input that must be fixed before resending."""


@dataclass(frozen=True)
class Suggested:
    id: str
    ref: str
    probe: str
    blob: str


def resolve_patch(
    repo: Path | str,
    *,
    commit: str = "",
    diff: str = "",
    blob: str = "",
) -> str:
    """The patch from exactly one of ``--commit``/``--diff``/``--blob``.

    ``commit`` is a revision whose change (vs its parent) is the patch; ``diff`` is a path to a
    `git diff` body; ``blob`` is a path to a raw patch text. Git is read for the commit form
    only; the two file forms are plain reads."""
    given = [k for k, v in (("commit", commit), ("diff", diff), ("blob", blob)) if v]
    if len(given) != 1:
        raise SuggestError(
            f"suggest takes exactly one of --commit/--diff/--blob, got {given or ['none']}"
        )
    if commit:
        from ...adapters import git

        show = git.run(repo, "show", "--format=", "--binary", commit)
        if show.unavailable or not show.ok:
            raise SuggestError(f"git could not show {commit}: {show.err}")
        if not show.out.lstrip().startswith("diff --git"):
            raise SuggestError(
                f"{commit}: git show gives no replayable patch (an empty or merge commit); "
                "name the change's diff with --diff FILE, or the parent with --commit "
                "COMMIT^"
            )
        return show.out
    src = Path(diff or blob)
    try:
        text = src.read_text("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SuggestError(f"could not read {src}: {exc}") from exc
    if not text.lstrip().startswith("diff --git"):
        raise SuggestError(
            f"{src}: a suggestion is a git diff patch (starts with 'diff --git'); "
            f"regenerate with `git diff` (CLI), or `ddflow_diff`"
        )
    return text


def suggest(  # noqa: PLR0913 -- one keyword per facet of a suggestion; all keyword-only
    log: Any,
    cfg: Config,
    *,
    to_item: str,
    reason: str,
    kind: str = "",
    commit: str = "",
    diff: str = "",
    blob: str = "",
    to_agent: str = "",
    from_item: str = "",
    base: str = "",
    evidence: str = "",
) -> Suggested:
    """Send a change to the owner of ``to_item``: resolve the input and hand it to `propose`.

    ``to_agent`` is the lease holder that must answer; ``base`` is the owner's tip the patch
    is re-cut onto (the API resolves it from the owner's branch); ``evidence`` is the
    ``CMD,EXIT`` a ``gate_fix`` re-run must reproduce. With no base no ref is cut, but the
    blob is still recorded and the patch-id taken."""
    if not reason.strip():
        raise SuggestError("a suggestion needs --reason: why the owner should take it")
    patch = resolve_patch(log.root, commit=commit, diff=diff, blob=blob)
    try:
        out = events.propose(
            log,
            cfg,
            from_item=from_item,
            to_item=to_item,
            to_agent=to_agent,
            patch=patch,
            kind=kind,
            reason=reason,
            base=base,
            evidence=evidence,
            repo=log.root,
        )
    except SuggestionRefused as exc:
        raise SuggestError(str(exc)) from exc
    return Suggested(id=out.id, ref=out.ref, probe=out.probe, blob=out.blob)
