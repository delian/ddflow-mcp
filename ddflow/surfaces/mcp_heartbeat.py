"""A heartbeat for MCP calls that can run silent for minutes (B7b3b4cf483).

Claude Code resets its idle timer on every `notifications/progress` for a call that sent a
`_meta.progressToken`, and aborts a call that stays quiet for its window. A gate command
that prints nothing for ten minutes sent no frame at all. This sends one every
`[mcp].progress_s` seconds from a thread for as long as the call runs, through the same
sender (and so the same counter) as a tool's own per-line progress."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from ..config import Config


@contextmanager
def heartbeat(
    repo, name: str, say: Callable[[str], None] | None, every: float | None = None
) -> Iterator[None]:
    """Call ``say`` every ``every`` seconds (default `[mcp].progress_s`) while the block
    runs. No ``say`` (the client sent no progress token) or an interval of 0 is no thread."""
    if say is None:
        yield
        return
    if every is None:
        try:
            every = float(Config.load(repo).mcp.progress_s)
        except Exception:  # a broken config must not fail the call it would keep alive
            every = 20.0
    if not math.isfinite(every) or every <= 0:  # nan would make wait() return at once: a spin
        yield
        return
    done = threading.Event()
    start = time.monotonic()

    def beat() -> None:
        while not done.wait(every):
            say(f"{name} still running ({time.monotonic() - start:.0f}s)")

    t = threading.Thread(target=beat, name=f"ddflow-heartbeat-{name}", daemon=True)
    t.start()
    try:
        yield
    finally:
        done.set()
        t.join(timeout=1)
