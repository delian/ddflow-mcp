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
from collections.abc import Iterable

from ..globs import overlap as globs_overlap
from ..model import Item

#: The ``wave:N`` tag shape, the same `services.waves.WAVE_TAG` reads for a whole `State`;
#: this reads ONE item's tags: the number among the item's ``wave:N`` stamps, or ``None``.
_WAVE_TAG = re.compile(r"wave:\\d+")


def wave_of(tags: Iterable[str]) -> int | None:
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


def co_location_hints(items: list[Item]) -> list[dict]:
    """Cluster ``items`` that should run in one lane: items that share a declared
    dependency, or whose globs overlap, unioned transitively.

    Returns one entry per cluster of TWO OR MORE items (a singleton is not a hint), each
    ``{"items", "shared_deps", "overlap"}``:

    * ``items`` — the member ids, sorted;
    * ``shared_deps`` — dependency ids named by at least two members;
    * ``overlap`` — the overlapping glob pairs inside the cluster, so the advice says WHY
      the cluster must be one lane (minimize locking), not just who is in it.
    """
    if len(items) < 2:
        return []
    ids = [it.id for it in items]
    needs = [set(it.needs) for it in items]
    globs = [list(it.globs) for it in items]
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if needs[i] & needs[j]:
                union(i, j)
            elif any(globs_overlap(g, h) for g in globs[i] for h in globs[j]):
                union(i, j)
    clusters: dict[int, list[int]] = {}
    for i in range(len(items)):
        clusters.setdefault(find(i), []).append(i)
    out: list[dict] = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        counts = Counter(d for it in members for d in needs[it])
        shared = sorted(d for d, n in counts.items() if n >= 2)
        pairs: set[tuple[str, str]] = set()
        for a in members:
            for b in members:
                if a >= b:
                    continue
                for g in globs[a]:
                    for h in globs[b]:
                        if globs_overlap(g, h):
                            pairs.add(tuple(sorted((g, h))))
        out.append(
            {
                "items": sorted(ids[i] for i in members),
                "shared_deps": shared,
                "overlap": sorted([g, h] for g, h in pairs),
            }
        )
    # Deterministic: sort clusters by their first member, so the answer does not depend
    # on the order `items` happened to be walked.
    out.sort(key=lambda c: c["items"])
    return out
