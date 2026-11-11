"""The fold of `agent.onboarded`: what an agent's onboarding put into the project.

One event is one run of an onboarding writer and carries every project-scope artifact it
wrote (`services.installmanifest`). The newest record of a path and key wins, so a rewrite
through upgrade keeps the manifest true. Pure."""

from __future__ import annotations

from typing import Any

from ..events import Event
from ..records import State

ARTIFACT_FIELDS = ("path", "kind", "key", "digest", "descriptor", "version")


def onboarded_of(st: State) -> dict[str, dict[str, dict[str, Any]]]:
    """The folded manifest, agent -> "path\\0key" -> artifact (a declared `State` field; the
    setdefault serves an instance restored without ``__init__``)."""
    return st.__dict__.setdefault("onboarded", getattr(st, "onboarded", {}))


def identity(path: str, key: str) -> str:
    return f"{path}\0{key}"


def h_onboarded(st: State, ev: Event) -> None:
    agent = str(ev.data.get("agent") or ev.subject)
    rows = onboarded_of(st).setdefault(agent, {})
    for raw in ev.data.get("artifacts", []):
        if not isinstance(raw, dict) or not raw.get("path"):
            continue
        art = {f: str(raw.get(f, "")) for f in ARTIFACT_FIELDS}
        rows[identity(art["path"], art["key"])] = art


INSTALLMANIFEST_HANDLERS = {"agent.onboarded": h_onboarded}
