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
import os
import sys
from pathlib import Path
from typing import Any

from ..api import identity as ID
from ..config import Config
from ..core.ids import auto_id
from ..core.model import fold
from ..core.outcome import (  # noqa: F401  (the commands import these from here)
    FAIL,
    NOTHING,
    OK,
    REFUSED,
)
from ..core.plain import plain
from ..infra import worktree as W
from ..infra.log import EventLog
from ..infra.store import Store
from ..services import gates as G
from .render import emit_json

#: Splitting into one piece is a rename, not a split.
_MIN_SPLIT_PARTS = 2
UNAVAILABLE_EXIT = NOTHING

#: How many offending files a refusal lists before summarising the rest. Enough to see
#: whether they are build artefacts or real source -- which is the judgement the
#: operator has to make -- without burying the remedy underneath them.
MAX_LISTED_FILES = 10

#: Re-exported from `infra/worktree.py`, which is where the functions that RETURN it
#: live. The api layer needs the same constant and may not import a surface.
GIT_REFUSED = W.GIT_REFUSED


class Ctx:
    """Everything a command needs, resolved once."""

    def __init__(self, args: argparse.Namespace) -> None:
        start = Path(args.repo or os.environ.get("DDFLOW_REPO") or Path.cwd())
        #: WHERE THE CALLER IS, before resolution to the primary. `self.repo` is
        #: deliberately the primary checkout -- that is what makes every worktree share
        #: one event log -- but resolving loses the one fact `claim` needs to avoid
        #: building a rival worktree: whether the caller was already standing in one.
        self._start = start
        self._where: Path | None = None
        #: The identity whose working tree the caller stands in, when it is not the
        #: caller's own -- see `called_from`. Known once `called_from` has been read.
        self.tree_owner = ""
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
        #: What the caller EXPLICITLY asked for, "" when it asked for nothing. Kept
        #: separate from the resolved id because passing the resolved value back into
        #: `resolve_agent_id` makes it look explicit — which is how `config --explain`
        #: came to report `[explicit]` for an identity nobody had set anywhere.
        self.requested_agent = args.agent or ""
        harness = ""
        if not self.requested_agent and not os.environ.get("DDFLOW_AGENT"):
            # What this agent declared with `ddflow_identify`: its shell is a different
            # process from its MCP connection, and deriving here split one agent's
            # claim and heartbeat across two names (Bfad021e8d9). A declaration, so it
            # rides in `requested_agent`: the api calls re-resolve from that, and
            # leaving it out would split them again.
            from ..infra import harness_identity

            harness = self.requested_agent = harness_identity.declared(self.repo)
        who = ID.resolve(self.repo, self.cfg, self.requested_agent)
        # The layer that actually won, not a guess from comparing values. With nothing set
        # anywhere the derived name differs from `cfg.agent.id` ("") so the old code fired
        # and recorded `env` -- and `config --explain` then blamed the environment for a
        # variable nobody had exported.
        ID.bind(self.cfg, ID.AgentId(who.id, "ddflow_identify" if harness else who.source))
        self.log = EventLog(
            self.repo,
            self.cfg.agent.id,
            lock_timeout_s=self.cfg.lease.acquire_timeout_s,
            log_cfg=self.cfg.log,
        )
        self.store = Store(self.repo, self.cfg)
        self.gates = G.load_gates(self.repo, self.cfg)
        self.json = bool(getattr(args, "json", False))

    @property
    def called_from(self) -> Path:
        """Where the caller stands, for the commands that resolve an item's work from it
        (claim's adoption; gate run/record, merge, review, tests, precommit, heartbeat
        for an item without a tree of its own).

        Unless it is a linked tree ANOTHER identity is working in: a subagent's shell in
        its parent's harness tree is not standing in its own work, and answering from
        there bound its item to the parent's tree, ran the parent's tree as its gate and
        landed the parent's branch as its merge (B11e4c5a185). Then the primary -- where
        the item, not the caller's location, decides. Read lazily: it reads the log.
        """
        if self._where is None:
            from ..services.tree_owner import foreign_tree_owner

            self.tree_owner = foreign_tree_owner(
                self.repo, self._start, self.log.agent_id, self.log.read_all()
            )
            self._where = self.repo if self.tree_owner else self._start
        return self._where

    def state(self):
        return fold(self.log.read_all(), strict=False)

    def out(self, human: str, data: Any = None) -> None:
        if self.json:
            emit_json(_plain(data if data is not None else {"message": human}))
        else:
            print(human)


#: Re-exported. The recursive converter lives in `core/plain.py` — the api layer needs
#: it too and cannot import a surface, and three copies was two too many.
_plain = plain


def _csv(v: str | None) -> list[str]:
    """`config.csv_list` — one parser for the comma-separated notation, not two."""
    from ..config import csv_list

    return csv_list(v)


#: Re-exported, not reimplemented. The `api` layer needs the same generator and may
#: not import a surface to get it, so the function moved down to `core/ids.py` and this
#: name stays for the command modules that already call it.
_auto_id = auto_id


def _wrap(text: str, width: int) -> list[str]:
    """Reflow a config knob's documentation to a column. Here rather than in `cli` because
    `commands/setup.py` renders `config --explain` and may not import the surface it was
    extracted out of."""
    import textwrap

    return textwrap.wrap(" ".join(text.split()), width)


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
