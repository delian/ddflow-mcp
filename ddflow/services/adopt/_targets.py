"""The harness targets and native rules surface `adopt` writes, as data.

This is the bottom of the package: a VIEW over the descriptors in
`ddflow/harnesses/<id>.toml` (`services.harnessreg`), one record per agent rather than
parallel dicts keyed by the same string.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import harnessreg as HR
from ..mcpconfig import SHAPE_NONE


@dataclass(frozen=True)
class AgentTarget:
    """One supported harness: its delta doc, its MCP config file, and that file's shape.

    One record per agent rather than parallel dicts keyed by the same string. Two dicts
    that must be edited together are the drift this project keeps paying for -- there
    were three of them before this (a ternary in the adopter, `_json_field` in the
    companions writer, and a `SERVERS_FIELD_AGENTS` set beside it), and the comment on
    the last one correctly predicted that a third shape would break them.
    """

    delta: str  #: filename under `templates/drivers/deltas/`
    config: str  #: MCP config path relative to the repo root; "" when none exists
    shape: str  #: one of the SHAPE_* constants above

    # There is deliberately NO `rules` field here. One briefly existed, populated for six
    # agents and read by nothing: `NATIVE_RULES` already owns which surface an agent reads
    # and `adopt` is driven entirely by that, so the field was a second, unowned copy of the
    # same fact -- `NATIVE_RULES["tabnine"].path` said `.tabnine/guidelines/ddflow.md` while
    # it said `.tabnine/guidelines/`. The next maintainer would have filled it in believing
    # `adopt` honoured it. Found by roborev on 3040d4b; the duplicate-then-drift class.

    @property
    def writes_config(self) -> bool:
        return bool(self.config) and self.shape != SHAPE_NONE


#: Every harness ddflow can adopt a project into: a VIEW over the descriptors in
#: `ddflow/harnesses/<id>.toml` (`services/harnessreg`), where each path and shape carries the
#: reason it was chosen. Each is taken from that product's OWN documentation
#: (`docs/RESEARCH.md` R15, R-hx) -- never from the family resemblance between them, because a
#: wrong key is valid JSON that the agent silently ignores, which looks exactly like success.
#: An agent with no project-level MCP file has `config == ""` and `SHAPE_NONE`: a verified
#: absence, with the manual step named in its delta doc.
AGENT_TARGETS: dict[str, AgentTarget] = {
    h.id: AgentTarget(h.delta, h.mcp.path, h.mcp.shape) for h in HR.load()
}


#: How a native rules surface must be written.
#:
#: `FORM_WHOLE` — the file is ddflow's entirely, because something must come FIRST in it
#:   (Cursor's `.mdc` binds through YAML frontmatter, which cannot be preceded by a marker).
#: `FORM_BLOCK` — a managed BEGIN/END block inside a file the project may already own, so
#:   the operator's own rules survive alongside ours.
#: `FORM_AIDER` — not a rules file at all: a `read:` entry in `.aider.conf.yml`, because
#:   Aider discovers nothing automatically and only loads what it is told to load.
FORM_WHOLE = "whole"
FORM_BLOCK = "block"
FORM_AIDER = "aider-conf"


@dataclass(frozen=True)
class NativeRule:
    """One agent's own instruction surface, and how to write into it."""

    path: str  #: relative to the repo root
    form: str  #: FORM_WHOLE | FORM_BLOCK | FORM_AIDER
    why: str  #: why `AGENTS.md` alone is not enough here — shown by `doctor`


#: Agents whose OWN instruction surface must also carry the block, because `AGENTS.md`
#: alone does not reach them.
#:
#: **The block is INLINED into each, never pointed at.** A pointer was the obvious design
#: and this repo had already refuted it: *"a link is only followed if the agent chooses to
#: follow it"* (`templates/drivers/deltas/kilo-cline.md`). A one-line stub saying "see
#: AGENTS.md" is therefore a rule that binds only if the model feels like opening a file,
#: which is exactly the property an enforced rule must not have.
#:
#: Inlining means N copies of one text, and the answer to that is the answer already used
#: for `AGENTS.md` and `CLAUDE.md`: ONE generator (`project_section`), a managed block, and
#: `rules_status()` comparing every copy against it so drift is reported rather than
#: discovered. Duplication that a check owns is not duplication that drifts.
#:
#: Absent from this map means the agent reads `AGENTS.md` directly — which is most of them,
#: and is the whole reason `AGENTS.md` is the canonical surface.
NATIVE_RULES: dict[str, NativeRule] = {
    h.id: NativeRule(
        h.instructions.native_path, h.instructions.native_form, h.instructions.native_why
    )
    for h in sorted(
        (h for h in HR.load() if h.instructions is not None and h.instructions.native_path),
        key=lambda h: h.instructions.native_rank,  # type: ignore[union-attr]
    )
}
