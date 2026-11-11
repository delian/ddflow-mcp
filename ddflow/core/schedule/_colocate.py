"""Co-location advice for the ready set: which ready items to place in ONE lane.

`next` offers independent items an agent may start right now. Independence says they
may run in PARALLEL, in separate worktrees; it says nothing about where an orchestrator
should PLACE them to minimise lock contention with other lanes. Two ready items that
share a dependency, or whose declared globs overlap, will contend on the same held file
or the same upstream at merge/claim time — if two different lanes each take one, one of
them will wait on the other. Placing that cluster in a single lane runs them one after
another instead, leaving the other lanes free.

Pure: it reads the items it is given and nothing else.
"""

from __future__ import annotations

import re
from collections import Counter

from ..globs import overlap as globs_overlap

#: The ``wave:N`` tag shape, the same `services.waves.WAVE_TAG` reads for a whole `State`;
#: this reads ONE item's tags: the number among the item's ``wave:N`` stamps, or ``None``.
_WAVE_TAG = re.compile(r"wave:\d+")

#: A co-location cluster is a HINT only at this many items or more; a singleton is not
#: advice, so an item alone in its component drops out below.
_MIN_CLUSTER = 2


def wave_of(tags: list) -> int | None:
    """The ``wave:N`` number an item's tags carry, or ``None`` when none is stamped.

    ``wave:0`` = no open prerequisite; ``wave:N`` = 1 + the highest wave among open
    prerequisites (`services.waves.derive`). An item with no ``wave:*`` stamp yields
    ``None``, which buckets after every stamped wave so an unstamped item never hides a
    stamped one.
    """
    for tag in tags:
        if _WAVE_TAG.fullmatch(tag):
            return int(tag[len("wave:") :])
    return None


def co_location_hints(items: list) -> list:
    """Cluster ``items`` into the sets that should run in one lane.

    Two items are joined (union-find) when they share a declared dependency, or their
    globs overlap (and then transitively); the clusters are the connected components
    that follow. Returns one ``{"items", "shared_deps", "overlap"}`` entry per cluster
    of TWO OR MORE items (a singleton is not a hint).
    """
    if len(items) < _MIN_CLUSTER:
        return []
    ids = [it.id for it in items]
    needs = [set(it.needs) for it in items]
    globs = [list(it.globs) for it in items]
    parent = _union_roots(len(items), needs, globs)
    groups = {}
    for i in range(len(items)):
        groups.setdefault(_find(parent, i), []).append(i)
    hints = [
        _cluster(ids, needs, globs, members)
        for members in groups.values()
        if len(members) >= _MIN_CLUSTER
    ]
    return sorted(hints, key=lambda c: c["items"])


def _union_roots(n, needs: list, globs: list) -> list:
    """A union-find parent array over ``n`` items, joining shared-dep and glob-overlap pairs."""
    parent = list(range(n))
    for i in range(n):
        for j in range(i + 1, n):
            if needs[i] & needs[j]:
                _union(parent, i, j)
    for i in range(n):
        for j in range(i + 1, n):
            if _overlap_globs(globs[i], globs[j]):
                _union(parent, i, j)
    return parent


def _union(parent: list, a: int, b: int) -> None:
    ra = _find(parent, a)
    rb = _find(parent, b)
    if ra != rb:
        parent[rb] = ra


def _find(parent: list, i: int) -> int:
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


def _overlap_globs(a: list, b: list) -> bool:
    return any(globs_overlap(g, h) for g in a for h in b)


def _cluster(ids: list, needs: list, globs: list, members: list) -> dict:
    return {
        "items": sorted(ids[i] for i in members),
        "shared_deps": _shared_deps(needs, members),
        "overlap": _overlap_pairs(globs, members),
    }


def _shared_deps(needs: list, members: list) -> list:
    counts = Counter(d for i in members for d in needs[i])
    return sorted(d for d, n in counts.items() if n >= _MIN_CLUSTER)


def _overlap_pairs(globs: list, members: list) -> list:
    pairs = set()
    for a, b in _pairs(members):
        for g in globs[a]:
            for h in globs[b]:
                if globs_overlap(g, h):
                    pairs.add(tuple(sorted((g, h))))
    return sorted(list(p) for p in pairs)


def _pairs(members: list) -> list:
    return [(a, b) for a in members for b in members if a < b]
