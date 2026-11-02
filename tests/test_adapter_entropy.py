"""The `Entropy` port: the default adapter's behaviour and the fake's (CONFORMANCE)."""

from __future__ import annotations

import re
import uuid

from ddflow.adapters import entropy as N
from ddflow.adapters.entropy import Entropy
from ddflow.adapters.fakes import FAKES
from ddflow.adapters.fakes.entropy import FakeEntropy


def entropy_conformance(e: Entropy) -> None:
    t = e.token_hex(4)
    assert re.fullmatch(r"[0-9a-f]{8}", t) and len(e.token_hex()) == 16
    u = uuid.UUID(e.uuid4())
    assert u.version == 4
    assert all(1.5 <= e.uniform(1.5, 2.5) <= 2.5 for _ in range(20))
    assert e.token_hex(8) != e.token_hex(8)


CONFORMANCE = {
    "Entropy": {"real": lambda monkeypatch: N.SystemEntropy(), "cases": entropy_conformance}
}


def test_the_adapter_and_the_fake_satisfy_the_port():
    assert isinstance(N.SystemEntropy(), Entropy) and isinstance(FakeEntropy(), Entropy)
    assert FAKES["Entropy"] is FakeEntropy
    assert N.default_entropy() is N.DEFAULT


def test_the_fake_is_reproducible_per_seed():
    a, b, c = FakeEntropy(1), FakeEntropy(1), FakeEntropy(2)
    draw = lambda e: (e.token_hex(), e.uuid4(), e.uniform(0, 1))  # noqa: E731
    assert draw(a) == draw(b) != draw(c)
