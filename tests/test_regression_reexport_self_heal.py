"""A lazy re-export re-imports after an import halts mid-flight (bug B7e585f9e5d).

``ddflow.core._reexport`` re-exports a split package's public names lazily: ``__getattr__``
imports the submodule that defines a name on first use. When a prior import of that submodule
halted and left ``sys.modules[<pkg>.<sub>] = None`` (``importlib`` re-raises
``ModuleNotFoundError`` with "import of <pkg>.<sub> halted; None in sys.modules" on every
later access instead of re-importing), the lazy name stays broken for the whole process.
``check_after_merge`` hit this through ``ddflow.services.ci.main`` and crashed on every merge.
``__getattr__`` must drop the ``None`` sentinel before retrying so the name resolves.
"""

from __future__ import annotations

import sys

import pytest

from ddflow.core import _reexport


def test_a_lazy_reexport_reimports_after_an_import_halts_on_a_none_entry(tmp_path, monkeypatch):
    (tmp_path / "poisonpkg").mkdir()
    (tmp_path / "poisonpkg" / "__init__.py").write_text("")
    (tmp_path / "poisonpkg" / "sub.py").write_text("MAGIC = 7\n")
    monkeypatch.syspath_prepend(str(tmp_path))

    import poisonpkg

    _reexport.reexport(poisonpkg.__dict__, {"MAGIC": "sub"}, "poisonpkg")

    # A halted import leaves the sentinel importlib later refuses with
    # "import of poisonpkg.sub halted; None in sys.modules". Without the drop-and-retry, the
    # first access that resolves the lazy name would raise ModuleNotFoundError here; the fix
    # drops the sentinel and re-imports the submodule.
    sys.modules["poisonpkg.sub"] = None
    try:
        assert poisonpkg.MAGIC == 7
    finally:
        # The throwaway package outlives its tmp_path; drop its modules so a later import of
        # the same name cannot bind a directory pytest has removed.
        sys.modules.pop("poisonpkg", None)
        sys.modules.pop("poisonpkg.sub", None)


def test_a_real_submodule_import_failure_re_raises_and_runs_its_body_once(tmp_path, monkeypatch):
    (tmp_path / "badpkg").mkdir()
    (tmp_path / "badpkg" / "__init__.py").write_text("")
    (tmp_path / "badpkg" / "runs.py").write_text("RUNS = 0\n")
    (tmp_path / "badpkg" / "sub.py").write_text(
        "import badpkg.runs as R\nR.RUNS += 1\nfrom does_not_exist import NOPE\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    import badpkg
    import badpkg.runs as R

    _reexport.reexport(badpkg.__dict__, {"MAGIC": "sub"}, "badpkg")

    # A REAL failure (the submodule's body imports a missing dependency) must re-raise and
    # must run the body exactly once -- the retry is reserved for the halt sentinel only.
    try:
        with pytest.raises(ModuleNotFoundError):
            badpkg.MAGIC
        assert R.RUNS == 1
    finally:
        sys.modules.pop("badpkg", None)
        sys.modules.pop("badpkg.runs", None)
        sys.modules.pop("badpkg.sub", None)
