"""Configuration — every significant constant is a documented, overridable knob.

Resolution order (last wins):

1. the dataclass defaults in `_core.py`,
2. ``<repo>/.ddflow/config.toml``,
3. environment variables ``DDFLOW_<SECTION>_<KNOB>`` (upper-case, e.g.
   ``DDFLOW_LEASE_TTL_S=900``).

Nothing in ddflow reads a bare literal for a policy decision; if you find one, it is
a bug.  ``ddflow config --explain`` prints every knob with its value, its source and
its docstring, which is the discoverability contract this module exists to keep.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Every [section] dataclass lives in `config_sections/`; each name is re-exported from here.
from ..config_sections._compat import is_source_tree, upgrade_advice  # noqa: F401
from ..config_sections._docs import DECLARED, KNOB_DOCS, RENAMED, declared_tables  # noqa: F401
from ..config_sections._layers import ConfigConflict, layer_path, read_toml  # noqa: F401
from ..config_sections.agent import FAMILY_HINTS, family_for, router_set  # noqa: F401
from ..config_sections.ci import CI_ON_MERGE_MODES, CI_TESTS_SCOPES  # noqa: F401
from ..config_sections.dedupe import DEDUPE_KINDS, DEDUPE_ON_MATCH  # noqa: F401
from ..config_sections.docs import DOCS_LINK_MODES, EXTRACT_KINDS  # noqa: F401
from ..config_sections.export import (  # noqa: F401
    EXPORT_DEFAULT_TARGETS,
    EXPORT_MODES,
    EXPORT_REFRESH_MODES,
)
from ..config_sections.flow import (  # noqa: F401
    FLOW_CLAIMS,
    FLOW_FORGES,
    FLOW_INTEGRATIONS,
    FLOW_MODELS,
    FLOW_ON_CHANGES,
    FLOW_PORT_STRATEGIES,
    FLOW_PR_MERGE,
)
from ..config_sections.ids import (  # noqa: F401
    ID_KINDS,
    ID_PREFIXES,
    ID_TOKENS,
    id_problem,
    id_template_problem,
)
from ..config_sections.mcp import MCP_TOOL_TIERS  # noqa: F401
from ..config_sections.release import RELEASE_LINT_POLICIES  # noqa: F401
from ..config_sections.schedule import (  # noqa: F401
    FLOW_SIGNALS,
    SIGNAL_MARKS,
    SIGNALS,
    _number,
    _signals_problem,
    default_signals,
    merge_signals,
    strictest_signals,
)
from ..config_sections.session import PROGRESS_MODES  # noqa: F401
from ..config_sections.triggers import TRIGGERS_MAX_FIRES_LIMIT  # noqa: F401
from ..config_sections.upgrade import UPGRADE_SKEW_POLICIES  # noqa: F401
from ._coerce import (  # noqa: F401
    _coerce,
    _coerce_dict,
    _coerce_knob,
    _coerce_layer_knob,
    _is_exactly,
    _maybe_json,
    _outer_is_dict,
    csv_list,
)

# The loader machinery, split across submodules so each stays under 30k chars; every
# name is re-exported from here so call sites keep `from ddflow.config import X`.
from ._core import (  # noqa: F401
    _NOT_SECTIONS,
    CONFIG_FORMAT,
    AgentConfig,
    ApprovalsConfig,
    BugsConfig,
    CadenceConfig,
    ChoreConfig,
    CiConfig,
    CompanionsConfig,
    Config,
    DedupeConfig,
    DigestConfig,
    DocsConfig,
    EnforceConfig,
    EvaluationConfig,
    ExportConfig,
    FlowConfig,
    GatesConfig,
    GovernConfig,
    GrantsConfig,
    HealthConfig,
    HooksConfig,
    IdsConfig,
    ImportConfig,
    LanesConfig,
    LeaseConfig,
    LessonsConfig,
    LogConfig,
    LoopsConfig,
    McpConfig,
    MemoryConfig,
    MergeQueueConfig,
    MetricsConfig,
    PinsConfig,
    PlanConfig,
    PromptsConfig,
    RagConfig,
    ReinstructConfig,
    ReleaseConfig,
    ReviewConfig,
    RulesConfig,
    ScheduleConfig,
    SensitivityConfig,
    SessionConfig,
    SwarmConfig,
    TriggersConfig,
    UpgradeConfig,
    WavesConfig,
    WorktreeConfig,
    _merge_budget_table,
    parallel_range_problems,
)
from ._decls import (  # noqa: F401
    _KNOB_CHECKS,
    _MEMBER_FILTERS,
    _STRICT_FALLBACK,
    _TOLERANT_VALUES,
    _VALUE_CHECKS,
    KNOB_CHOICES,
    KNOB_OUTWARD,
    KNOB_STRICTEST,
    KNOB_STRICTEST_NUMBER,
    strictest,
)
from ._errors import InvalidValue  # noqa: F401
from ._tree import _CODE_TREE
from ._warn import _WARNED, _warn_unknown  # noqa: F401


def _is_code_tree(root: Path) -> bool:
    """Is `root` the very tree this code runs from -- not merely a parent of it?

    Equality, not a prefix: a harness tree NESTED under the primary
    (`.claude/worktrees/x`) has the primary as a prefix, and it is exactly the checkout
    whose older code meets the primary's newer config (bug B9cb7dd1c3b).
    """
    try:
        return root.resolve() == _CODE_TREE
    except OSError:
        return False


#: Old keys already warned about in this process (`_migrate_renamed`).
_WARNED_RENAMED: set[str] = set()


def _migrate_renamed(data: dict[str, Any], source: str) -> dict[str, Any]:
    """``data`` with every renamed knob (`RENAMED`, D-compat) under its CURRENT key.

    The old key is still read, with one warning on stderr per process; when both are set the
    new key wins, as the more recent spelling. ``source="check"`` (a write being validated)
    stays quiet: the write path migrates the file itself (`services/configwrite`).
    """
    hits = [
        old
        for old in RENAMED
        if isinstance(sec := data.get(old.partition(".")[0]), dict) and old.partition(".")[2] in sec
    ]
    if not hits:
        return data
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in data.items()}
    for old in hits:
        new, since, removed_in = RENAMED[old]
        osec, _, oknob = old.partition(".")
        nsec, _, nknob = new.partition(".")
        value = out[osec].pop(oknob)
        if not out[osec] and osec not in Config.__dataclass_fields__:
            del out[osec]  # the old section held nothing else: do not call it unknown
        target = out.setdefault(nsec, {})
        if isinstance(target, dict):
            target.setdefault(nknob, value)
        if source != "check" and old not in _WARNED_RENAMED:
            _WARNED_RENAMED.add(old)
            print(
                f"ddflow: warning: config key '{old}' is deprecated since {since}; use "
                f"'{new}' (the old key is read until {removed_in}; the next config write "
                "moves it).",
                file=sys.stderr,
            )
    return out
