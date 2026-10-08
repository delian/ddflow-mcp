"""Seed migration: rewrite the deprecated names ddflow itself wrote into a project's files.

A rename keeps the old command or tool working through an alias (D-compat), but a settings
hook, a git hook, a driver doc or an instruction block that ddflow wrote keeps NAMING the old
one. `services.compat_refs` finds those and says `ddflow upgrade --apply` rewrites them; this
registers that rewrite, so it is detected, planned, backed up, applied and verified like any
other migration. Only references inside a region ddflow wrote and nobody edited are rewritten;
the project's own text gets a proposal in `ddflow doctor` and is never touched.

The command table and the tool table belong to the surfaces, and a service must not import
them: the surface that runs the upgrade hands in a source with `provide`. A process that
provided none (nothing loaded to judge names by) finds nothing here, as `doctor` reports
nothing then.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..compat_refs import DEPRECATED, Vocabulary, resolve, rewrite, scan
from ..compat_refs import Finding as Ref
from . import register
from .base import Change, Context, Corrective, Finding, Migration

#: What `provide` set: returns the vocabulary to judge names by, or None.
_source: Callable[[], Vocabulary | None] | None = None


def provide(source: Callable[[], Vocabulary | None] | None) -> None:
    """Register (or clear, with None) where the vocabulary comes from."""
    global _source  # noqa: PLW0603
    _source = source


def _vocabulary() -> Vocabulary | None:
    return _source() if _source is not None else None


def _rewritable(ctx: Context) -> tuple[Vocabulary | None, list[tuple[str, Ref]]]:
    """``(vocabulary, [(key, reference)])`` for the deprecated names in ddflow's own regions.
    The key is the path, the name and which occurrence of it in that file: stable when lines
    above it come and go, so a later scan finds the same reference under the same key."""
    vocab = _vocabulary()
    if vocab is None:
        return None, []
    seen: dict[tuple[str, str], int] = {}
    out: list[tuple[str, Ref]] = []
    for f in scan(ctx.repo, vocab):
        if not (f.managed and f.ref.status == DEPRECATED and f.ref.replacement):
            continue
        n = seen[f.path, f.ref.text] = seen.get((f.path, f.ref.text), 0) + 1
        out.append((f"{f.path}:{f.ref.text}#{n}", f))
    return vocab, out


def _chosen(ctx: Context, found: list[Finding]) -> tuple[Vocabulary | None, list[Ref]]:
    """The references to act on: the current ones that are among the detected ``found``.
    Anything that appeared since is not part of what was shown and backed up."""
    vocab, refs = _rewritable(ctx)
    keys = {f.key for f in found}
    return vocab, [f for key, f in refs if key in keys]


def _detect(ctx: Context) -> list[Finding]:
    return [Finding(key, f.describe(), f.path) for key, f in _rewritable(ctx)[1]]


def _shown(repo: Path, rel: str) -> str:
    path = resolve(repo, rel)
    if path is None:
        return rel
    try:
        return path.relative_to(repo).as_posix()
    except ValueError:
        return str(path)


def _plan(ctx: Context, found: list[Finding]) -> list[Change]:
    by_path: dict[str, list[str]] = {}
    for f in _chosen(ctx, found)[1]:
        by_path.setdefault(f.path, []).append(f"`{f.ref.text}` -> `{f.ref.replacement}`")
    return [
        Change(_shown(ctx.repo, rel), "rewrite " + ", ".join(sorted(set(names))))
        for rel, names in sorted(by_path.items())
    ]


def _apply(ctx: Context, found: list[Finding]) -> list[Corrective]:
    vocab, refs = _chosen(ctx, found)
    if vocab is not None:
        # The runner saved the planned files first; this only rewrites ddflow's own regions.
        rewrite(ctx.repo, vocab, refs, backup=False)
    return []


def _verify(ctx: Context) -> list[str]:
    return []  # the runner's fresh detect is the check: nothing managed may stay deprecated


STALE_REFERENCES = register(
    Migration(
        id="stale-references",
        since_version="0.2.0",
        format_level=1,
        kinds=("references",),
        title="deprecated command and tool names in files ddflow wrote",
        action="rewrite them to the current names, inside ddflow's own regions only",
        detect=_detect,
        plan=_plan,
        apply=_apply,
        verify=_verify,
    )
)
