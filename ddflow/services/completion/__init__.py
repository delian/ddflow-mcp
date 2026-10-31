"""Whether an item may be completed (`rules.py`) and shell completion (`tree.py`).

`services.completion` was one module, the completion rule-set; it became a package so the
shell-completion modules can sit beside it. Its public names are still reached here --
``from ..services.completion import verdict`` -- through a lazy ``__getattr__``, so that
importing ``completion.tree`` (run on every TAB) does not import the rule-set and the
config, core and infra layers it pulls in.
"""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    if name.startswith("__"):
        raise AttributeError(name)
    from importlib import import_module

    _rules = import_module(f"{__name__}.rules")

    try:
        return getattr(_rules, name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
