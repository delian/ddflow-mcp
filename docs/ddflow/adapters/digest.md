# Adapter family `digest`

Port: **Digest**, declared in `ddflow/adapters/digest.py` and marked `@port`.

The one hashing and content-digest family (stdlib `hashlib` only).

- **Default adapter:** `ddflow.core.digest` today (compat; the home moves in with ADP-migrate-homes).
- **Fake:** `FakeDigest`, a deterministic CRC chain: same input same value, `length` cuts the hex, `canonical` sorts keys.
- **Conformance cases:** tests/test_adapter_digest.py.

The pattern is in [README.md](README.md).
