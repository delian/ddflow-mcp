"""The shared kernel every command surface needs: exit codes, `Ctx`, tiny helpers.

Extracted so that command modules can import it WITHOUT importing `cli`. That matters
more than tidiness: `surfaces/` may import `surfaces/`, which the layer check permits
by design, so a command module reaching back up to `cli` for `Ctx` would recreate the
module-level cycle `test_no_module_level_import_cycles` was written to forbid — and
`cli.py` grew to 4,314 lines partly because there was nowhere else for this to live.

Nothing here decides anything. `Ctx` resolves the repo, the config, the log, the store
and the gate set once per invocation; the rest are three-line conveniences that were
otherwise copied. Policy belongs in `services/`, and the answer a command returns
belongs in `api/`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from ..config import Config
from ..core.model import fold
from ..infra import worktree as W
from ..infra.log import EventLog, resolve_agent_id
from ..infra.store import Store
from ..services import gates as G

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3

#: Splitting into one piece is a rename, not a split.
_MIN_SPLIT_PARTS = 2
UNAVAILABLE_EXIT = NOTHING

#: How many offending files a refusal lists before summarising the rest. Enough to see
#: whether they are build artefacts or real source -- which is the judgement the
#: operator has to make -- without burying the remedy underneath them.
MAX_LISTED_FILES = 10

#: `worktree.merge`/`remove` return this to mean "refused on a precondition" as opposed
#: to "git failed". It maps to the CLI's REFUSED, and naming it keeps the two exit
#: vocabularies from being silently conflated.
GIT_REFUSED = 2


class Ctx:
    """Everything a command needs, resolved once."""

    def __init__(self, args: argparse.Namespace) -> None:
        start = Path(args.repo or os.environ.get("DDFLOW_REPO") or Path.cwd())
        #: WHERE THE CALLER IS, before resolution to the primary. `self.repo` is
        #: deliberately the primary checkout -- that is what makes every worktree share
        #: one event log -- but resolving loses the one fact `claim` needs to avoid
        #: building a rival worktree: whether the caller was already standing in one.
        self.called_from = start
        try:
            self.repo = W.repo_root(start)
        except W.GitError:
            self.repo = start.resolve()
        self.cfg = Config.load(self.repo)
        # `DDFLOW_AGENT` is the short alias documented in server.json and used by MCP
        # clients and the git hook, which run in an environment where passing `--agent`
        # is not possible. It was documented before it was read -- and a dead env var
        # in a published manifest is worse than an undocumented one, because operators
        # set it and nothing happens.
        # One encoding of the precedence, shared with the typed MCP path. Two copies
        # drifted the moment the second path existed: `api._load` built its log with
        # `EventLog(repo, "")`, which reads neither the env var nor the config, so the
        # two surfaces wrote the same connection's events under different identities.
        resolved, layer = resolve_agent_id(self.repo, self.cfg, args.agent or "")
        if resolved != self.cfg.agent.id:
            self.cfg.agent.id = resolved
            # The layer that actually won, not a guess from comparing values. With
            # nothing set anywhere the derived name differs from `cfg.agent.id` (""),
            # so the old code fired and recorded `env` -- and `config --explain` then
            # blamed the environment for a variable nobody had exported.
            self.cfg.sources["agent.id"] = layer
        self.log = EventLog(
            self.repo, self.cfg.agent.id, lock_timeout_s=self.cfg.lease.acquire_timeout_s
        )
        self.store = Store(self.repo, self.cfg)
        self.gates = G.load_gates(self.repo, self.cfg)
        self.json = bool(getattr(args, "json", False))

    def state(self):
        return fold(self.log.read_all(), strict=False)

    def out(self, human: str, data: Any = None) -> None:
        if self.json:
            print(
                json.dumps(
                    _plain(data if data is not None else {"message": human}), indent=2, default=str
                )
            )
        else:
            print(human)


def _resolved(c: Ctx, obj: Any) -> Any:
    """Plain-data view with worktree paths resolved to absolute.

    The LOG stores worktree paths relative to the repo root, which is what makes a
    committed log true on every checkout. But a CALLER needs a path it can `cd` to: an
    agent handed ".ddflow-worktrees/T1" has to know what it is relative to, and will
    resolve it against its own cwd — which is frequently not the repo root. Storage
    portable, interface usable; the conversion happens here, at the boundary.
    """
    out = _plain(obj)
    if isinstance(out, dict):
        for key in ("worktree", "path"):
            val = out.get(key)
            if isinstance(val, str) and val and not os.path.isabs(val):
                out[key] = str(W.load_path(c.repo, val))
        if isinstance(out.get("lease"), dict):
            lv = out["lease"].get("worktree")
            if isinstance(lv, str) and lv and not os.path.isabs(lv):
                out["lease"]["worktree"] = str(W.load_path(c.repo, lv))
    return out


def _plain(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _plain(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


def _csv(v: str | None) -> list[str]:
    """`config.csv_list` — one parser for the comma-separated notation, not two."""
    from ..config import csv_list

    return csv_list(v)


def _auto_id(prefix: str, *parts: str) -> str:
    """A collision-free auto id.

    Second-resolution timestamps (`f"L{int(time.time())}"`) collide whenever two items
    are created in the same second -- which a script, a loop, or an agent recording two
    lessons from one bug hunt does routinely. The collision is SILENT: the second record
    overwrites the first in the fold, so the entry simply disappears. Measured: 7
    lessons added in one second, 2 survived.

    Content-addressed instead, so the id is stable for identical content and distinct
    for anything else, with a microsecond stamp to separate genuine duplicates.
    """
    seed = "|".join(parts) + f"|{time.time_ns()}"
    return prefix + hashlib.blake2b(seed.encode("utf-8"), digest_size=5).hexdigest()


# -- commands --------------------------------------------------------------------------


def _require_item(c: Ctx, item_id: str, st=None):
    """The item, or ``None`` after reporting why — the one place that says so.

    `no such item` was written eleven times in this module and twice more, differently,
    in `services.gates` and `services.leases`. Eleven copies of a two-line check is
    eleven chances for one to forget `removed`, which is exactly what a caller acting
    on a removed item does not expect: the item folds, so `.get()` finds it, and only
    the flag says it is gone.
    """
    st = st if st is not None else c.state()
    it = st.items.get(item_id)
    if it is None or it.removed:
        gone = " (it was removed from the queue)" if it is not None else ""
        print(f"no such item {item_id!r}{gone}", file=sys.stderr)
        return None
    return it
