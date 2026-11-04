"""Cap BLAS/OpenMP threads for a ddflow *process* (B4c7b7e8d5b).

Called by the console-script entry points only, never on ``import ddflow``: a host program
that imports ddflow as a library keeps its own threading. On a many-core host numpy's pool
otherwise burns seconds of CPU per command. A value the user already set is never changed.
"""

import os

VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")


def cap_blas_threads() -> None:
    for var in VARS:
        os.environ.setdefault(var, "1")
