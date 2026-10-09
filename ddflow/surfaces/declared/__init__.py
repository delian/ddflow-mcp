"""Commands declared once (`registry.Command`): the parser and the MCP tool entry of each
are generated from the declaration (D-unify 4, B-uni-cmd-migrate).

One module per family. The tool modules (`surfaces/tools/`) take a family's entries from
here and the parser modules (`surfaces/parsers/`) register its commands, so neither holds a
second copy of a name, a help text or a type.

The declarations' `call` and `payload` use the helpers in `surfaces/tools/_common.py`,
and the tool modules import the declarations: importing the tool package first, as below,
makes the cycle resolve the same way whichever side a process enters from.
"""

from __future__ import annotations

from .. import tools as _tools  # noqa: F401 -- see the docstring
