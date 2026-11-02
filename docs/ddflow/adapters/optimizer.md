# Adapter family `optimizer`

Port: **Optimizer** (`OptimizerPort` is an alias), declared in `ddflow/adapters/optimizer.py` and marked `@port`.

Orders the ready set and cuts it to the free slots: `order(ctx, candidates)` returns one `Placement` per candidate, best first, with its itemised terms; `cut(ctx, ready, slots, hold=, clash=)` returns one verdict per item (`taken`, `held`, `clash`, `cut`); `explain(placement)` says why in words. Deterministic: integer terms, the id breaks every tie, no clock, no randomness. An optimizer only reorders and cuts; hard constraints stay `needs` and `globs`. `ctx` is an open mapping: an adapter reads the keys it knows (lock-wait signals will arrive there).

- **Adapters:** `greedy-v1` (`GreedyV1`, the default and today's algorithm: the order keys of `core.schedule`, bugs first, priority, id, and a greedy overlap-free cut). Several adapters live in `OPTIMIZERS`, keyed by name; `[schedule].optimizer` (file, CLI, MCP) picks one; an unknown name is refused with the names known.
- **Fake:** `FakeOptimizer` in `ddflow/adapters/fakes/optimizer.py`: pin an order with `ranking`, force verdicts with `verdicts`, read the calls back from `calls`.
- **Conformance cases:** tests/test_adapter_optimizer.py.

The pattern is in [README.md](README.md).
