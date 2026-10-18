"""The one sensitivity vocabulary (SE-sensitivity.1): levels, the scan mapping, the egress rule.

Levels (public, internal, restricted, confidential, and any a project adds) are DATA: the
packaged ``templates/sensitivity.toml`` and, over it, a project's ``.ddflow/sensitivity.toml``,
parsed here into a `Vocabulary` whose levels sit in a `core.registry.Registry`. This module
decides three things and nothing else: which of two levels is higher (`rank`), what level a
write-time scan's findings put a text at (`scan_level`, from the kinds `core.redact` counts),
and whether a level may go to a model (`may_send`). Where labels are stored and where the
check is applied are the next parts (SE-sensitivity.2, .3).

Pure: only `tomllib` and `core.registry`.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .registry import Registry


class SensitivityError(ValueError):
    """A vocabulary that is not one."""


@dataclass(frozen=True)
class Level:
    name: str
    rank: int
    to_model: bool
    summary: str = ""


class Vocabulary:
    """Levels by name, and the scan mapping from redaction kind to level."""

    def __init__(self, levels: Iterable[Level], scan: Mapping[str, str], unlabelled: str) -> None:
        self.levels: Registry[Level] = Registry("sensitivity level", key=lambda lv: lv.name)
        ranks: dict[int, str] = {}
        for lv in levels:
            if lv.rank in ranks:
                raise SensitivityError(
                    f"levels {ranks[lv.rank]!r} and {lv.name!r} share rank {lv.rank}"
                )
            ranks[lv.rank] = lv.name
            self.levels.register(lv)
        unknown = sorted(set(scan.values()) - set(self.levels))
        if unknown:
            raise SensitivityError(f"the scan mapping names unknown level(s) {', '.join(unknown)}")
        if unlabelled not in self.levels:
            raise SensitivityError(f"unlabelled level {unlabelled!r} is not one of the levels")
        self.scan = dict(scan)
        self.unlabelled = unlabelled

    def ordered(self) -> list[Level]:
        return sorted(self.levels.values_list(), key=lambda lv: lv.rank)

    def rank(self, name: str) -> int:
        return self.levels[name].rank

    def highest(self, names: Iterable[str]) -> str:
        """The highest of ``names`` (an unknown one is an error naming the levels known)."""
        return max(names, key=self.rank)

    def scan_level(self, found: Mapping[str, int], floor: str = "") -> str:
        """The level a text is at after a scan: the highest of ``floor`` (default: the
        unlabelled level) and the level of every kind found (``found`` is
        `core.redact.Redacted.counts`). A kind found that the vocabulary does not map fails
        closed: it counts as the highest level, never as nothing."""
        top = self.ordered()[-1].name
        hits = [self.scan.get(k, top) for k, n in found.items() if n]
        return self.highest([floor or self.unlabelled, *hits])

    def may_send(self, level: str, allowed: Iterable[str] = ()) -> bool:
        """May text at ``level`` go to a model? Yes when the level is marked ``to_model`` or the
        project lists it in ``allowed``."""
        return self.levels[level].to_model or level in set(allowed)


def _level(n: int, raw: Any) -> Level:
    where = f"level #{n}"
    if not isinstance(raw, dict):
        raise SensitivityError(f"{where} must be a table")
    if bad := sorted(set(raw) - {"name", "rank", "to_model", "summary"}):
        raise SensitivityError(f"{where}: unknown key(s) {', '.join(bad)}")
    name, rank, to_model = raw.get("name"), raw.get("rank"), raw.get("to_model")
    if not isinstance(name, str) or not name.strip():
        raise SensitivityError(f"{where} needs a name")
    if isinstance(rank, bool) or not isinstance(rank, int) or rank < 0:
        raise SensitivityError(f"level {name!r}: rank is a whole number, 0 or more")
    if not isinstance(to_model, bool):
        raise SensitivityError(f"level {name!r}: to_model is true or false")
    return Level(name.strip(), rank, to_model, str(raw.get("summary", "")))


def parse(*texts: str) -> Vocabulary:
    """The vocabulary in ``texts`` (the packaged one first, a project's after): a level of a
    later text replaces one of the same name, and its ``[scan]`` entries replace earlier ones."""
    levels: dict[str, Level] = {}
    scan: dict[str, str] = {}
    unlabelled = ""
    for text in texts:
        try:
            raw = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise SensitivityError(f"sensitivity vocabulary is not TOML: {exc}") from None
        if bad := sorted(set(raw) - {"level", "scan", "unlabelled"}):
            raise SensitivityError(f"unknown key(s) {', '.join(bad)}")
        entries = raw.get("level", [])
        if not isinstance(entries, list):
            raise SensitivityError("levels are [[level]] tables")
        for i, entry in enumerate(entries, 1):
            lv = _level(i, entry)
            levels[lv.name] = lv
        table = raw.get("scan", {})
        if not isinstance(table, dict) or not all(isinstance(v, str) for v in table.values()):
            raise SensitivityError("[scan] is a table of redaction kind = level")
        scan.update(table)
        if "unlabelled" in raw:
            if not isinstance(raw["unlabelled"], str):
                raise SensitivityError("unlabelled is the name of a level")
            unlabelled = raw["unlabelled"]
    if not levels:
        raise SensitivityError("a vocabulary needs at least one level")
    return Vocabulary(levels.values(), scan, unlabelled)
