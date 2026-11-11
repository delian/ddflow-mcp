"""Rules API: persistence, manifest generation, and orchestration.

The operations live one module per area in this package; the public functions and classes
are re-exported, so `from ddflow.api import rules as R; R.rule_add(...)` keeps working.
A REBINDING is not shared: each function reads names from its own module, so to replace a
helper in a test, patch the module that defines it, not this package.
"""

from __future__ import annotations

from ...services.rules import Rule, RulesStorage
from ..rulesinv import rule_inventory
from .add import _add_checked_against_others, _answer_other_kind, _related_to_rule, rule_add  # noqa: F401
from .dedup import (  # noqa: F401
    RuleDedupAnswer,
    _cross_kind,
    _is_rule,
    _other_kind_rows,
    _points_elsewhere,
    rule_dedup_check,
    rule_dedup_check_dry_run,
)
from .mutate import _check_edit, rule_remove, rule_update  # noqa: F401
from .read import rule_get, rule_list, rule_search, rule_sync
from .write import (  # noqa: F401
    DEFAULT_RULE_PRIORITY,
    _extend_rule,
    _over_limits,
    _record_in_log,
    _refused_up_front,
    _retire_in_log,
    _unrecorded,
    apply_rule_update,
    rules_manifest,
)

#: The public operations and constants, as the single module exposed them.
__all__ = [
    "DEFAULT_RULE_PRIORITY",
    "Rule",
    "RuleDedupAnswer",
    "RulesStorage",
    "apply_rule_update",
    "rule_add",
    "rule_dedup_check",
    "rule_dedup_check_dry_run",
    "rule_get",
    "rule_inventory",
    "rule_list",
    "rule_remove",
    "rule_search",
    "rule_sync",
    "rule_update",
    "rules_manifest",
]
