# Adapter family `transport`

Port: **Transport**, declared in `ddflow/adapters/transport.py` and marked `@port`.

Where and how a lane's process runs, and how its files come back: **start**, **signal**, observe liveness, **copy** in and out (the lane's own output is its `lane.log` read back with `copy_out`), **cleanup**. Multi-adapter, keyed by `name` in a `Registry[Transport]`; `target` says where one instance runs.

- **Default adapter:** `LocalTransport` in `ddflow/adapters/transport_local.py`, registered under `local`. It spawns the argv list as one local subprocess in its own directory, so liveness is the held child, never a recycled pid.
- **Fake:** `FakeTransport` in `ddflow/adapters/fakes/transport.py`, a scripted lane that records every call and never spawns a process.
- **Conformance cases:** `tests/test_adapter_transport.py`, run against the adapter and the fake.

The other executors (tmux, ssh, container, dsh, the git-bundle remote) register into the same registry as their own modules; remote trust and consent stay in the swarm decisions, not in this port.

The pattern is in [README.md](README.md).
