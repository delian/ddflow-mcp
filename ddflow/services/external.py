"""Dependencies on items in a sibling repository (`needs = ["run_nemo_run:132.D"]`).

Two projects ddflow was built for depend on each other: a data generator whose plan
waits on trainer-side items in the other repository, each driven by its own agent
service. The dependency lived in prose ("trainer-side only, see 132.D") and nothing
could say when it was satisfied.

This reads the sibling's event log -- read-only, never writing there -- and records
what it OBSERVED in this log as `external.observed`. Readiness then comes from a dated
fact in our own log, which keeps `fold` pure and makes "why was this started?"
answerable later: the log says what the other repository looked like at the time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..core.model import State, fold
from ..core.schedule import is_external
from ..infra.log import EventLog


def repos(cfg: Config, root: Path) -> dict[str, Path]:
    """`[schedule] repos` as name -> absolute path."""
    out: dict[str, Path] = {}
    for spec in cfg.schedule.repos:
        name, _, path = spec.partition("=")
        if name.strip() and path.strip():
            p = Path(path.strip())
            out[name.strip()] = p if p.is_absolute() else (Path(root) / p).resolve()
    return out


def referenced(state: State) -> set[str]:
    """Every external dependency a live item declares."""
    return {dep for it in state.live_items() for dep in it.needs if is_external(dep)}


@dataclass
class Observation:
    dep: str
    state: str
    title: str = ""
    changed: bool = False
    error: str = ""


def sync(log: EventLog, cfg: Config, root: Path, state: State) -> list[Observation]:
    """Observe every referenced external item; record the ones whose state CHANGED.

    Recording only changes keeps a session-start sync from writing an event per
    dependency per session. A repository that is not configured, or whose log cannot be
    read, is reported and records nothing: an outage is not an observation.
    """
    where = repos(cfg, root)
    folded: dict[str, State | str] = {}
    out: list[Observation] = []
    for dep in sorted(referenced(state)):
        name, _, item = dep.partition(":")
        if name not in where:
            out.append(Observation(dep, "", error=f"{name!r} is not in [schedule] repos"))
            continue
        if name not in folded:
            path = where[name]
            if not (path / ".ddflow" / "events").is_dir():
                folded[name] = f"{path} has no ddflow log"
            else:
                try:
                    folded[name] = fold(
                        EventLog(path, log_cfg=cfg.log, cache_writes=False).read_all(), strict=False
                    )
                except OSError as exc:
                    folded[name] = f"could not read {path}: {exc}"
        other = folded[name]
        if isinstance(other, str):
            out.append(Observation(dep, "", error=other))
            continue
        it = other.items.get(item)
        now = "missing" if it is None or it.removed else it.state
        title = it.title if it is not None else ""
        prev = state.external.get(dep, {}).get("state")
        changed = prev != now
        if changed:
            log.append("external.observed", dep, {"state": now, "title": title, "repo": name})
        out.append(Observation(dep, now, title, changed))
    return out
