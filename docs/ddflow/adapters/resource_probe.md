# Adapter family `resource_probe`

Port: **ResourceProbe**, declared in `ddflow/adapters/resource_probe.py`. The optional LIVE
sampler behind the perf metrics: one RSS sample of a process, no full memory info (slow).

- **Default adapter (stdlib):** `ProcProbe` (RSS of one pid via `/proc/<pid>/stat` field 24) then
  `PsProbe` (RSS via `ps -o rss=`); `sample(pid)` runs the stdlib ladder proc → ps.
- **Degrade ladder:** a host prepends its OPTIONAL cgroup-v2 and psutil adapters in the
  cgroup → psutil → proc → ps order and passes the list to `sample`; `degrade` returns the first
  adapter that answered, else `unavailable` (never a 0).
- **Fake:** `FakeProbe` in `ddflow/adapters/fakes/resource_probe.py`: a canned RSS (or None to
  test "the ladder declined").
- **Conformance cases:** tests/test_adapter_resource_probe.py (degrade fall-through, an
  explicit empty ladder is unavailable, the fake short-circuits a live read).
- **Not collected:** network bytes (no portable stdlib way); a host-wide counter delta is a
  labelled approximation. Only RSS is sampled; `io_in_blocks`/`io_out_blocks` ride the sample
  when an adapter supplies them, and are None otherwise.

The pattern is in [README.md](README.md).
