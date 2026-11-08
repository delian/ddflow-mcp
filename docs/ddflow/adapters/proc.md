# Adapter family `proc`

Port: **Proc**, declared in `ddflow/adapters/proc.py` and marked `@port`.

Run subprocesses with stdin detached from the MCP stdio session.

- **Default adapter:** `ddflow.infra.proc` today.
- **Fake:** none yet (baseline `tests/guard_baselines/ports_without_fake/Proc.txt`)
- **Conformance cases:** none yet (they arrive with the fake).

The pattern is in [README.md](README.md).
