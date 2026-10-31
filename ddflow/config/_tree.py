"""Where this code runs from."""

from __future__ import annotations

from pathlib import Path

#: The checkout this code was imported from: `<tree>/ddflow/config/_tree.py` -> `<tree>`.
#: For an installed ddflow it is site-packages, which is no project's root. The same
#: answer as `infra.paths.package_parent()`, recomputed because `config` is the bottom
#: layer and may import nothing (tests/test_layering.py); the module sits directly under
#: `ddflow/config/`, so `parents[2]` reaches `<tree>` and the level count cannot drift the
#: way the layered modules' did.
_CODE_TREE = Path(__file__).resolve().parents[2]
