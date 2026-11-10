"""Compatibility re-export: the text clipper moved to `ddflow.adapters.textcut`.

The clipper is pure (stdlib alone), so it belongs in the bottom `ddflow.adapters` layer,
which the forge and other adapters import without a `ddflow.core` dependency. This shim
keeps `from ddflow.core.textcut import clip` (and `clip_lines`, `window`, `whole_marks`)
working for the layers above until their import sites read the adapter module directly.
"""

from ddflow.adapters.textcut import *  # noqa: F403  # clip, clip_lines, window, whole_marks, MARK_START
