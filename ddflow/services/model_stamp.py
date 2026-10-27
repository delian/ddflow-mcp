"""Authorship provenance for agent-authored event records (bug Bb54029aee9).

Reviewed and gated records written by agents (``review.triaged`` probe text,
``gate.passed`` evidence notes, and the other agent-authored kinds) used to carry only
the agent id, so drift (leaked script words, fabricated references) could not be
attributed to a SERVED MODEL or a CONTEXT FILL. This module computes the three fields
the design asks each agent-authored event to carry next to ``agent``
(docs/ddflow/research/hallucination-drift.md, HAL-model-facts — the writer WIRING of
these fields into the event kinds is the follow-up whose globs cover the writers; this
module is the stamping half):

* ``served_model`` -- the served model id the harness reports under a model env name;
* ``harness``      -- the claiming harness id, detected from its session variables;
* ``ctx_tokens``   -- the current context fill in tokens over ddflow's own reporting
  channel; no harness env name for a context fill is verified yet, so none is read.

**Absent = "unknown", never guessed** (the bug's rule): only the declared channel names
below are read; an env variable that is not a declared channel is ignored; an agent id
is never mapped to a model; two different reports of the served model refuse to guess
and read as ``unknown``.

The channels are env-variable NAMES of the harness->ddflow reporting contract, the same
class as ``harnessreg.LEGACY_SESSION_ENV`` (a module tuple, by the rule
r-everything-configurable's structural-constant exemption). The values a person wants
to change lift to ``[stamp]`` knobs when the event-writer wiring task adds that section
(its globs cover ddflow/config_sections/; a knob file here would be dead config until
then). ``DDFLOW_SERVED_MODEL``/``DDFLOW_CTX_TOKENS`` are ddflow's own override channels:
a hook or launcher that KNOWS what was served exports one, and it beats every harness
env. ``MODEL_ENV`` holds only harness env names VERIFIED to carry the served model id
(docs/ddflow/research/agent-token-features-pass2.md, the copilot/devin/junie env
tables); nothing is listed on a guess -- an unverified name would attribute a record to
whatever that variable happens to hold.

The writers apply ``stamp()`` where an agent-authored record's data is composed,
before it is appended; a field the caller already set is kept.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from . import harnessreg

#: The literal every absent report reads as: attrbutting nothing is better than
#: guessing (bug Bb54029aee9).
UNKNOWN = "unknown"

#: The three provenance fields an agent-authored record carries. Event data (dict)
#: fields, so structural: consumers and doctor views read these names.
FIELDS = ("served_model", "harness", "ctx_tokens")

#: ddflow's own reporting channel for the served model id. An exporting hook wins over
#: every harness env below: it is a deliberate report, not a second claim.
DDFLOW_SERVED_MODEL = "DDFLOW_SERVED_MODEL"
#: ddflow's own reporting channel for the current context fill in tokens. Parts of the
#: value that are not a token count read as ``unknown``, never as a number ddflow could
#: mis-bucket.
DDFLOW_CTX_TOKENS = "DDFLOW_CTX_TOKENS"

#: Harness env names VERIFIED to carry the served model id, in precedence order
#: (agent-token-features-pass2.md, the copilot/devin/junie env tables). No context-fill
#: env name is verified there (the doc records hook INPUTS, not env), so none is listed:
#: an unverified name would be guessing.
MODEL_ENV: tuple[str, ...] = ("COPILOT_MODEL", "DEVIN_MODEL", "JUNIE_MODEL")


def served_model(environ: Mapping[str, str] | None = None) -> str:
    """The reported served model id, or ``UNKNOWN``. The ddflow channel wins; two
    different harness reports refuse to choose between themselves."""
    environ = os.environ if environ is None else environ
    own = (environ.get(DDFLOW_SERVED_MODEL) or "").strip()
    if own:
        return own
    reported = {(environ.get(var) or "").strip() for var in MODEL_ENV}
    reported.discard("")
    if len(reported) > 1:
        return UNKNOWN  # two channels, two ids: attribution never guesses
    return reported.pop() if reported else UNKNOWN


def ctx_tokens(environ: Mapping[str, str] | None = None) -> str:
    """The reported context fill in tokens, or ``UNKNOWN``. Only a decimal count is a
    report, kept as-is; anything else means the harness did not report one."""
    environ = os.environ if environ is None else environ
    value = (environ.get(DDFLOW_CTX_TOKENS) or "").strip()
    if not value:
        return UNKNOWN
    return value if value.isdigit() else UNKNOWN


def harness(environ: Mapping[str, str] | None = None) -> str:
    """The claiming harness id, or ``UNKNOWN``: the harness whose session variable is
    set, and therefore attributed without guessing between harnesses."""
    found = harnessreg.detect(os.environ if environ is None else environ)
    return found or UNKNOWN


def stamp(
    data: dict[str, Any] | None = None, environ: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """``data`` with the three provenance fields added; a field the caller already set
    is kept (a caller that knows better -- the operator's channel -- wins)."""
    environ = os.environ if environ is None else environ
    out = dict(data or {})
    out.setdefault("served_model", served_model(environ))
    out.setdefault("harness", harness(environ))
    out.setdefault("ctx_tokens", ctx_tokens(environ))
    return out
