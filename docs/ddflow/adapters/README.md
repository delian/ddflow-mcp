# Adapters

Every outside dependency (a subprocess, git, a file, a forge, a model endpoint) is reached
through a **port** so it can be swapped or faked. Decided in D-adapters-answers,
D-adapters-answers-2 and D-adapters-answers-3; the code half is pinned by
`tests/test_ports_contract.py`. This directory is the index: one page per family, named after
its module in `ddflow/adapters/`. Listing the directory lists the families, so adding one
edits no shared file (ADP-modular-registry).

## The pattern

- **A port** is a `typing.Protocol` named for the family's noun, marked `@port`. It lives in
  its family module `ddflow/adapters/<family>.py`, beside its default adapter once the home has
  moved in (ADP-migrate-homes).
  `ddflow/adapters/ports.py` is only the shared base: the marker and the discovery helpers.
- **The default adapter** is the ONE implementation, in that module once the family's home
  has moved in (ADP-migrate-homes; until then the family page names where it is). A second implementation
  of a single-adapter family is a bug. A family with several adapters holds them in a
  `Registry[Port]` (`ddflow/adapters/registry.py`) keyed by `name`; configuration picks one.
- **The fake** is a `@fake class Fake<Port>` in `ddflow/adapters/fakes/<family>.py`, shipped
  (not test-only). Fake modules are imported in sorted order and register themselves under
  the port's name in `FAKES`.
- **Conformance**: `tests/test_adapter_<family>.py` has a `CONFORMANCE` table, per port a
  `real` factory (monkeypatch to adapter), an optional `fake` factory and the `cases`; the
  one generic test runs the same cases against the adapter and its fake.
- **Baseline**: a port that has no fake yet has one file in
  `tests/guard_baselines/ports_without_fake/`. The set only shrinks.
- `adapters` is the bottom layer: it imports no other ddflow layer, and every layer may
  import it. Config for ddflow itself stays TOML only.

## Add a port

1. `ddflow/adapters/<family>.py`: the `@port` Protocol (declarations only) and its default
   adapter, importing nothing above `adapters`.
2. `ddflow/adapters/fakes/<family>.py`: `@fake class Fake<Port>`.
3. `tests/test_adapter_<family>.py`: the adapter's own tests and `CONFORMANCE`.
4. `docs/ddflow/adapters/<family>.md`: one short page.

No other file changes: `tests/test_adapter_modularity.py` fails if a port needs an edit to
`ports.py`, `fakes/__init__.py`, `tests/test_ports_contract.py` or `docs/ARCHITECTURE.md`.
Check with `uv run pytest tests/test_ports_contract.py tests/test_adapter_modularity.py
tests/test_layering.py` (MCP: `ddflow_gate action=run id=<item> gate=unit_tests`).

## Swap a dependency

Take the port as an argument (or look it up in the family's registry), never import the
adapter at the call site; in a test pass the fake. A module that migrates behind a port keeps
a thin re-export at its old import path until its import-linter baseline is empty
(ADP-migrate-homes): no behaviour change, and every baseline only shrinks (D-unify).

## Families planned in P-adapters

http, sqlite, toml, fs (atomic write, lock, scratch), templates, clock and env, search,
reviewer clients, managed files, config store, git, forge, MCP, ACP, agent lanes, lane
transport, notify and ask, external tools, platform (Linux and macOS; Windows reports
unavailable), log store, secrets providers (SX-provider-core), scheduler, optimizer, and the
home ports (`proc`, `git`, `fsio`, `tomlcfg`, `digest`). Adapters that reach the network
(forge, chat, webhook, email) need consent recorded once per kind and report unavailable when
declined or offline. A family lands with its task as the four files above.
