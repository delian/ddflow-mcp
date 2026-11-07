"""The process-entry BLAS/OpenMP thread cap, re-exported through the API layer.

The cap itself lives in `ddflow.infra.threads` (its one home). Surfaces reach infra only
through `ddflow.api` (the `surfaces-through-api` contract), so the console-script entry
points call `ddflow.api.cap_blas_threads` rather than import `ddflow.infra.threads`
directly.
"""

from __future__ import annotations

from ..infra.threads import cap_blas_threads

__all__ = ["cap_blas_threads"]
