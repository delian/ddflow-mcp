"""The sources a person or an agent fills: the engine yields nothing for them.

``ask`` is answered by the operator (``doc.fragment_added``), ``draft`` is written by an agent
and reviewed, ``static`` is text that stays as written.
"""

from __future__ import annotations

from .sources import SourceKind, register

for _name in ("ask", "draft", "static"):
    register(SourceKind(_name))
