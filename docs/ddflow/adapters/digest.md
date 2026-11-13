# Adapter family `digest`

Port: **Digest**, declared in `ddflow/adapters/digest.py` and marked `@port`.

The one hashing and content-digest family (stdlib `hashlib` only).

- **Default adapter:** `ddflow.adapters.digest` today.
- **Fake:** `FakeDigest`, a deterministic CRC chain: same input same value, `length` cuts the hex, `canonical` sorts keys.
- **Conformance cases:** tests/test_adapter_digest.py.

The pattern is in [README.md](README.md).
