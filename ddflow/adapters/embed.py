"""The `Embedder` port and its default adapter: texts to vectors via model2vec.

The ONE home for model2vec (the ``extras-one-adapter`` contract): nothing else imports
``model2vec``; ``ddflow/services/embed.py`` (the semantic port, D-unify 2) reads through this
adapter for its in-process backend, so swapping that library touches one file. ``model2vec``
is imported at first use (it drags in numpy, joblib and tokenizers, seconds of CPU no other
verb needs); ``static_model`` is the class when the ``ddflow[rag]`` extra is installed, else
``None``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .ports import port

__all__ = ["DEFAULT", "Embedder", "Model2VecEmbedder", "default_embedder", "static_model"]


def static_model() -> type | None:
    """The model2vec ``StaticModel`` class, or None without the ``[rag]`` extra.

    The single import site; a broken install (ImportError or not) reports as None. Python's
    import cache makes repeats free; a caller that caches the first answer keeps it itself.
    """
    try:  # the [rag] extra; every caller works without it
        from model2vec import StaticModel
    except Exception:  # absent or broken install (ImportError or not): no embedder
        return None
    return StaticModel


@port
@runtime_checkable
class Embedder(Protocol):
    """Text to vectors. Home: `ddflow.adapters.embed` (`Model2VecEmbedder`); fake `FakeEmbedder`."""

    def embed(self, model_dir: str, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        """One vector per text, in order, from the model2vec model at ``model_dir``."""


class Model2VecEmbedder:
    """The default adapter: `StaticModel.from_pretrained(path).encode(texts)`; never downloads."""

    def embed(self, model_dir: str, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        cls = static_model()
        if cls is None:
            raise ImportError("the ddflow[rag] extra (model2vec) is not installed")
        encoded = cls.from_pretrained(model_dir).encode(list(texts))  # path checked by the caller
        return tuple(tuple(float(x) for x in row) for row in encoded)


#: The one stateless instance callers default to; a test passes `FakeEmbedder` instead.
DEFAULT = Model2VecEmbedder()


def default_embedder() -> Model2VecEmbedder:
    """The embedder a caller uses when it was handed none."""
    return DEFAULT
