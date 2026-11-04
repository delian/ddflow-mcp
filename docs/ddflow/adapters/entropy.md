# Adapter family `entropy`

Port: **Entropy**, declared in `ddflow/adapters/entropy.py` and marked `@port`.

Randomness and ids in one place: `token_hex`, `uuid4`, `uniform` (jitter, not for secrets).

- **Default adapter:** `SystemEntropy` (`secrets`, `uuid`, `random`); `DEFAULT` is the shared instance.
- **Fake:** `FakeEntropy` in `ddflow/adapters/fakes/entropy.py`: seeded, so a test gets the same ids every run.
- **Conformance cases:** tests/test_adapter_entropy.py.

The pattern is in [README.md](README.md).
