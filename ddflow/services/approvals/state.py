"""The state digest each gated verb acts on (RA-cli `request`, the refusal sites).

An approval is of a canonical command whose `state` the SITE computes from the log
(`canon.Canon.state`). A site registers its provider here when it is wired, so
`ddflow approvals request <verb>` (the agent filing a request itself) builds the same canonical
command the refusal site will rebuild when the agent re-runs it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

#: verb -> (state, item, target, args) -> the state digest string the site would compute.
PROVIDERS: dict[str, Callable[[Any, str, str, dict[str, str]], str]] = {}


def register(verb: str, provider: Callable[[Any, str, str, dict[str, str]], str]) -> None:
    PROVIDERS[verb] = provider
