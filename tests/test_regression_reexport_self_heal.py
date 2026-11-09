"""A lazy re-export recovers after its submodule import fails once (bug B7e585f9e5d).

``ddflow.core._reexport`` re-exports a split package's public names lazily: ``__getattr__``
imports the submodule that defines a name on first use. A failed import of that submodule
(a transient mid-import sibling cycle during the package-split / home-migration churn) made
``importlib`` poison ``sys.modules`` with ``submodule_name: None``. From then on every access
re-raised ``ModuleNotFoundError: No module named '<pkg>.<sub>'`` without re-importing, so
``check_after_merge`` crashed on every merge (``ddflow.services.ci.main`` was the reported
name). The lazy ``__getattr__`` must drop the poison before retrying so a transient failure
self-heals instead of becoming permanent.
"""

from __future__ import annotations

import sys

import pytest

from ddflow.core import _reexport


def test_a_lazy_reexport_resolves_after_a_transient_submodule_import_failure(
    tmp_path, monkeypatch
):
    (tmp_path / "poisonpkg").mkdir()
    (tmp_path / "poisonpkg" / "__init__.py").write_text("")
    dep = tmp_path / "poisondep.py"
    dep.write_text("VALUE = 7\n")
    (tmp_path / "poisonpkg" / "sub.py").write_text(
        "from poisondep import VALUE\nMAGIC = VALUE\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    import poisonpkg  # noqa: PLC0415

    _reexport.reexport(poisonpkg.__dict__, {"MAGIC": "sub"}, "poisonpkg")

    # First access fails: the submodule's own import cannot see its sibling yet. A failed
    # import leaves ``sys.modules["poisonpkg.sub"] = None`` behind.
    dep.unlink()
    with pytest.raises(ModuleNotFoundError):
        getattr(poisonpkg, "MAGIC")
    assert sys.modules.get("poisonpkg.sub") is None

    # The transient cause is gone: the retry must re-import and resolve the lazy name, not
    # re-raise ``ModuleNotFoundError`` against the poisoned entry (B7e585f9e5d).
    dep.write_text("VALUE = 7\n")
    assert poisonpkg.MAGIC == 7
