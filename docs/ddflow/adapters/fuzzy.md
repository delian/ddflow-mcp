# Adapter family `fuzzy`

Port: **Fuzzy**, declared in `ddflow/adapters/fuzzy.py` and marked `@port`.

Fuzzy string likeness: `ratio` is rapidfuzz's token-set ratio when the `[search]` extra is
installed and `difflib`'s sequence ratio otherwise; both fold to lowercase and are 0..1.
`available` says which one a caller is getting.

- **Default adapter:** `DefaultFuzzy` (rapidfuzz, dirfflib fallback); `DEFAULT` is the shared instance.
- **Fake:** `FakeFuzzy` in `ddflow/adapters/fakes/fuzzy.py`: case-folded exact match, no rapidfuzz.
- **Conformance cases:** tests/test_adapter_fuzzy.py.

The one home for rapidfuzz (`extras-one-adapter`); `ddflow/core/fuzzy.py` re-exports `ratio`
and `available`, so `core` stays pure.
