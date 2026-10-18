"""Egress at the doors that feed models (SE-sensitivity.3): a record whose sensitivity label may
not go to a model (`core.sensitivity.Vocabulary.may_send`, widened by ``[sensitivity].model_allowed``)
is withheld from a door listed in ``[sensitivity].egress_doors``. A record with no label is at the
vocabulary's unlabelled level. A label the vocabulary no longer has is withheld (fail closed).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Config
from ..core import defs as D
from ..core.sensitivity import LABEL_FIELD, Vocabulary
from .sensitivity import load


class Egress:
    """The rule for one repository, built once per call at a door."""

    def __init__(
        self, repo: Path | str | None, cfg: Config, vocab: Vocabulary | None = None
    ) -> None:
        self.cfg = cfg.sensitivity
        self.vocab = vocab or load(repo)

    def withholds(self, door: str, fields: dict[str, Any] | None) -> bool:
        """Is a record with these fields kept from ``door``?"""
        if door not in self.cfg.egress_doors:
            return False
        label = (fields or {}).get(LABEL_FIELD) or self.vocab.unlabelled
        if label not in self.vocab.levels:
            return True
        return not self.vocab.may_send(label, self.cfg.model_allowed)

    def withholds_def(self, door: str, st: Any, def_kind: str, rid: str) -> bool:
        rec = st.defs.get(D.key(def_kind, rid))
        return rec is not None and self.withholds(door, dict(rec.fields))
