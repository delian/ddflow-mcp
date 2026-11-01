"""Shared imports and constants for the declared `lifecycle` command family.

The command modules of this package declare one area's ``Command``s each; the imports and
constants the single ``declared/lifecycle.py`` held at module level live here so they have
one source, and ``lifecycle/__init__.py`` re-exports the public names (``COMMANDS``,
``BY_TOOL`` and ``DEFAULT_CHECK_RECOVERY``).
"""

from __future__ import annotations

# Each import below is re-exported for the command modules (`navigate`, `gates`, `outcome`)
# to import from the single source, so none is "unused" here.
import argparse  # noqa: F401

from ....core.defaults import DEFAULT_NEXT_KIND, DEFAULT_WAIT_TIMEOUT_S  # noqa: F401
from ....core.model import GATE_OUTCOMES  # noqa: F401
from ....core.outcome import NOTHING, OK  # noqa: F401
from ...argtypes import GLOBS_HELP, _Globs  # noqa: F401
from ...registry import CliPolicy, Command, Param  # noqa: F401
from ...tools._common import MCP_WAIT_DEFAULT_S, MCP_WAIT_MAX_S, _api, _wait_timeout  # noqa: F401
from .. import agent_brief_cli as AB  # noqa: F401
from .. import lifecycle_cli as L  # noqa: F401
from ..mutation_params import MUTATION_FLAG_EXEMPT, MUTATION_ORDER, MUTATION_PARAMS, mutation_of  # noqa: F401

#: `api.lifecycle.DEFAULT_CHECK_RECOVERY`, spelled here so declaring the commands does not
#: import the api (tests/test_uc_surf_loop_brief.py holds the two together).
DEFAULT_CHECK_RECOVERY = True

#: The override of another clone's live lease (B90b43dd2d8) is a person's call at the
#: terminal, not a flag an agent reaches for on reflex (and each flag costs tools/list bytes).
_OVERRIDE = "an operator's override of another clone's live lease, recorded; CLI only (B90b43dd2d8)"
OVERRIDE_EXEMPT = {"--force": _OVERRIDE, "--reason": _OVERRIDE}

_APPROVE_EPILOG = (
    "who: a PERSON only. The command is refused under --agent, DDFLOW_AGENT, a harness marker\n"
    "and MCP; there is no MCP tool for it. An agent files a request instead.\n"
    "what: a recorded `partial` outcome of a required gate that ddflow MEASURED as baseline-red\n"
    "(the failing tests also fail on the base branch). The approval is bound to the digest of\n"
    "that record (outcome, time, reason): a re-run or re-record is a different outcome and\n"
    "needs its own approval. The gate stays `partial`; completion only stops counting it.\n"
    "agent route: re-run with `--approval request` to file the request, a person answers it,\n"
    "then re-run with `--approval ID`. Pending requests: `ddflow approvals list`; a person\n"
    "answers with `ddflow approvals approve ID` (see `ddflow approvals --help`)."
)
