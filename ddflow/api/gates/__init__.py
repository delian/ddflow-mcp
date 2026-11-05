"""The gate pipeline: ask what is next, run it, record it, skip it, prove it can fail.

This family was `ddflow/api/gates.py` and is now a package (r-small-files): the module
was split into ``_timeout.py``, ``_tree.py``, ``_query.py``, ``_run.py`` and ``_vet.py``,
each under 30k chars, with the public API re-exported here unchanged.

Two rules are worth reading before changing anything:

* **Order is enforced but not by default.** ``gates.enforce_order`` is ``warn``, and a
  recording out of order appends a ``gate.out_of_order`` event either way. Reported rather
  than refused because some interleaving is legitimate, and a tool that blocks on every
  harmless reordering gets ``--force``d on reflex. The event exists so that whether
  ``warn`` should become ``block`` has evidence behind it.
* **A human gate is a REFUSAL (3), never a failure (1).** Nothing went wrong; the caller
  is simply not the party who can clear it. An agent branching on exit codes needs to
  tell "this is broken" from "this is not yours to do".

Name RE-EXPORT is lazy (``__getattr__`` resolves each public or patch-covered private
name from ``_SUBMODULE_OF`` to its defining submodule, so an eager ``from .sub import
name`` here stays absent and does not read as a latent parent<->child cycle in
``test_no_mutually_importing_pair``). The submodules themselves are imported EAGERLY so
``gates._query`` (and the other siblings) stay real module attributes with monkeypatchable
globals — ``test_refuted_gate_flag`` patches ``gates._query._load`` directly.
``monkeypatch.setattr(gates, name, ...)`` rebinding a re-exported name shims the attribute
on THIS package for callers that reach the name through the package; a name used
INTERNALLY within a submodule must be patched at its defining submodule
(``gates._run._covered_by_ci``, ``gates._query._load``), because that function resolves
its own module global, not the package attribute.
"""

from __future__ import annotations

import importlib
from typing import Any

# Eager so the sibling submodules stay REAL module attributes (``gates._query`` is
# monkeypatched directly); the redundant aliases mark them as intentional re-exports.
from . import _query as _query
from . import _run as _run
from . import _timeout as _timeout
from . import _tree as _tree
from . import _vet as _vet

#: Public (and patch-covered private) name -> the submodule that defines it.
_SUBMODULE_OF = {
    # query
    "list_gates": "_query",
    "status": "_query",
    "sweep_dead_runs": "_query",
    "verify": "_query",
    "_recorded_since": "_query",
    "_resolve": "_query",
    "_list_overrides": "_query",
    "_order_gate": "_query",
    "_load": "_query",
    # run
    "OUTCOME_EXIT": "_run",
    "record": "_run",
    "run": "_run",
    "_note_logged": "_run",
    "_lease_keeper": "_run",
    "_superseded_run": "_run",
    "_mark_or_refuse_twin": "_run",
    "_account_flakes": "_run",
    "_failure_reason": "_run",
    "_refuse_repeated_failure": "_run",
    "_fail_fast": "_run",
    "_flakes_after_pass": "_run",
    "_rerun_failed_tests": "_run",
    "_guarded": "_run",
    "_execute": "_run",
    "_scoped": "_run",
    "_covered_by_ci": "_run",
    "TSC": "_run",
    # timeout
    "_CI_VERB": "_timeout",
    "_CI_WORDS": "_timeout",
    "_quotes_balanced": "_timeout",
    "_ci_command_with_timeout": "_timeout",
    "_with_run_timeout": "_timeout",
    # tree
    "item_tree": "_tree",
    "_at_merged_tree": "_tree",
    "_where_to_run": "_tree",
    "_no_tree_reason": "_tree",
    # vet
    "Evidence": "_vet",
    "_Vetted": "_vet",
    "_has_research_note": "_vet",
    "_implement_without_commits": "_vet",
    "_vet_claims": "_vet",
    "_author_model_on_reviewer_gate": "_vet",
    "_family_of_name_or_value": "_vet",
    "_reviewed_sha_check": "_vet",
    "_roborev_reviewer": "_vet",
    "_docs_gate_export": "_vet",
    "_evidence_fields": "_vet",
    "_failed_review_unanswered": "_vet",
    "_not_gated_skip_note": "_vet",
    "_override_gate": "_vet",
    "_agent_skip_refusal": "_vet",
}


def __getattr__(name: str) -> Any:
    sub = _SUBMODULE_OF.get(name)
    if sub is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(f"{__name__}.{sub}"), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_SUBMODULE_OF))


__all__ = [
    "OUTCOME_EXIT",
    "Evidence",
    "item_tree",
    "list_gates",
    "record",
    "run",
    "status",
    "sweep_dead_runs",
    "verify",
]
