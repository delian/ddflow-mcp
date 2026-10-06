"""The knob-docs registry every section writes to as it is defined. `ddflow.config`
re-exports `KNOB_DOCS` (the same dict); `config --explain` renders it."""

from __future__ import annotations

# --------------------------------------------------------------------------------------
# Knob documentation lives beside the knob, in this dict, keyed "section.knob".
# `ddflow config --explain` renders it.  A knob with no entry here fails a ratchet test
# (tests/test_config.py::test_every_knob_is_documented), so the docs cannot silently rot.
# --------------------------------------------------------------------------------------
KNOB_DOCS: dict[str, str] = {}


def _doc(section: str, knob: str, text: str) -> None:
    KNOB_DOCS[f"{section}.{knob}"] = text
