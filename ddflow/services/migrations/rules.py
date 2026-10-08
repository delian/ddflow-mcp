"""Migration: the project's rule files become rule definitions in the log (D-unify 7).

A rule used to live only in ``.ddflow/rules/<id>.toml``. Rules are now recorded definitions
(``def.recorded`` / ``def.updated``, kind ``rule``) and the files are a view rendered from
them. This imports what a project already has, once:

* a rule file with no ``rule:<id>`` record becomes a ``def.recorded`` whose provenance says it
  was IMPORTED (``via = import``) and whose ``source`` is the file;
* a file whose content differs, by digest, from the active record of its rule -- someone
  edited it by hand -- becomes a ``def.updated`` with the file's fields, so the log says what
  the file says;
* a rule whose record is retired, superseded or merged is left alone.

The files themselves are not touched: this only appends events (the log is append-only), so
there is nothing to back up. The fields, their digest and the redaction profile are
`services.guidance.deffields`, the same as every other writer of a rule definition.
"""

from __future__ import annotations

from ...core import defs as D
from ..guidance import deffields as DF
from ..guidance.kinds import RULE
from ..guidance.record import GuidanceRecord
from ..guidance.store import GuidanceFiles
from . import register
from .base import Change, Context, Corrective, Finding, Migration

RECORD, UPDATE = "record", "update"


def _own_files(ctx: Context) -> list[GuidanceRecord]:
    """The rules the PROJECT has a file for (not the ones ddflow ships beneath them)."""
    files = GuidanceFiles(ctx.repo, RULE)
    return [r for r in files.all() if files.path(r.id).is_file()]


def _needed(ctx: Context) -> list[tuple[str, GuidanceRecord]]:
    """``(RECORD | UPDATE, rule)`` for each rule file the log does not already say."""
    out: list[tuple[str, GuidanceRecord]] = []
    for rec in _own_files(ctx):
        known = ctx.st.defs.get(D.key(RULE.kind, rec.id))
        digest = DF.digest_of(DF.to_fields(rec, RULE), ctx.cfg)
        if known is None:
            out.append((RECORD, rec))
        elif known.live and known.digest != digest:
            out.append((UPDATE, rec))
    return out


def _detect(ctx: Context) -> list[Finding]:
    rel = GuidanceFiles(ctx.repo, RULE).directory.relative_to(ctx.repo).as_posix()
    return [
        Finding(
            f"{how}:{rec.id}",
            f"rule {rec.id}: {'not in the log' if how == RECORD else 'the file differs from the log'}",
            f"{rel}/{rec.id}.toml",
        )
        for how, rec in _needed(ctx)
    ]


def _plan(ctx: Context, found: list[Finding]) -> list[Change]:
    # The log is appended to, no file is rewritten: nothing for a backup to hold.
    return []


def _envelope(ctx: Context, rec: GuidanceRecord, path: str) -> dict[str, object]:
    prov = {"by": ctx.cfg.agent.id, "via": "import", **DF.to_provenance(rec)}
    return {"kind": RULE.kind, "id": rec.id, "source": path, "provenance": prov}


def _apply(ctx: Context, found: list[Finding]) -> list[Corrective]:
    chosen = {f.key for f in found}
    directory = GuidanceFiles(ctx.repo, RULE).directory.relative_to(ctx.repo).as_posix()
    events: list[Corrective] = []
    for how, rec in _needed(ctx):
        if f"{how}:{rec.id}" not in chosen:
            continue
        fields = DF.logged(DF.to_fields(rec, RULE), ctx.cfg)
        data = _envelope(ctx, rec, f"{directory}/{rec.id}.toml")
        data.update(fields=fields, digest=D.digest(fields))
        kind = "def.recorded" if how == RECORD else "def.updated"
        events.append((kind, D.key(RULE.kind, rec.id), data))
    return events


def _verify(ctx: Context) -> list[str]:
    return []  # the runner's fresh detect is the check: every file is now said by the log


RULES_TO_EVENTS = register(
    Migration(
        id="rules-to-events",
        since_version="0.2.1",
        format_level=1,
        kinds=("rules", "log"),
        title="rule files not yet recorded as rule definitions in the log",
        action="append a def.recorded (imported) per rule file, a def.updated per edited file",
        detect=_detect,
        plan=_plan,
        apply=_apply,
        verify=_verify,
    )
)
