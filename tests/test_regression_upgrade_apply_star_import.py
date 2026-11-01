"""B-upgrade-apply-star-import: ``from ddflow.services.upgrade_apply import *`` must
export every public name the pre-split module bound, including the lazily re-exported ones.

Star-import reads the module ``__dict__`` when the package has no ``__all__``, so the lazy
``__getattr__``/``__dir__`` installed by ``ddflow/core/_reexport.py`` was invisible to it:
``import *`` dropped every name the split moved into a submodule (``apply``, ``Watch``,
``parse_categories``, ``touched``, the status words). ``_reexport.reexport`` now also
installs ``__all__`` — the package's real public names plus the public keys of its
``_SUBMODULE_OF`` map — so a star-import resolves the lazy names through ``__getattr__``.
"""

from __future__ import annotations

from ddflow.services import upgrade_apply as UA


def _star() -> dict:
    ns: dict = {}
    exec("from ddflow.services.upgrade_apply import *", ns)
    return ns


def test_star_import_exports_the_lazy_reexported_names():
    ns = _star()
    for name in ("apply", "Watch", "parse_categories", "touched"):
        assert name in ns, name
    for name in ("ACKNOWLEDGED", "APPLIED", "BACKUP_MODES", "CONFIG_POLICIES", "FAILED",
                 "REFUSED", "SKIPPED", "UNAVAILABLE"):
        assert name in ns, name
    # The eager re-exports the old module held for its callers stay exported too.
    for name in ("UP", "BACKUPS", "MANIFEST", "SnapshotRefused", "backup_name",
                 "make_backup", "make_snapshot", "prune"):
        assert name in ns, name


def test_all_is_the_public_surface():
    assert sorted(UA.__all__) == list(dict.fromkeys(sorted(UA.__all__))), "unique, sorted"
    for name in ("apply", "Watch", "parse_categories", "touched", "UP", "ACKNOWLEDGED",
                 "SKIPPED"):
        assert name in UA.__all__, name
    assert not any(n.startswith("_") for n in UA.__all__), UA.__all__
