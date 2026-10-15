"""Needs edges that can never be met: the dependency was abandoned or removed, not done."""

from __future__ import annotations

from .model import DONE


def gone(dep) -> bool:
    """A dependency that finished without being done: abandoned, or removed."""
    return dep.removed or (dep.terminal and dep.state != DONE)


def drop_command(st, item, also: str = "") -> str:
    """The command that drops every gone need of ``item`` (and ``also``, about to be gone):
    ``--needs`` replaces the list, so one command per edge would undo each other."""
    keep = (n for n in item.needs if n != also and not (n in st.items and gone(st.items[n])))
    rest = ",".join(keep)
    return f'ddflow update {item.id} --needs "{rest}"'


def stuck_edges(st) -> list[tuple[str, str, str]]:
    """(open item, gone dependency, command that drops the edge), in item order."""
    out = []
    for it in st.items.values():
        if it.removed or it.terminal:
            continue
        for dep_id in it.needs:
            dep = st.items.get(dep_id)
            if dep is not None and gone(dep):
                out.append((it.id, dep_id, drop_command(st, it)))
    return out


def stuck_edges_to(st, dep_id: str) -> list[tuple[str, str]]:
    """(open item, edge-dropping command) for each open item that needs ``dep_id``."""
    return [
        (it.id, drop_command(st, it, dep_id))
        for it in st.items.values()
        if not it.removed and not it.terminal and dep_id in it.needs
    ]
