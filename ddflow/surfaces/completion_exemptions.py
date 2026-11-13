"""Arguments that deliberately do NOT complete, each with its reason.

`Param.complete` declares the value kind an argument completes (`id:<record kind>`, `enum`,
`path`, ..., or `none` for free text). Defaulting is automatic, so an argument only ever
lands here on purpose: its NAME looks like a record id or a closed value set, yet the right
completion is free text (or no completion). The ratchet in
`tests/test_completion_declare.py` walks the real parser and fails the build when such an
argument resolves to free text WITHOUT an entry here, and requires every entry here to
match a real argument -- so the list may only shrink.

Each entry carries its reason; the test is that a forgotten declaration fails CI instead of
silently not completing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Exemption:
    """One argument of one command path that must not complete.

    ``argument`` is the option string (``--foo``) or the positional ``dest`` it matches.
    ``path`` is the command words to reach it (empty for the root parser).
    """

    path: tuple[str, ...]
    argument: str
    reason: str


#: Arguments whose value must stay free text. Currently empty: every id-like or
#: closed-set argument resolves to a completion kind by inference. A future argument that
#: must not complete adds its entry here with the reason; the ratchet then proves the entry
#: is used (matches a real argument) and everything else still resolves.
EXEMPTIONS: tuple[Exemption, ...] = ()


def exempt(path: tuple[str, ...], argument: str) -> str | None:
    """The reason ``argument`` of ``path`` is exempt from the completion ratchet, or None."""
    for entry in EXEMPTIONS:
        if entry.path == path and entry.argument == argument:
            return entry.reason
    return None
