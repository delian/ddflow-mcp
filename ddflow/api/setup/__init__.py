"""Adoption and configuration: companions, config, prompts, hooks, help, setup.

The last family off the argv path (B97). Everything here is operator-facing one-shot work
rather than queue policy, which is why it went last — but two of the refusals are as
load-bearing as anything in the pipeline:

* **Registering a companion that is not installed** writes a launch command that fails
  mid-task, as an agent reaches for the tool a gate told it to use. And "not installed"
  is kept distinct from "could not tell": sending the first to an operator whose probe
  merely TIMED OUT makes them install something they already have.
* **A config append is validated against the MERGED text**, not against what is already on
  disk. Validating the state you are replacing has checked nothing — an unknown section
  went in and every later command failed to load the file.
"""

# The operations live one module per area in this package (r-small-files); the public
# functions and classes are re-exported, so
# `from ddflow.api.setup import setup` and `ddflow.api.setup.A.setup(...)` (where A is the
# module re-exported as `ddflow.api.setup`) keep working. A REBINDING is not shared: each
# function reads names from its own module, so to replace a helper or a constant in a
# test, patch the module that defines it (`setup.session.SESSION_START_HEADER`,
# `setup.adopt._scan`), not this package. (Nothing patched the single module's names when
# it was split.)

from __future__ import annotations

from .companions import (  # noqa: F401
    Registration,
    _add_install_state,
    _add_refusal,
    _add_stopped,
    _scan,
    _verify_outcome,
    _verify_skipped,
    companions,
    companions_add,
    companions_verify,
)
from .config import (  # noqa: F401
    ConfigEdit,
    _BUDGET_KNOBS,
    _budget_keys,
    _default_layer,
    _extra_keys,
    _said,
    configure,
    report_budget_change,
)
from .session import (  # noqa: F401
    SESSION_START_FOOTER,
    SESSION_START_HEADER,
    _START_LOCK,
    _START_REPORTS,
    _after_compaction,
    _check_msg,
    _companion_lines,
    _record_compaction,
    _session_start,
    _sync_sections,
    _worktree_drift,
    release_proposal,
    stale_server_facts,
    stale_server_note,
    upgrade_notice,
    upgrade_start,
)
from .hooks import (  # noqa: F401
    _HOOK_HANDLERS,
    _HookResult,
    _agent_hooks,
    _agent_hooks_outcome,
    _capture_prompt,
    _harness_hooks,
    _hook_line,
    _hook_remedy,
    _hooks_status,
    _installed_hooks,
    _on_nothing,
    _on_pre_compact,
    _on_pre_tool,
    _on_prompt,
    _on_session_start,
    _precompact_line,
    _pretool_line,
    _prompt_core,
    _prompt_hook_line,
    _run_hook,
    _state_line,
    _synth_stdin,
    hooks,
)
from .upgrade import (  # noqa: F401
    UPGRADE_APPLY_PAYLOAD,
    UPGRADE_CHECK_PAYLOAD,
    UPGRADE_HISTORY_PAYLOAD,
    UPGRADE_PAYLOAD,
    UPGRADE_RESTORE_PAYLOAD,
    UPGRADE_SELF_PAYLOAD,
    UPGRADE_STATUS_PAYLOAD,
    SelfUpgrade,
    _APPLIED_FIELDS,
    _apply_reason,
    _other_modes,
    _snapshot_conflict,
    _status_reason,
    _upgrade_alone,
    _upgrade_apply,
    _upgrade_check,
    _upgrade_history,
    _upgrade_plan,
    _upgrade_restore,
    _upgrade_resume,
    _upgrade_self,
    _upgrade_status,
    upgrade,
)
from .prompts import _prompts_get, prompts  # noqa: F401
from .adopt import (  # noqa: F401
    Adoption,
    files_tree,
    help_topic,
    init_project,
    setup,
)

#: The public API, exactly what the single module exposed before it was split.
__all__ = [
    "Adoption",
    "ConfigEdit",
    "Registration",
    "SESSION_START_FOOTER",
    "SESSION_START_HEADER",
    "SelfUpgrade",
    "UPGRADE_APPLY_PAYLOAD",
    "UPGRADE_CHECK_PAYLOAD",
    "UPGRADE_HISTORY_PAYLOAD",
    "UPGRADE_PAYLOAD",
    "UPGRADE_RESTORE_PAYLOAD",
    "UPGRADE_SELF_PAYLOAD",
    "UPGRADE_STATUS_PAYLOAD",
    "companions",
    "companions_add",
    "companions_verify",
    "configure",
    "files_tree",
    "help_topic",
    "hooks",
    "init_project",
    "prompts",
    "release_proposal",
    "report_budget_change",
    "setup",
    "stale_server_facts",
    "stale_server_note",
    "upgrade",
    "upgrade_notice",
    "upgrade_start",
]
