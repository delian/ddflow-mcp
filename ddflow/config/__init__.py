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

# Every [section] dataclass lives in `config_sections/`; each name is re-exported from here.
from ..config_sections._compat import is_source_tree, upgrade_advice  # noqa: F401
from ..config_sections._docs import (  # noqa: F401
    DECLARED,
    KNOB_DOCS,
    RENAMED,
    SECTION_CLASSES,
    declared_tables,
)
from ..config_sections._layers import ConfigConflict, layer_path, read_toml  # noqa: F401
from ..config_sections.agent import FAMILY_HINTS, family_for, router_set  # noqa: F401
from ..config_sections.ci import CI_ON_MERGE_MODES, CI_TESTS_SCOPES  # noqa: F401
from ..config_sections.dedupe import DEDUPE_KINDS, DEDUPE_ON_MATCH  # noqa: F401
from ..config_sections.docs import DOCS_LINK_MODES, EXTRACT_KINDS  # noqa: F401
from ..config_sections.export import (  # noqa: F401
    EXPORT_DEFAULT_TARGETS,
    EXPORT_MODES,
    EXPORT_REFRESH_MODES,
    EXPORT_TABLE_KEYS,
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
from ._compat import _WARNED_RENAMED, _migrate_renamed  # noqa: F401

# The loader machinery, split across submodules so each stays under 30k chars; every
# name is re-exported from here so call sites keep `from ddflow.config import X`. The
# `*Config` section classes are re-exported lazily through `__getattr__` below (not a
# hand-kept list) -- `declare()` owns the name (B-config-section-needs-hub-edit).
from ._core import (  # noqa: F401
    _NOT_SECTIONS,
    CONFIG_FORMAT,
    Config,
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
    _one_of,
    strictest,
)
from ._errors import InvalidValue  # noqa: F401
from ._tree import _CODE_TREE, _is_code_tree  # noqa: F401
from ._warn import _WARNED, _warn_unknown  # noqa: F401

#: `ddflow.config.<Section>Config` names, by class name, so the re-export is a lookup on
#: the registry `declare()` populated -- not a hand-kept list that a new section must edit
#: (r-extension-contract, B-config-section-needs-hub-edit).
_SECTION_CLASS_BY_NAME: dict[str, type] = {cls.__name__: cls for cls in SECTION_CLASSES.values()}


def __getattr__(name: str):
    cls = _SECTION_CLASS_BY_NAME.get(name)
    if cls is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return cls


#: A star-import reads the module `__dict__` or `__all__`, never `__getattr__`: name every
#: public name (real globals plus the section classes) so `from ddflow.config import *`
#: binds the section classes, as the eager re-export it replaced did (review finding).
#: PLE0605: dynamic by design -- the section classes exist only after every module's
#: `declare()` ran, so no literal can name them; a literal here is the hand-kept list
#: B-config-section-needs-hub-edit removed (r-extension-contract).
__all__ = sorted({n for n in globals() if not n.startswith("_")} | set(_SECTION_CLASS_BY_NAME))  # noqa: PLE0605
