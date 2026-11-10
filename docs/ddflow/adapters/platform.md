# Adapter family `platform`

Port: **Platform**, declared in `ddflow/adapters/platform.py` and marked `@port`.

OS-dependent operations behind one seam: advisory file locks, process-group signals,
owner-only (0600) permissions, home directory, executable lookup and terminal detection.

- **Default adapter:** `ddflow.adapters.platform.Posix` (`DEFAULT`), wrapping the
  existing code in `fsio.file_lock`, `proc_server.kill_group`, `fs.which`.
- **Windows adapter:** `ddflow.adapters.platform.Windows` -- documented unsupported
  (D-adapters-answers-3); every verb reports `unavailable` (`NotImplementedError`).
- **Fake:** `ddflow.adapters.fakes.platform.FakePlatform` (recorded answers).
- **Conformance cases:** `tests/test_adapter_platform.py`.

The pattern is in [README.md](README.md).
