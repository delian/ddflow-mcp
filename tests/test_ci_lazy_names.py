"""Bug B663d174cba: ``failure_text`` named both a lazy re-export (flakes.failure_text, a function)
and the submodule ``ddflow.services.ci.failure_text``, so ``from ddflow.services.ci import
failure_text`` returned the function or the module depending on what was imported first."""

from __future__ import annotations

import subprocess
import sys

CODE = """
import types
{pre}
from ddflow.services.ci import failure_text
print(isinstance(failure_text, types.ModuleType))
"""


def _is_module(pre: str) -> str:
    out = subprocess.run(
        [sys.executable, "-I", "-c", CODE.format(pre=pre)], capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_the_name_is_the_submodule_whether_or_not_it_was_imported_first():
    assert _is_module("") == "True"
    assert _is_module("import ddflow.services.ci.failure_text") == "True"


def test_no_lazy_name_shadows_a_submodule_of_the_package():
    import pkgutil

    import ddflow.services.ci as CI

    subs = {m.name for m in pkgutil.iter_modules(CI.__path__)}
    assert not subs & set(CI._SUBMODULE_OF), subs & set(CI._SUBMODULE_OF)
