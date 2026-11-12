"""The state digest each gated verb acts on (RA-cli `request`, the refusal sites).

An approval is of a canonical command whose `state` the SITE computes from the log
(`canon.Canon.state`). A site registers its provider here when it is wired, so
`ddflow approvals request <verb>` (the agent filing a request itself) builds the same canonical
command the refusal site will rebuild when the agent re-runs it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...core.registry import Registry

#: verb -> (state, item, target, args) -> the state digest string the site would compute.
PROVIDERS: Registry[Callable[[Any, str, str, dict[str, str]], str]] = Registry(
    "approval state provider", replace=True
)


def register(verb: str, provider: Callable[[Any, str, str, dict[str, str]], str]) -> None:
    PROVIDERS.register(provider, verb)


def _approve_partial(st: Any, item: str, target: str, args: dict[str, str]) -> str:
    """`approve.partial` (a person accepting a baseline-red partial): the provider lives with the
    acceptance (`gate_accept.provider`); imported here on first use, which keeps this module
    free of the approvals-consume cycle."""
    from .. import gate_accept

    return gate_accept.provider(st, item, target, args)


register("approve.partial", _approve_partial)
