"""Compatibility re-export: the Git family moved to `ddflow.adapters.git` (ADP-migrate-homes).

This shim stays until its import sites import the adapter module directly, then it is deleted.
"""

from ddflow.adapters.git import *  # noqa: F403  # the port + GitResult/GitError + run/paths/unmerged + ...
