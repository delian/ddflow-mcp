"""The event log AS A COMMIT RECORDS IT, for the generated-view check.

A staged view or rule file must agree with the log committed beside it. The shards on
disk are not that log: other agents append to them while the commit is made (bug
B10725b2c5e), so the check reads the shards from the commit's own index instead.
"""

from __future__ import annotations

from pathlib import Path

from ..core.events import Event
from ..infra import git as G
from ..infra import log as L
from ..infra import proc as P


class CommittedLog:
    """Duck-types the part of `EventLog` the view check reads: ``dir`` and ``read_all``."""

    def __init__(self, dir: Path, events: list[Event]) -> None:
        self.dir = dir
        self._events = events

    def read_all(self) -> list[Event]:
        return list(self._events)


def committed_log(tree: Path, rel_dir: str, log_dir: Path) -> CommittedLog | None:
    """The shards under ``rel_dir`` as ``tree``'s index holds them; None if git cannot say.

    None is never read as an empty log: the caller keeps its refusal.
    """
    names = G.git_paths(tree, "ls-files", "--", rel_dir)
    if names is None:
        return None
    blobs: list[bytes] = []
    for name in sorted(n for n in names if n.endswith(".jsonl")):
        r = G.run(tree, "show", f":{name}", binary=True, timeout=P.TIMEOUTS["git_listing"])
        if not r.ok:
            return None
        blobs.append(r.out_bytes)
    return CommittedLog(log_dir, L._sorted_unique([e for b in blobs for e in L._parse_lines(b)[0]]))
