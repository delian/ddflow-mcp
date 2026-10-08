"""The rule files as a checked generated view of the log (D-unify 7).

The log holds each rule as a ``rule`` definition (`core.defs`); ``.ddflow/rules/<id>.toml`` is
rendered from it. A file and its definition agree when the digest of the file's fields
(`deffields.digest_of`) is the one on the definition. This is the one place that asks:

* `edits`: the files the log does not say yet -- a file with no record (RECORD) and a file
  whose content differs from its live record, i.e. a hand edit (UPDATE). The import migration,
  `rule sync` and the drift report all read it, so none can disagree about what "edited" is;
* `drift`: `edits` plus the live records with no file (MISSING), as ``Drift`` rows;
* `render`: the file text a definition renders to, and `restore`: write the missing ones;
* `notes`: the drift as ``ddflow doctor`` notes.

A retired, superseded or merged rule is left alone, as is a file that does not load (that is
``doctor``'s ``file:line`` report). Timestamps are not content.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from ...config import Config
from ...core import defs as D
from ...core.model import State
from ..overlay import OverlayError
from . import deffields as DF
from . import fileformat
from .kinds import RULE
from .record import GuidanceRecord
from .store import GuidanceFiles

RECORD, UPDATE = "record", "update"
UNRECORDED, EDITED, MISSING = "unrecorded", "edited", "missing"
#: How many drifted rules a doctor note names before it counts the rest.
_SHOWN = 5
UNRECORDED_WHY = "no record of it in the log"
EDITED_WHY = "differs from the log's record: edited by hand"


@dataclass(frozen=True)
class Drift:
    """One rule file and its record disagreeing: ``how`` is UNRECORDED (a file the log has no
    record of), EDITED (a file whose content differs from its live record) or MISSING (a live
    record with no file)."""

    id: str
    how: str
    path: str

    @property
    def text(self) -> str:
        what = {
            UNRECORDED: "has a file the log does not record",
            EDITED: "was edited since the log recorded it",
            MISSING: "is recorded in the log but has no file",
        }[self.how]
        return f"rule {self.id} {what} ({self.path})"


def files(repo: Path) -> GuidanceFiles:
    return GuidanceFiles(repo, RULE)


def relpath(repo: Path, rid: str) -> str:
    """The repository-relative path of a rule's file."""
    return files(repo).path(rid).relative_to(Path(repo)).as_posix()


def project_rules(repo: Path) -> list[GuidanceRecord]:
    """The rules the PROJECT has a file for (not the ones ddflow ships beneath them), keyed by
    FILE NAME -- the id `rule get` reads them by -- even when the file's own ``id`` says
    otherwise. Only reads: the rules directory is not created."""
    store = files(repo)
    out: list[GuidanceRecord] = []
    for name in store.names():
        if not store.path(name).is_file():
            continue
        try:
            out.append(replace(store.read(name), id=name))
        except (OSError, OverlayError, ValueError):
            continue  # a file that does not load is `ddflow doctor`'s to report
    return out


def edits(repo: Path, cfg: Config, st: State) -> list[tuple[str, GuidanceRecord]]:
    """``(RECORD | UPDATE, rule)`` for each rule file the log does not already say."""
    out: list[tuple[str, GuidanceRecord]] = []
    for rec in project_rules(repo):
        known = st.defs.get(D.key(RULE.kind, rec.id))
        digest = DF.digest_of(DF.to_fields(rec, RULE), cfg)
        if known is None:
            out.append((RECORD, rec))
        elif known.live and known.digest != digest:
            out.append((UPDATE, rec))
    return out


def disagreement(cfg: Config, st: State, rid: str, text: str) -> str:
    """Why a rule file's ``text`` is not what the log says of rule ``rid``, or "" when it is
    (or is not this check's: it does not load, or its record is retired)."""
    try:
        rec = replace(fileformat.parse(text, RULE), id=rid)
    except ValueError:
        return ""  # `doctor` reports a file that does not load, with file:line
    known = st.defs.get(D.key(RULE.kind, rid))
    if known is None:
        return UNRECORDED_WHY
    if known.live and known.digest != DF.digest_of(DF.to_fields(rec, RULE), cfg):
        return EDITED_WHY
    return ""


def live_rule_ids(st: State) -> list[str]:
    """The ids of the rule records the log holds as live, in id order."""
    return sorted(d.id for d in st.defs.values() if d.kind == RULE.kind and d.live)


def live_without_file(repo: Path, st: State) -> list[str]:
    """The ids of live rule records that have no file of their own, in id order."""
    store = files(repo)
    return [rid for rid in live_rule_ids(st) if not store.path(rid).is_file()]


def drift(repo: Path, cfg: Config, st: State) -> list[Drift]:
    """Every disagreement between the rule files and the log, in id order."""
    rows = [
        Drift(rec.id, UNRECORDED if how == RECORD else EDITED, relpath(repo, rec.id))
        for how, rec in edits(repo, cfg, st)
    ]
    rows += [Drift(rid, MISSING, relpath(repo, rid)) for rid in live_without_file(repo, st)]
    return sorted(rows, key=lambda r: (r.id, r.how))


def render(st: State, rid: str) -> str:
    """The file text the log's record of rule ``rid`` renders to (KeyError: no such record)."""
    rec = st.defs[D.key(RULE.kind, rid)]
    return DF.render(rid, rec.fields, rec.provenance, RULE)


def restore(repo: Path, st: State, ids: list[str] | None = None) -> list[str]:
    """Write the file of each live rule record that has none (or just ``ids`` of them); the ids
    written. A file that exists is never touched: it is `edits`' to record first."""
    store = files(repo)
    wanted = live_without_file(repo, st)
    done: list[str] = []
    for rid in wanted:
        if ids is not None and rid not in ids:
            continue
        rec = st.defs[D.key(RULE.kind, rid)]
        store.ensure_dir()
        store.write(DF.from_fields(rid, rec.fields, rec.provenance, RULE), new=True)
        done.append(rid)
    return done


def notes(repo: Path, cfg: Config, st: State) -> list[str]:
    """``ddflow doctor`` notes for the drift: a note, not a problem -- one command settles it."""
    rows = drift(repo, cfg, st)
    if not rows:
        return []
    shown = "; ".join(r.text for r in rows[:_SHOWN])
    if len(rows) > _SHOWN:
        shown += f"; and {len(rows) - _SHOWN} more"
    return [f"rules: {len(rows)} rule file(s) disagree with the log: {shown} (`ddflow rule sync`)"]
