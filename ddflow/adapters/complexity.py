"""The `Complexity` port and its default adapter: a function's cyclomatic complexity from radon.

The ONE home for radon (the ``extras-one-adapter`` contract): nothing else imports
`radon.complexity`, and ``core``/``services`` read through this module. radon is a dev
dependency; without it nothing is said (and nothing is claimed clean). The commit-hook note
in ``ddflow/services/complexity_budget.py`` re-exports ``complexities`` from here.
"""

from __future__ import annotations

import io
import tokenize
from typing import Protocol, runtime_checkable

try:  # a dev dependency: absent, the check says nothing
    from radon.complexity import cc_visit
except ImportError:  # pragma: no cover - exercised by monkeypatching `cc_visit` to None
    cc_visit = None

from .ports import port

__all__ = ["DEFAULT", "Complexity", "RadonComplexity", "complexities"]


@port
@runtime_checkable
class Complexity(Protocol):
    """Cyclomatic complexity. Home: `ddflow.adapters.complexity` (`RadonComplexity`); fake `FakeComplexity`."""

    def complexities(self, source: bytes | None) -> dict[str, tuple[int, int]]:
        """``{qualified function name: (line, complexity)}``; empty when radon is missing or the
        source does not parse."""


class RadonComplexity:
    """The default adapter: radon's `cc_visit`, tokenized with PEP 263. Stateless."""

    def complexities(self, source: bytes | None) -> dict[str, tuple[int, int]]:
        if source is None or cc_visit is None:
            return {}
        try:
            encoding = tokenize.detect_encoding(io.BytesIO(source).readline)[0]  # PEP 263
            blocks = cc_visit(source.decode(encoding, errors="replace"))
        except (SyntaxError, ValueError, LookupError):
            return {}
        found: dict[str, tuple[int, int]] = {}
        # cc_visit lists a class block whose methods are also listed on their own: functions only.
        for b in sorted((b for b in blocks if not hasattr(b, "methods")), key=lambda b: b.lineno):
            # a name defined twice (under `if`/`else`) is two functions: `f` and `f#2`, by order
            key, n = b.fullname, 1
            while key in found:
                n += 1
                key = f"{b.fullname}#{n}"
            found[key] = (b.lineno, b.complexity)
        return found


#: The one stateless instance callers default to; a test passes `FakeComplexity` instead.
DEFAULT = RadonComplexity()


def complexities(source: bytes | None) -> dict[str, tuple[int, int]]:
    """``{qualified function name: (line, complexity)}`` from the default adapter."""
    return DEFAULT.complexities(source)
