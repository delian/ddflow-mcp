# Adapter family `git`

Port: **Git**, declared in `ddflow/adapters/git.py` and marked `@port`.

Every git call, as a value rather than an exception where the exit code is the answer.

- **Default adapter:** `ddflow.adapters.git` today.
- **Fake:** none yet (baseline `tests/guard_baselines/ports_without_fake/Git.txt`)
- **Conformance cases:** none yet (they arrive with the fake).

The pattern is in [README.md](README.md).
