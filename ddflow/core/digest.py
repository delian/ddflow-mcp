"""Compatibility re-export: the digest family moved to `ddflow.adapters.digest` (ADP-migrate-homes).

This shim stays until the import-linter baselines are empty, then it is deleted; callers
import `ddflow.adapters.digest` directly. See `ddflow.adapters.digest` for the functions.
"""

from ddflow.adapters.digest import *  # noqa: F401,F403  # re-export the whole family
from ddflow.adapters.digest import (  # noqa: F401  # the historical names, for `import *` and isinstance
    MARKER,
    NOT_FOR_SECURITY,
    REDACTED,
    canonical,
    content_digest,
    hasher,
    normalize_markers,
    normalize_text,
    normalized,
    of_obj,
)
