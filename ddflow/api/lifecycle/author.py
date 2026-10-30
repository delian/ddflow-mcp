"""The completion's author identity: the model or family the completing agent is, and
the event keys that record it (DX-author-family).

`complete` used to refuse without an author model (B-complete-requires-model-even-when-
recorded); a harness now declares its family once instead, and the event stores the
family only when the author IS a family, so a model id never reaches the committed log.
*Extracted from `complete.py` for the module-line budget (`no_area_module_is_a_monolith`).
"""

from __future__ import annotations

from ...core.model import State
from ...services import gates as G
from ...services import leases as L


def _session_model(st: State, agent: str) -> str:
    """The model `agent` declared on its most recent OPEN session, or "" (one definition,
    `leases.open_session_model`, shared with what `recover` advises)."""
    return L.open_session_model(st, agent)


def _author_model(st: State, agent: str, model: str, cfg) -> tuple[str, str]:
    """(model, source) judged against for reviewer independence: `--model`, else the
    `[agent].family` the harness configured (DX-author-family): the family is declared
    ONCE and speaks OVER a session's model record, so a model id never reaches the log
    while a family is configured -- else the model the agent's LATEST session declared
    (open or ended -- the record, not its liveness), said out loud and recorded
    (B-complete-requires-model-even-when-recorded)."""
    if model:
        return model, "flag"
    if cfg.agent.family:
        return cfg.agent.family, "config"
    found = _session_model(st, agent)
    if found:
        return found, "session"
    return ("", "")


def _recorded_identity(model: str, model_source: str, cfg) -> tuple[str, bool]:
    """(recorded, family_only) for the completion event's author key: the family spelled
    CANONICALLY when the author IS a family (family_only=True), else the model as given.

    `[agent].family` / DDFLOW_AGENT_FAMILY / a bare family after `--model` makes the
    author a FAMILY, recorded as the family only -- a model id never reaches the log
    where a name was not given (DX-author-family); a non-canonical configured spelling
    (a `[agent].families` NEEDLE such as "gemini") resolves to its canonical family
    value, and one the map does not know is recorded VERBATIM (the operator's
    declaration, never a model id), so the key the log records matches the family the
    verdict judged. Only the CONFIG leg canonicalizes: a needle after `--model` that is
    NOT itself a family value is a MODEL id given out loud, recorded as `author_model`
    (a model name is recorded only when one was actually given) -- tests pin it."""
    family = G.family_name_of(model, cfg.agent.families)
    if model_source == "config" and not family:
        family = G.family_of(model, cfg).strip().lower() or model
    return (family, True) if family else (model, False)


def _author_event_identity(model: str, model_source: str, family_only: bool) -> dict[str, str]:
    """The completion event's author keys: `author_family` when the author declared a
    FAMILY (the family only, never a model id — DX-author-family), else `author_model`
    with the model name, else nothing."""
    if family_only:
        return {"author_family": model, "model_source": model_source}
    if model:
        return {"author_model": model, "model_source": model_source}
    return {}
