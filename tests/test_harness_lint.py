"""`ddflow harness lint` is clean for shipped descriptors but still nudges a local one
(Bd5961564d8): shipped descriptors leave their later (v2) sections out as the NOT VERIFIED
third state, while a local descriptor being authored must keep [verified].unverified in sync."""

from __future__ import annotations

from pathlib import Path

from ddflow.api import harness_drv as HD
from ddflow.services import harnessreg as HR

LOCAL_DIR = Path(".ddflow") / "local" / "harnesses"

MINIMAL = """id = "acmex"
rank = 1
family = "acmex"
title = "Acme X"
[mcp]
path = ".acmex.json"
shape = "mcpServers"
[verified]
date = "2026-11-11"
sources = ["https://example.com"]
"""


def test_shipped_descriptors_lint_without_left_out_warnings(repo):
    """A shipped descriptor is NOT VERIFIED for its later sections by design; linting it
    must be clean, not warn that their absence is unlisted (Bd5961564d8)."""
    for h in HR.load():
        out = HD.harness_lint(repo, h.id)
        assert not [w for w in out.data["warnings"] if "left out but not listed" in w], h.id


def test_a_local_descriptor_still_warns_on_a_section_left_out(repo):
    """The nudge stays for a descriptor under authoring: a section left out and not listed
    under [verified].unverified is named, unlike a shipped descriptor."""
    (repo / LOCAL_DIR).mkdir(parents=True, exist_ok=True)
    (repo / LOCAL_DIR / "acmex.toml").write_text(MINIMAL, encoding="utf-8")
    out = HD.harness_lint(repo, "acmex")
    assert any("left out but not listed" in w for w in out.data["warnings"])
    assert any("[detect] is left out but not listed" in w for w in out.data["warnings"])
