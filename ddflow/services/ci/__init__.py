"""The CI gate: run the project's pre-push/CI checks on the MERGE RESULT, in a scratch worktree.

Every per-task gate used to run a different command set from the pre-push hook, and nothing
tested a branch together with what main had become, so lint, format, bandit and the other
whole-tree checks first ran at push time, in a batch (decision D-ci-parity). `ddflow ci run`
makes "passed its gates" mean "would pass pre-push": it merges the branch with the base in a
throwaway worktree (a conflict is a failure) and runs the configured command there, which by
default is the project's own pre-push stage: `pre-commit run --hook-stage pre-push`.

This package is the former ``ddflow/services/ci.py``, split into submodules that stay under
30k chars each (rule r-small-files). The public API is unchanged: every name the old module
exported is re-exported here, so ``from ddflow.services.ci import run`` and
``from ddflow.services import ci as CI`` bind exactly what they bound before.

The re-export is LAZY (``__getattr__``): the package imports no submodule until one name is
asked for. That keeps two invariants a one-file module had and a package must keep —
monkeypatch (``monkeypatch.setattr(CI.W, "git", ...)`` re-binds the name on THIS module exactly
as before) and the layering ratchet (``test_no_mutually_importing_pair`` would read an eager
``from .runner import ...`` here as a latent parent<->child cycle).
"""

from __future__ import annotations

from ddflow.core import _reexport

#: Public (and patch-covered private) name -> the submodule that defines it.
_SUBMODULE_OF = {
    # types
    "CANNOT_MERGE": "types",
    "CM": "types",
    "CONFLICT": "types",
    "Check": "types",
    "Failure": "types",
    "NOT_MERGEABLE": "types",
    "Result": "types",
    "failure_text": "types",
    # precommit
    "DEFAULT_COMMAND": "precommit",
    "PRE_COMMIT_CONFIG": "precommit",
    "PRE_PUSH_RUN": "precommit",
    "_FAIL_LANGUAGE": "precommit",
    "_HOOKS_KEY": "precommit",
    "_HOOK_ID": "precommit",
    "_LIST_ITEM": "precommit",
    "_PUSH_STAGES": "precommit",
    "_REPO_START": "precommit",
    "_hook_blocks": "precommit",
    "_runs_at_pre_push": "precommit",
    "_stage_list": "precommit",
    "resolve_command": "precommit",
    "tripwire_ids": "precommit",
    # shell
    "_escaped": "shell",
    "_leading_skip": "shell",
    "_scope_the_run": "shell",
    "_sh_single": "shell",
    "_shell_active": "shell",
    "_sub_end": "shell",
    "executable_missing": "shell",
    "scope_to_diff": "shell",
    "tool_missing": "shell",
    # ruff
    "auto_fix": "ruff",
    "changed_python": "ruff",
    "failing_ruff": "ruff",
    "ruff_bin": "ruff",
    # scratch
    "CC": "scratch",
    "OWNER_SUFFIX": "scratch",
    "SCRATCH_PREFIX": "scratch",
    "W": "scratch",
    "_claim_owner": "scratch",
    "_remove_on_signals": "scratch",
    "_remove_scratch": "scratch",
    "fsio": "scratch",
    "merge_tree": "scratch",
    "owner_line": "scratch",
    "owner_marker": "scratch",
    # runner
    "CS": "runner",
    "CommandRunner": "runner",
    "Config": "runner",
    "Declared": "runner",
    "SL": "runner",
    "TIMEOUT": "runner",
    "WK": "runner",
    "_HOOK_LINE": "runner",
    "_STAGE_NAMES": "runner",
    "_default_on_the_diff": "runner",
    "_execute": "runner",
    "_holder": "runner",
    "_lock_wait": "runner",
    "_merge_base": "runner",
    "_needs_suite_slot": "runner",
    "_result": "runner",
    "_run_locked": "runner",
    "_run_stages": "runner",
    "_runs_whole_suite": "runner",
    "_say_what_ran": "runner",
    "_scope_evidence": "runner",
    "_stale_pins": "runner",
    "_unfinished": "runner",
    "_with_workers": "runner",
    "_worker_log": "runner",
    "child_env": "runner",
    "failure_evidence": "runner",
    "localize_hooks": "runner",
    "parse_checks": "runner",
    "pin_files": "runner",
    "regenerate_pins": "runner",
    "run": "runner",
    "TAIL_CHARS": "runner",
    # main
    "CI_ON_MERGE_MODES": "main",
    "FAST_SKIP": "main",
    "IDS": "main",
    "ON_MERGE_MODES": "main",
    "ascii_slug": "main",
    "bug_id": "main",
    "content_digest": "main",
    "is_filing_of": "main",
    "main_command": "main",
    "refile_id": "main",
}

_reexport.reexport(globals(), _SUBMODULE_OF, __name__)
del _reexport
