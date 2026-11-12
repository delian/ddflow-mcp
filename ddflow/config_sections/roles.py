"""The `[roles]` section: the project's rules for a role, handed over by `ddflow brief --role R`.

A role pack is data: a list of lines per role, each a plain sentence or `rule:<id>` (rendered
from that rule record's title). Override a pack in any config layer; the default packs below
are the project's standing rules for a phase closer, a fixer, a researcher and a reviewer.
Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob


def _whole(v: object) -> str:
    ok = isinstance(v, int) and not isinstance(v, bool) and v >= 0
    return "" if ok else "a whole number of zero or more"


def _lines(v: object) -> str:
    ok = isinstance(v, list) and all(isinstance(x, str) for x in v)
    return "" if ok else "a list of strings"


@declare("roles")
@dataclass
class RolesConfig:
    """Role packs: what a coordinator would otherwise retype to an agent starting a role."""

    budget_tokens: int = knob(
        600,
        doc="Most tokens (about four characters each) a role pack may take; a longer pack is cut at a line boundary and says so. 0 means no cap.",
        check=_whole,
    )
    phase_closer_rules: list[str] = knob(
        factory=list,
        doc="The phase-closer role pack: one line per rule, a plain sentence or `rule:<id>` (the title of that rule record). Empty (the default): the packaged pack, ddflow/templates/roles/phase-closer.txt. A non-empty list replaces it, in any config layer.",
        check=_lines,
    )
    fixer_rules: list[str] = knob(
        factory=list,
        doc="The fixer role pack: one line per rule, a plain sentence or `rule:<id>`. Empty (the default): the packaged pack, ddflow/templates/roles/fixer.txt.",
        check=_lines,
    )
    researcher_rules: list[str] = knob(
        factory=list,
        doc="The researcher role pack: one line per rule, a plain sentence or `rule:<id>`. Empty (the default): the packaged pack, ddflow/templates/roles/researcher.txt.",
        check=_lines,
    )
    reviewer_rules: list[str] = knob(
        factory=list,
        doc="The reviewer role pack: one line per rule, a plain sentence or `rule:<id>`. Empty (the default): the packaged pack, ddflow/templates/roles/reviewer.txt.",
        check=_lines,
    )
