"""The `[resultcache]` section: reuse of a ci or unit_tests result for the same code tree.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, int_at_least, knob


@declare("resultcache")
@dataclass
class ResultCacheConfig:
    """A content-addressed cache of gate results, per project, under the git-ignored `.ddflow/local`."""

    enabled: bool = knob(
        True,
        doc="Whether a `ci` or `unit_tests` command gate reuses a recorded result for the same code tree, command and environment instead of running again. The key is the tree of tracked files minus `.ddflow/` and `[gates].doc_globs` documents (a dirty tree by its content), the gate, the resolved command, the Python version, the dependency lock files and the variables of `env_vars`; a change to any of them misses. Only `passed` and `failed` are stored, a `failed` hit is reported failed, an entry that does not read back whole is a miss, and the cache is per project, never shared across projects or users. The hit is recorded with the evidence 'cached from run X'. false runs every gate.",
    )
    keep_days: int = knob(
        14,
        doc="Days an entry of the result cache is kept; older entries are removed when the cache is written. At least 1.",
        check=int_at_least(1),
    )
    max_mb: int = knob(
        64,
        doc="Most megabytes the result cache of a project may hold; past it the oldest entries are removed first when the cache is written. At least 1.",
        check=int_at_least(1),
    )
    reuse_failed: bool = knob(
        False,
        doc="Whether a stored `failed` result is served again for the same key (reported failed, never passed). Off by default: a rerun of a failed gate on an unchanged tree is how a flake is told from a real failure (`gate run --rerun-failed`, the flake log), and a served failure would hide it. A `failed` result is stored either way.",
    )
    env_vars: list[str] = knob(
        factory=list,
        doc="Names of environment variables whose values are part of the cache key (a variable that changes what the tests do). Empty by default.",
    )
