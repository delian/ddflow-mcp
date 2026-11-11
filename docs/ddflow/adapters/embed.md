# Adapter family `embed`

Port: **Embed**, declared in `ddflow/adapters/embed.py` and marked `@port`.

Text to vectors: `embed` returns one vector per text from the model2vec model at `model_dir`
(`StaticModel.from_pretrained(path).encode(texts)`); the path is checked by the caller, never
downloaded here.

- **Default adapter:** `Model2VecEmbedder` (model2vec); `DEFAULT` is the shared instance.
- **Fake:** `FakeEmbed` in `ddflow/adapters/fakes/embed.py`: one scripted vector for every text.
- **Conformance cases:** tests/test_adapter_embed.py.

The one home for model2vec (`extras-one-adapter`); `ddflow/services/embed.py` (the semantic
port, D-unify 2) caches this adapter's class in `_static_model`.
