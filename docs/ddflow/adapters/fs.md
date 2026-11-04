# Adapter family `fs`

Port: **Fs**, declared in `ddflow/adapters/fs.py` and marked `@port`.

Path operations on a file tree: `copy_file`, `copy_tree`, `move`, `rmtree` and `replace_path` (atomic rename). Atomic text writes, temporary files and locks stay in the `Fsio` family (`ddflow.infra.fsio`), which calls this adapter; callers import `copy_file`, `copy_tree`, `move` and `rmtree` from `ddflow.infra.fsio`, never `shutil`.

- **Default adapter:** `LocalFs` in `ddflow.adapters.fs`; `DEFAULT` is the shared instance (the one place `shutil` is imported once the sweep is done).
- **Fake:** `FakeFs` in `ddflow/adapters/fakes/fs.py`: in-memory files and directories, records every call in `calls`.
- **Conformance cases:** tests/test_adapter_fs.py.
- **Ratchet:** the `fsio-home` import contract forbids `shutil` outside `ddflow.adapters.fs`; the sites that still import it are one baseline file each in tests/guard_baselines/importlinter-fsio-home/ and only shrink (AU-fs-sweep migrates them). `os.replace` is counted by the existing `os_replace` guard.

The pattern is in [README.md](README.md).
