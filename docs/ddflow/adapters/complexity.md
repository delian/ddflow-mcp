# Adapter family `complexity`

Port: **Complexity**, declared in `ddflow/adapters/complexity.py` and marked `@port`.

Cyclomatic complexity: `complexities` returns `{qualified function name: (line, complexity)}`
from radon's `cc_visit`, empty when radon is missing or the source does not parse.

- **Default adapter:** `RadonComplexity` (radon); `DEFAULT` is the shared instance.
- **Fake:** `FakeComplexity` in `ddflow/adapters/fakes/complexity.py`: scripted table, no radon.
- **Conformance cases:** tests/test_adapter_complexity.py.

The one home for radon (`extras-one-adapter`); `ddflow/services/complexity_budget.py`
re-exports `complexities` so `risen`/`advice` read through one function.
