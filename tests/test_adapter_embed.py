"""The `Embedder` port: the default adapter's behaviour and the fake's (CONFORMANCE)."""

from __future__ import annotations

from ddflow.adapters import embed as N
from ddflow.adapters.embed import Embedder
from ddflow.adapters.fakes import FAKES
from ddflow.adapters.fakes.embed import FakeEmbedder


class _FakeStaticModel:
    """Stands in for model2vec's `StaticModel`: the real one needs a model on disk."""

    rows = [[1.0, 0.0]]

    @classmethod
    def from_pretrained(cls, path):
        return cls()

    def encode(self, texts):
        return [self.rows[i % len(self.rows)] for i, _ in enumerate(texts)]


def _real(monkeypatch):
    monkeypatch.setattr(N, "static_model", lambda: _FakeStaticModel)
    return N.Model2VecEmbedder()


def embed_conformance(e: Embedder) -> None:
    got = e.embed("/model", ["a", "b"])
    assert isinstance(got, tuple) and len(got) == 2
    assert all(isinstance(r, tuple) and all(isinstance(x, float) for x in r) for r in got)


CONFORMANCE = {
    "Embedder": {"real": _real, "cases": embed_conformance}
}


def test_the_adapter_and_the_fake_satisfy_the_port():
    assert isinstance(N.Model2VecEmbedder(), Embedder)
    assert isinstance(FakeEmbedder(), Embedder)
    assert FAKES["Embedder"] is FakeEmbedder


def test_the_fake_returns_the_vector_it_was_built_with():
    assert FakeEmbedder((1.0, 2.0)).embed("/m", ["a", "b"]) == ((1.0, 2.0), (1.0, 2.0))
