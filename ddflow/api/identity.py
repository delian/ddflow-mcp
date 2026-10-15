"""The identity answer the surfaces ask for (D-unify: surfaces reach services through api).

`resolve` is :func:`ddflow.services.identity.resolve`; `bind` records the answer on the
config. `resolve_actor` and `require` are the role and capability answer
(:mod:`ddflow.services.actors`). The CLI's `Ctx` asks here instead of importing the service.
"""

from __future__ import annotations

from ..services.actors import Actor, require, resolve_actor
from ..services.identity import AgentId, bind, resolve

__all__ = ["Actor", "AgentId", "bind", "require", "resolve", "resolve_actor"]
