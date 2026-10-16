"""Root conftest: loads the plugin that keeps the suite off the real repository.

``tests/_isolation.py`` must run before any test module (and in every xdist worker), and a
``pytest_plugins`` declaration is only allowed in a root conftest.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))

pytest_plugins = ["_isolation"]
