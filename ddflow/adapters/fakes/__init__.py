"""Shipped fakes: one `Fake<Port>` per port in `ddflow.adapters.ports` (D-adapters-answers-2).

`FAKES` maps a port's name to its fake class; `tests/test_ports_contract.py` requires every
port to be here (a baseline of the ports still without one only shrinks) and runs the same
conformance cases against the default adapter and the fake. Fakes are shipped, not test-only,
so a downstream user can build against a port without the real dependency.
"""

from __future__ import annotations

import json
import zlib
from typing import Any

from ..registry import Registry

FAKES: Registry[type] = Registry("fake", key=lambda cls: cls.__name__.removeprefix("Fake"))


def _fake(cls: type) -> type:
    """Register a `Fake<Port>` class under the port's name."""
    return FAKES.register(cls)


@_fake
class FakeDigest:
    """A deterministic in-memory `Digest`: no `hashlib`, a CRC chain rendered as hex.

    Same inputs give the same value, different inputs differ, `length` cuts the hex, text
    and its UTF-8 bytes agree, and `canonical` sorts keys -- the properties a caller relies
    on, not the algorithm's actual bytes.
    """

    WIDTH = 64  # hex characters of a full digest; a knob for a test that wants shorter

    def content_digest(
        self,
        data: str | bytes | memoryview,
        algo: str = "sha256",
        *,
        length: int | None = None,
        errors: str = "strict",
        size: int | None = None,
    ) -> str:
        raw = data.encode("utf-8", errors) if isinstance(data, str) else bytes(data)
        out, crc = "", zlib.crc32(algo.encode())
        while len(out) < self.WIDTH:
            crc = zlib.crc32(raw, crc)
            out += f"{crc:08x}"
        out = out[: (size * 2) if size is not None else self.WIDTH]
        return out[:length] if length is not None else out

    def canonical(self, obj: Any) -> str:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)

    def of_obj(
        self, obj: Any, algo: str = "blake2b", *, size: int | None = 12, length: int | None = None
    ) -> str:
        return self.content_digest(self.canonical(obj), algo, size=size, length=length)
