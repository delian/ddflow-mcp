"""The identity answer the surfaces ask for (D-unify: surfaces reach services through api).

`resolve` is :func:`ddflow.services.identity.resolve`; `bind` records the answer on the
config. The CLI's `Ctx` asks here instead of importing the service.
"""

from __future__ import annotations

from ..services.identity import AgentId, bind, resolve

__all__ = ["AgentId", "bind", "resolve"]
