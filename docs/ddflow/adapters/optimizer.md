# Adapter family `optimizer`

Port: **Optimizer** (`OptimizerPort` is an alias), declared in `ddflow/adapters/optimizer.py` and marked `@port`.

Orders the ready set and cuts it to the free slots: `order(ctx, candidates)` returns one `Placement` per candidate, best first, with its itemised terms; `cut(ctx, ready, slots, hold=, clash=)` returns one verdict per item (`taken`, `held`, `clash`, `cut`); `explain(placement)` says why in words. Deterministic: integer terms, the id breaks every tie, no clock, no randomness. An optimizer only reorders and cuts; hard constraints stay `needs` and `globs`. `ctx` is an open mapping: the caller fills `ctx["facts"]` with the per-candidate facts an adapter may read (`critical`: on the critical path, `waited_s`: seconds since filed) and an adapter reads the keys it knows. An adapter declares the keys it reads with its `facts` set; the default reads none, so a plain plan computes no facts.

- **Adapters:** `greedy-v1` (`GreedyV1`, the default and today's algorithm: the order keys of `core.schedule`, bugs first, priority, id, and a greedy overlap-free cut). `swarm-v1` (`SwarmV1`) is the second: the swarm scheduler's item order — critical-path items first, then the longest-waiting, then the greedy keys — over the SAME greedy cut, so the overlap check stays one. Several adapters live in `OPTIMIZERS`, keyed by name; `[schedule].optimizer` (file, CLI, MCP) picks one; an unknown name is refused with the names known.
- **Fake:** `FakeOptimizer` in `ddflow/adapters/fakes/optimizer.py`: pin an order with `ranking`, force verdicts with `verdicts`, read the calls back from `calls`.
- **Conformance cases:** tests/test_adapter_optimizer.py.

The pattern is in [README.md](README.md).
