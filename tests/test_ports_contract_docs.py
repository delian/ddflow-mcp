"""The docs half of the adapter model, machine-checked.

`ddflow/adapters/ports.py`, `docs/ddflow/adapters/README.md` and the r-adapters rule all say a
family is its `ddflow/adapters/<family>.py` port + `fakes/<family>.py` + the conformance cases
+ `docs/ddflow/adapters/<family>.md`. The code half has the contract test
(tests/test_ports_contract.py); this file is the check that the docs page does not drift from
it, the way the `docs/ARCHITECTURE.md` listing decayed into silence before ADP-modular-registry.
"""

from __future__ import annotations

from pathlib import Path

import ddflow.adapters as A
from ddflow.adapters import ports as P

PKG = Path(__file__).resolve().parents[1] / "ddflow"
DOCS = PKG.parent / "docs" / "ddflow" / "adapters"


def _family(cls: type) -> str:
    return cls.__module__.rsplit(".", 1)[1]


def test_every_port_family_has_a_docs_page():
    """Every discovered `@port` family ships `docs/ddflow/adapters/<family>.md`, so a port
    is never added without a page that says what the port, the default adapter and the fake
    do. Discovery is automatic, so no list of pages exists anywhere."""
    ports = P.discover(A)
    pages = {p.stem for p in DOCS.glob("*.md")}
    missing = sorted({_family(cls) for cls in ports.values()} - pages)
    assert not missing, (
        f"a port family without docs/ddflow/adapters/<family>.md: {missing}. "
        "Add the page (see docs/ddflow/adapters/README.md); do not list anyone here."
    )
