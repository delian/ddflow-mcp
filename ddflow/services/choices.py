"""Workflow choices: asked, recorded, and -- when nobody answers -- defaulted ON THE RECORD.

ddflow supports several ways of working (trunk or gitflow, local merges or approved
requests, forward-merging or cherry-picking fixes across release lines). Which one a
project uses is the operator's call, or the agent's where the operator lets it decide --
never ddflow's by silence (RESEARCH R17). So every such decision is a `core.flow.Choice`,
and its value comes from, in order:

1. **the config** (`.ddflow/config.toml` or env) -- the operator's file, which always wins;
2. **a recorded choice** -- `flow choose`, by an agent or a person, attributed in the log;
3. **the default** -- which is RECORDED the first time it matters (`adopt_defaults`), so
   the project keeps following it even if a later ddflow ships a different default, and
   anyone can see that it was a default nobody chose rather than a decision.

Until then a relevant, unmade choice is listed in `brief`, so the agent asks at the start
of work instead of discovering at the end that the project wanted something else.
"""

from __future__ import annotations

import getpass
from typing import Any

from ..config import Config
from ..core import flow as F
from ..core.model import State
from ..infra.log import EventLog

EXPLICIT, DEFAULT = "explicit", "default"


def config_wins(source: str) -> bool:
    """Does a CONFIG layer (file, local, env, ...) set this knob, so a recorded choice is
    not applied? The one rule `overlay`, `report` and `flow choose` share: each once spelled
    it separately, and `("file", "env")` missed the local layer (B025c8de942)."""
    return source != "default" and not source.startswith("log:")


def overlay(cfg: Config, st: State) -> None:
    """Apply recorded choices to ``cfg`` wherever no config layer set the knob."""
    for knob, rec in st.flow_choices.items():
        key = f"flow.{knob}"
        if knob in F.CHOICES and not config_wins(cfg.sources.get(key, "default")):
            setattr(cfg.flow, knob, F.choice_value(knob, rec.get("value", "")))
            cfg.sources[key] = f"log:{rec.get('by', EXPLICIT)}"


def _shown(value: Any) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def report(cfg: Config, st: State) -> list[dict[str, Any]]:
    rows = []
    for knob, ch in F.CHOICES.items():
        key = f"flow.{knob}"
        source = cfg.sources.get(key, "default")
        rec = st.flow_choices.get(knob, {})
        value = _shown(getattr(cfg.flow, knob))
        row: dict[str, Any] = {
            "knob": knob,
            "value": value,
            "options": list(ch.options),
            "question": ch.question,
            "relevant": ch.relevant(cfg),
            "source": source,
            "decided": source != "default",
            "recorded": dict(rec),
        }
        if config_wins(source) and rec and _shown(rec.get("value")) != value:
            # Visible, not resolved silently: the file wins, and whoever recorded the
            # other value should know their choice is not in effect.
            row["overridden"] = (
                f"recorded {rec.get('value')!r} ({rec.get('by')}) is overridden by the "
                f"{source} value {value!r}"
            )
        rows.append(row)
    return rows


def pending(cfg: Config) -> list[F.Choice]:
    """Relevant choices nobody has made -- neither in config, nor on the record."""
    return [
        ch
        for ch in F.CHOICES.values()
        if ch.relevant(cfg) and cfg.sources.get(f"flow.{ch.knob}", "default") == "default"
    ]


def validate(knob: str, value: str) -> str:
    ch = F.CHOICES.get(knob)
    if ch is None:
        return f"{knob!r} is not a workflow choice. Choices: {', '.join(F.CHOICES)}"
    if str(value).lower() not in ch.options:
        return f"{knob} must be one of {', '.join(ch.options)}, not {value!r}"
    return ""


def choose(log: EventLog, knob: str, value: str, *, reason: str = "", by: str = EXPLICIT) -> None:
    try:
        user = getpass.getuser()
    except (KeyError, OSError):  # no passwd entry, e.g. in a container
        user = ""
    log.append(
        "flow.chosen",
        knob,
        {"knob": knob, "value": str(value).lower(), "by": by, "reason": reason, "user": user},
    )


def adopt_defaults(log: EventLog, cfg: Config, knobs: list[str]) -> list[str]:
    """Record the default for each relevant, unmade choice in ``knobs``. Returns them.

    Called at the first moment a choice has consequences -- the first branch made, the
    first request opened, the first fix planned across lines. From then on the project
    FOLLOWS that value: a later ddflow with a different default does not change a
    project mid-flight, and `flow show` says it was a default, not a decision.
    """
    adopted = []
    for ch in pending(cfg):
        if ch.knob in knobs:
            choose(
                log,
                ch.knob,
                _shown(getattr(cfg.flow, ch.knob)),
                reason="nobody chose; the default was applied at first use and is followed from here",
                by=DEFAULT,
            )
            cfg.sources[f"flow.{ch.knob}"] = f"log:{DEFAULT}"
            adopted.append(ch.knob)
    return adopted


def brief_block(cfg: Config) -> str:
    """The 'please decide' section of a brief. Empty when nothing relevant is undecided."""
    todo = pending(cfg)
    if not todo:
        return ""
    lines = [
        "## Open workflow choices",
        "",
        "Nobody has decided these for this project. Ask the operator if you can; if they "
        "do not care, choose what suits the project with `ddflow flow choose <knob> "
        "<value> --reason ...`. Left alone, the default shown is applied the first time it "
        "matters and followed from then on.",
        "",
    ]
    for ch in todo:
        lines.append(
            f"- **{ch.knob}** = `{_shown(getattr(cfg.flow, ch.knob))}` (default; options: "
            f"{', '.join(ch.options)}) — {ch.question}"
        )
    return "\n".join(lines) + "\n"
