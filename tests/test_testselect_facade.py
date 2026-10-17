"""Beb27076db4: a test that reaches a changed module only through a package
facade is selected when it spells a name the facade re-exports from that module -- and not
otherwise (walking through the whole facade raised selections by up to 50 tests a merge)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpers import git_quiet as _git

from ddflow.services import testselect as T


@pytest.fixture
def faced(repo):
    files = {
        "pkg/__init__.py": "",
        "pkg/api/__init__.py": "from .wait import wait, LIMIT as WAIT_LIMIT\nfrom .claim import claim\n",
        "pkg/api/wait.py": "LIMIT = 1\n\ndef wait():\n    return 1\n",
        "pkg/api/claim.py": "def claim():\n    return 1\n",
        "tests/conftest.py": "",
        "tests/test_alias.py": "from pkg import api as A\n\ndef test_a():\n    assert A.wait()\n",
        "tests/test_from.py": "from pkg.api import WAIT_LIMIT\n\ndef test_b():\n    assert WAIT_LIMIT\n",
        "tests/test_dotted.py": "import pkg.api\n\ndef test_c():\n    assert pkg.api.wait()\n",
        "tests/test_other.py": "from pkg import api as A\n\ndef test_d():\n    assert A.claim()\n",
    }
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "fixture")
    return repo


def test_a_test_spelling_a_reexported_name_is_selected_through_the_facade(faced):
    (faced / "pkg/api/wait.py").write_text("LIMIT = 2\n\ndef wait():\n    return 2\n")
    sel = T.select(faced, "main", hub_importers=0)
    assert sel is not None and sel.full == ""
    got = {t.path for t in sel.tests}
    assert got == {"tests/test_alias.py", "tests/test_from.py", "tests/test_dotted.py"}, got
