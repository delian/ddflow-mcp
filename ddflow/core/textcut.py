"""Re-export of the text clipper, homed in `ddflow.adapters.textcut`.

The clipper moved out of `core` because `ddflow.adapters.forge` (`_clip`) needs it, and
`adapters` is the BOTTOM layer (below `config`): it may import no other layer, so the
clipper cannot live in `core` and be reached from there. Every layer above `adapters`
keeps importing it from here; the names below are the same objects, re-exported. Move the
implementation, never copy it. See `ddflow.adapters.textcut` for what each function does.

Pure: no I/O.
"""

from __future__ import annotations

from ..adapters.textcut import MARK_START, clip, clip_lines, whole_marks, window

__all__ = ["MARK_START", "clip", "clip_lines", "whole_marks", "window"]
