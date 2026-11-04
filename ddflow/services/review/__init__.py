"""Cross-family reviewers — a configured endpoint, not a prompt for the agent to obey.

This package is the former ``ddflow/services/review.py``, split into submodules that
stay under 30k chars each (rule r-small-files). The public API is unchanged: every
name the old module exported is re-exported here, so ``from ddflow.services.review
import review`` and ``from ddflow.services import review as R`` bind exactly what they
bound before.

The re-export is LAZY (``__getattr__``): the package imports no submodule until one
name is asked for. That keeps two invariants a one-file module had and a package must
keep — monkeypatch (``monkeypatch.setattr(R, name, ...)`` re-binds the name on THIS
module exactly as before) and the layering ratchet (``test_no_mutually_importing_pair``
would read an eager ``from .engine import ...`` here as a latent parent<->child cycle).
"""

from __future__ import annotations

from ddflow.core import _reexport

#: Public (and patch-covered private) name -> the submodule that defines it.
_SUBMODULE_OF = {
    # model
    "ERROR": "model",
    "FINDING_DETAIL_KEPT": "model",
    "Finding": "model",
    "LaunchSpec": "model",
    "Layer": "model",
    "PARTIAL": "model",
    "PRESETS": "model",
    "Pool": "model",
    "REVIEWED": "model",
    "ReviewResult": "model",
    "Reviewer": "model",
    "ReviewerDispatch": "model",
    "Route": "model",
    "Selected": "model",
    "UNAVAILABLE": "model",
    "WELL_KNOWN_ENDPOINTS": "model",
    "_SEVERITY_RANK": "model",
    "blocking_findings": "model",
    "classify_exhaustion": "model",
    "degraded_notes": "model",
    "degraded_marks": "model",
    "family_of": "model",
    "finding_digest": "model",
    "load_reviewers": "model",
    "pool_enabled": "model",
    "reviewers_for": "model",
    "route_unrouted": "model",
    # transport
    "_Cancel": "transport",
    "_abort": "transport",
    "_attach_to_running": "transport",
    "_cancel_on_error": "transport",
    "_client": "transport",
    "_running": "transport",
    "detect": "transport",
    "probe_endpoint": "transport",
    # engine
    "DID_NOT_CONVERGE": "engine",
    "TRUNCATED": "engine",
    "_COLD": "engine",
    "_FINDING_RE": "engine",
    "_HTTP_OK_FLOOR": "engine",
    "_HTTP_SERVER_ERROR": "engine",
    "_HUNK_CONTEXT_RE": "engine",
    "_RUNNER": "engine",
    "_STATUS_RE": "engine",
    "_SYSTEM_FALLBACK": "engine",
    "_chat": "engine",
    "_chat_anthropic": "engine",
    "_chat_command": "engine",
    "_chat_gemini": "engine",
    "_chat_openai": "engine",
    "_chunk_budget": "engine",
    "_cut_off": "engine",
    "_no_answer_in_time": "engine",
    "_post": "engine",
    "_post_json": "engine",
    "_temperature": "engine",
    "_tool_moved": "engine",
    "_truncated": "engine",
    "_url_ok": "engine",
    "chunk_files": "engine",
    "diff_fence": "engine",
    "effective_chunk_budget": "engine",
    "ensure_running": "engine",
    "parse": "engine",
    "split_diff": "engine",
    "strip_hunk_context": "engine",
    # runner
    "AUTO_CONCURRENCY_CEILING": "runner",
    "_Progress": "runner",
    "_absorb_chunk": "runner",
    "_bounded": "runner",
    "_concurrency": "runner",
    "_halves": "runner",
    "_in_rerun_cut": "runner",
    "_name_unreviewed": "runner",
    "_preflight": "runner",
    "_race": "runner",
    "_retry_truncated": "runner",
    "_retryable": "runner",
    "_settle": "runner",
    "_waves": "runner",
    "diff_digest": "runner",
    "merge_rerun": "runner",
    "review": "runner",
}

_reexport.reexport(globals(), _SUBMODULE_OF, __name__)
del _reexport
