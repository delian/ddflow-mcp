"""The fake live sampler (PM-sampler): a canned RSS for a recorded pid (tests run it, never /proc)."""

from __future__ import annotations

from ddflow.adapters.resource_probe import ResourceProbe, ResourceSample


class FakeProbe(ResourceProbe):
    """Returns a fixed sample for every pid, or None for an unrecorded one (the fake must be
    able to say the ladder is unavailable on this host, so the degrade contract is testable)."""

    def __init__(self, rss_kb: int | None = 1024, source: str = "proc"):
        self._rss = rss_kb
        self._source = source

    def name(self) -> str:
        return "fake"

    def sample(self, pid: int) -> ResourceSample | None:
        return None if self._rss is None else ResourceSample(self._rss, self._source)


__all__ = ["FakeProbe"]
