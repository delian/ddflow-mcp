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


def _run(argv, fake_dir=None, **env):
    e = {k: v for k, v in os.environ.items() if not k.endswith("_NUM_THREADS")}
    e.update(env)
    if fake_dir:
        e["PYTHONPATH"] = str(fake_dir)
    out = subprocess.run([sys.executable, *argv], env=e, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout.split("\n")


def test_cli_import_does_not_load_numpy_or_model2vec(tmp_path):
    # A stand-in model2vec importable without numpy, so the test bites whether or not the real
    # [rag] extra is installed: an eager import of it shows up in sys.modules.
    (tmp_path / "model2vec.py").write_text("class StaticModel: ...\n")
    assert _run(["-c", _PROBE], tmp_path)[0] == "[]"


def test_cli_main_caps_blas_threads_unless_the_user_set_them():
    probe = (
        "import os; from ddflow.infra import threads; print(os.environ.get('OPENBLAS_NUM_THREADS')); "
        "threads.cap_blas_threads(); print(os.environ['OPENBLAS_NUM_THREADS'])"
    )
    assert _run(["-c", probe])[:2] == ["None", "1"]  # a bare `import ddflow` changes nothing
    assert _run(["-c", probe], OPENBLAS_NUM_THREADS="4")[1] == "4"
    # the real entry point wires the cap (a removed call in cli.main fails here)
    main = (
        "import os, sys; from ddflow.surfaces import cli; "
        "import atexit; atexit.register(lambda: print(os.environ.get('OPENBLAS_NUM_THREADS'))); "
        "cli.main(['--version'])"
    )
    assert _run(["-c", main])[1] == "1"
    assert _run(["-c", main], OPENBLAS_NUM_THREADS="4")[1] == "4"


def test_extra_installed_reports_a_valid_extra_as_installed(tmp_path):
    (tmp_path / "model2vec.py").write_text("class StaticModel: ...\n")
    probe = "from ddflow.services import embed; print(embed.extra_installed())"
    assert _run(["-c", probe], tmp_path)[0] == "True"


def test_extra_installed_reports_a_broken_install_as_not_installed(tmp_path):
    # "findable" is not "installed": the probe finds the package, but the import itself fails.
    (tmp_path / "model2vec.py").write_text("import this_module_does_not_exist\n")
    probe = "from ddflow.services import embed; print(embed.extra_installed())"
    assert _run(["-c", probe], tmp_path)[0] == "False"


def test_cap_blas_threads_writes_through_the_env_port():
    from ddflow.adapters.fakes.env import FakeEnv
    from ddflow.infra import threads

    env = FakeEnv()
    threads.cap_blas_threads(env)
    assert env.get("OMP_NUM_THREADS") == "1"
    assert env.get("OPENBLAS_NUM_THREADS") == "1"
    assert env.get("MKL_NUM_THREADS") == "1"
    env.set("OPENBLAS_NUM_THREADS", "4")  # a value the user already set is never changed
    threads.cap_blas_threads(env)
    assert env.get("OPENBLAS_NUM_THREADS") == "4"
