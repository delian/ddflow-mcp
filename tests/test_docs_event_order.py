"""B186: the documented event order must match Event.sort_key (lamport, agent, id)."""

import inspect
import re
from pathlib import Path

from ddflow.core.events import Event

ROOT = Path(__file__).resolve().parent.parent


def test_architecture_names_the_real_sort_key_fields():
    src = inspect.getsource(Event.sort_key)
    assert "self.lamport, self.agent, self.id" in src
    text = (ROOT / "docs" / "ARCHITECTURE.md").read_text()
    assert "(lamport, agent, id)" in text
    assert not re.search(r"agent_id,\s*event_id", text)


def test_research_does_not_cite_the_deleted_packaging_test():
    tests = (ROOT / "tests" / "test_packaging.py").read_text()
    text = (ROOT / "docs" / "RESEARCH.md").read_text()
    for name in set(re.findall(r"`(test_the_package_\w+)`", text)):
        assert f"def {name}(" in tests, f"docs/RESEARCH.md cites missing test {name}"
