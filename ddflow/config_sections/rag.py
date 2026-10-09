"""The `[rag]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._docs import declare, knob

#: The longest an embedding call may take, in seconds.
RAG_MAX_TIMEOUT_S = 600


def timeout_problem(v: Any) -> str:
    """ "" for a valid `[rag].timeout_s`, else what it must be."""
    if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= RAG_MAX_TIMEOUT_S:
        return ""
    return f"must be an integer from 1 to {RAG_MAX_TIMEOUT_S} (seconds)"


@declare("rag")
@dataclass
class RagConfig:
    """The Embedder behind every semantic feature (D-unify 2): `[rag]`."""

    command: str = knob(
        "",
        doc='The embedder companion: a command (a shell line, run like every operator command, in the repository root) that reads {"texts": ["...", ...]} as JSON on stdin and prints {"model": "<model id>", "vectors": [[...], ...]} on stdout, one vector per text, all the same length. The model id is how stored vectors are told apart from a different model\'s. Empty (default): none. A call that times out, exits non-zero, prints anything else or returns the wrong number of vectors is `unavailable` with the reason, never a partial answer. This runs a program, so set it in the git-ignored local file (`config --set rag.command ... --local`).',
    )
    model_dir: str = knob(
        "",
        doc="A LOCAL model2vec model directory, used in-process when `[rag].command` is empty and the `ddflow[rag]` extra (model2vec) is installed. ddflow never downloads a model: a path that is not a directory makes the embedder `unavailable`. Empty (default): none.",
    )
    timeout_s: int = knob(
        60,
        doc=f"Seconds one embedding call (the companion command) may take before it is `unavailable` (default 60, 1 to {RAG_MAX_TIMEOUT_S}).",
        check=timeout_problem,
    )
