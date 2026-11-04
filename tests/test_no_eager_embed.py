"""B4c7b7e8d5b: the CLI import must not pull numpy/model2vec (8 s of CPU on a many-core host)."""

import os
import subprocess
import sys

_PROBE = (
    "import sys, os; from ddflow.surfaces import cli; "
    "import ddflow.api.reporting.health; "
    "print([m for m in ('numpy','model2vec') if m in sys.modules]); "
    "print(os.environ.get('OPENBLAS_NUM_THREADS'))"
)


def _run(fake_dir=None, **env):
    e = {k: v for k, v in os.environ.items() if not k.endswith("_NUM_THREADS")}
    e.update(env)
    if fake_dir:
        e["PYTHONPATH"] = str(fake_dir)
    out = subprocess.run([sys.executable, "-c", _PROBE], env=e, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout.split("\n")


def test_cli_import_does_not_load_numpy_or_model2vec(tmp_path):
    # A stand-in model2vec that imports numpy, so the test bites whether or not the real
    # [rag] extra is installed (an eager import of it would pull numpy in).
    (tmp_path / "model2vec.py").write_text("import numpy\nclass StaticModel: ...\n")
    assert _run(tmp_path)[0] == "[]"


def test_blas_threads_capped_unless_the_user_set_them():
    assert _run()[1] == "1"
    assert _run(OPENBLAS_NUM_THREADS="4")[1] == "4"
