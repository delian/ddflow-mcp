"""Does it hold up when many agents hit it at once, and does it stay fast as the log grows?

The existing concurrency tests (`test_stress.py`, `test_lease_and_recovery.py`) drive the
CLI and the event log directly. Nothing exercised the **MCP surface** under load, which
is the surface the agents actually use — so the one place a deadlock or a lost write
would be seen first had no coverage at all.

Four questions, each with its own failure mode:

1. **Does it deadlock?** `EventLog.append` takes an exclusive `flock`, and `flock` is
   per-open-file-description — a second `EventLog` for one repo in one process blocks
   forever against a lock that process already holds. `_HELD` guards that case today;
   what is untested is many PROCESSES contending, which is the real deployment.
2. **Does it lose writes?** Per-agent shards mean appends do not contend, but the
   Lamport high-water mark is read inside the lock and a torn read there would silently
   reuse a clock value.
3. **Is attribution right under concurrency?** Identity is per connection. Under load,
   "mostly right" is indistinguishable from right on a small sample.
4. **Does it stay fast as the log grows?** Every state-reading call re-folds the WHOLE
   log (`Ctx.state()` is `fold(read_all())`), so cost is O(events) per call with no
   snapshot on that path. This is a real scaling property and the test measures it
   rather than asserting a wall-clock number that means nothing on someone else's CI.

**On timing assertions.** Absolute latency budgets on shared CI runners are a source of
flakes that teaches people to ignore the suite. So the budgets here are generous, and
every one is overridable from the environment — the numbers are defaults, not facts
about your machine.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog

pytestmark = pytest.mark.slow


def _knob(name: str, default: float) -> float:
    """Every threshold in this file is a default, not a fact about your hardware.

    A load test whose limits are baked in either fails on a slow shared runner or is
    set so loose it cannot fail at all. These are tunable per environment.
    """
    return float(os.environ.get(name, default))


#: How many agents contend at once. Above the usual core count on purpose: the point is
#: to oversubscribe, because a lock bug that needs contention to appear will not appear
#: at one process per core.
LOAD_AGENTS = int(_knob("DDFLOW_LOAD_AGENTS", 12))
#: Calls each agent makes.
LOAD_CALLS = int(_knob("DDFLOW_LOAD_CALLS", 15))
#: Whole-run ceiling. This is the DEADLOCK detector, not a performance assertion: a
#: wedged `flock` does not fail, it hangs, and a hang with no bound reads as a stuck CI
#: job rather than as a bug.
LOAD_TIMEOUT_S = _knob("DDFLOW_LOAD_TIMEOUT_S", 240.0)
#: Ceiling on the mean write latency through the full MCP surface.
WRITE_LATENCY_BUDGET_S = _knob("DDFLOW_WRITE_LATENCY_BUDGET_S", 2.0)
#: How much slower a read may get when the log grows by `GROWTH_FACTOR`. Folding is
#: O(events), so some growth is EXPECTED and the test asserts it stays roughly linear
#: rather than pretending it is flat. Superlinear here means an accidental O(n^2).
GROWTH_TOLERANCE = _knob("DDFLOW_GROWTH_TOLERANCE", 4.0)


# -- workers must be module level: `spawn` pickles them by reference -------------------


def _writer(args: tuple[str, str, int]) -> tuple[str, int, int, float]:
    """One agent, its own connection, N writes. Returns (agent, ok, failed, seconds)."""
    repo_s, agent, count = args
    from ddflow.surfaces.mcp import Server

    repo = Path(repo_s)
    srv = Server(repo)
    srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "tools/call",
            "params": {"name": "ddflow_identify", "arguments": {"agent": agent}},
        }
    )
    ok = failed = 0
    t0 = time.monotonic()
    for i in range(count):
        reply = srv.handle(
            {
                "jsonrpc": "2.0",
                "id": i + 1,
                "method": "tools/call",
                "params": {
                    "name": "ddflow_task_add",
                    "arguments": {
                        "id": f"{agent}-T{i}",
                        "title": f"task {i} from {agent}",
                        "globs": f"{agent}/{i}.py",
                    },
                },
            }
        )
        if reply and not reply.get("result", {}).get("isError"):
            ok += 1
        else:
            failed += 1
    return agent, ok, failed, time.monotonic() - t0


def _reader(args: tuple[str, int]) -> tuple[int, int]:
    """Readers take no lock by design. If that is true, they cannot be starved by
    writers — and if it is not, this is where it shows."""
    repo_s, count = args
    from ddflow.surfaces.mcp import Server

    srv = Server(Path(repo_s))
    ok = 0
    for i in range(count):
        reply = srv.handle(
            {
                "jsonrpc": "2.0",
                "id": i,
                "method": "tools/call",
                "params": {"name": "ddflow_status", "arguments": {}},
            }
        )
        # `"result" in reply` is true for an isError reply too, so counting that way
        # could only ever see a read that HUNG -- never one that failed. The assertion
        # `ok == total` then held while every read errored.
        if reply and "result" in reply and not reply["result"].get("isError"):
            ok += 1
    return ok, count


def _mixed(args: tuple[str, str, int]) -> tuple[str, int, int, int]:
    """Read-modify-write interleaved with other agents doing the same — the shape that
    loses updates when a check-then-act is not inside the transaction."""
    repo_s, agent, count = args
    from ddflow.surfaces.mcp import Server

    srv = Server(Path(repo_s))
    srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "tools/call",
            "params": {"name": "ddflow_identify", "arguments": {"agent": agent}},
        }
    )
    reads = writes = failed = 0
    for i in range(count):
        r = srv.handle(
            {
                "jsonrpc": "2.0",
                "id": i,
                "method": "tools/call",
                "params": {"name": "ddflow_next", "arguments": {}},
            }
        )
        if r and "result" in r:
            reads += 1
        w = srv.handle(
            {
                "jsonrpc": "2.0",
                "id": 10_000 + i,
                "method": "tools/call",
                "params": {
                    "name": "ddflow_lesson_add",
                    # `title` is the required field, not `text`. The first version of
                    # this worker passed `text` and every call failed with "missing
                    # required argument(s): title" -- and the test PASSED, because it
                    # compared the workers' own success count (0) against the lessons
                    # in the log (0) and called that "nothing was lost". A load test
                    # that exercises nothing is the vacuous-pass class wearing a
                    # stopwatch.
                    "arguments": {"title": f"{agent} observed {i}", "tags": "load"},
                },
            }
        )
        if w and not w.get("result", {}).get("isError"):
            writes += 1
        else:
            failed += 1
    return agent, reads, writes, failed


def _pool_map(fn, items, timeout_s: float):
    """Run in real processes, bounded. A deadlock must surface as a FAILURE with a
    message, not as a CI job that runs until the runner kills it — the second is
    indistinguishable from an infrastructure problem and gets retried, not fixed."""
    ctx = mp.get_context("spawn")
    with ctx.Pool(processes=min(len(items), (os.cpu_count() or 4) * 2)) as pool:
        async_result = pool.map_async(fn, items)
        try:
            return async_result.get(timeout=timeout_s)
        except mp.TimeoutError:
            pool.terminate()
            pytest.fail(
                f"{len(items)} concurrent workers did not finish within {timeout_s}s. "
                f"That is the deadlock signature: `EventLog.append` holds an exclusive "
                f"flock, and a lock acquired twice in one process against its own "
                f"open file description blocks forever."
            )


# -- 1. many agents writing at once ----------------------------------------------------


def test_many_agents_write_through_MCP_without_deadlock_or_loss(repo):
    """The headline case: N agents, each its own connection, all writing at once.

    Asserts three separate things, because they fail independently — every call
    succeeded, every event survived, and no two agents' work got merged.
    """
    run_cli(repo, "init")
    jobs = [(str(repo), f"agent-{i:02d}", LOAD_CALLS) for i in range(LOAD_AGENTS)]
    results = _pool_map(_writer, jobs, LOAD_TIMEOUT_S)

    failures = {a: f for a, _ok, f, _s in results if f}
    assert not failures, f"calls returned errors under load: {failures}"

    expected = LOAD_AGENTS * LOAD_CALLS
    st = fold(EventLog(repo).read_all(), strict=False)
    got = [i for i in st.items if "-T" in i]
    assert len(got) == expected, (
        f"{expected} tasks were written, {len(got)} survived the fold. A missing event "
        f"is a lost append; a duplicate id means two agents collided on one."
    )

    # Measured from each WORKER's own elapsed time, not from wall clock divided by all
    # calls. The latter was ~12x smaller than the per-write latency it was labelled as,
    # and -- worse -- it could not fail: `_pool_map` aborts the run at LOAD_TIMEOUT_S,
    # so `elapsed / (12 * 15)` was bounded above by 240/180 = 1.33s, already under the
    # 2.0s default and far under the 6.0s the CI step sets. The budget was decoration
    # and the CI override was dead, while a genuinely slow-but-not-wedged runner was
    # reported with the message "that is the deadlock signature".
    slowest = max(secs / LOAD_CALLS for _a, _ok, _f, secs in results)
    assert slowest < WRITE_LATENCY_BUDGET_S, (
        f"slowest agent averaged {slowest:.3f}s per write through the MCP surface "
        f"under {LOAD_AGENTS}-way contention, budget {WRITE_LATENCY_BUDGET_S}s "
        f"(override with DDFLOW_WRITE_LATENCY_BUDGET_S). This is SLOWNESS, not a "
        f"deadlock — the deadlock bound is LOAD_TIMEOUT_S and it was not reached."
    )


def test_every_event_is_attributed_to_the_agent_that_wrote_it(repo):
    """Attribution under contention, which is where "mostly right" hides.

    Each agent's tasks carry its own name in the id, so the event's agent and its
    subject must agree for every single one — not on a sample.
    """
    run_cli(repo, "init")
    jobs = [(str(repo), f"agent-{i:02d}", LOAD_CALLS) for i in range(LOAD_AGENTS)]
    _pool_map(_writer, jobs, LOAD_TIMEOUT_S)

    mismatched = [
        (e.subject, e.agent)
        for e in EventLog(repo).read_all()
        if "-T" in str(e.subject) and not str(e.subject).startswith(e.agent)
    ]
    assert not mismatched, (
        f"{len(mismatched)} event(s) attributed to an agent that did not write them, "
        f"e.g. {mismatched[:5]}"
    )


def test_the_lamport_clock_never_repeats_within_one_agent(repo):
    """The high-water mark is re-read INSIDE the lock; a torn read there would reuse a
    clock value and silently reorder history. Per agent, because the clock is only
    required to be monotonic within a shard."""
    run_cli(repo, "init")
    jobs = [(str(repo), f"agent-{i:02d}", LOAD_CALLS) for i in range(LOAD_AGENTS)]
    _pool_map(_writer, jobs, LOAD_TIMEOUT_S)

    seen: dict[str, set[int]] = {}
    dupes = []
    for e in EventLog(repo).read_all():
        bucket = seen.setdefault(e.agent, set())
        if e.lamport in bucket:
            dupes.append((e.agent, e.lamport))
        bucket.add(e.lamport)
    assert not dupes, f"repeated lamport values within one agent: {dupes[:5]}"


# -- 2. readers must not be starved ----------------------------------------------------


def test_readers_are_not_blocked_by_writers(repo):
    """`EventLog` documents that readers take no lock. If that ever stopped being true,
    a read-heavy agent would stall behind a write-heavy one and the symptom would be
    'the tool got slow', which nobody debugs to a lock."""
    run_cli(repo, "init")
    for i in range(20):
        run_cli(repo, "task", "add", f"SEED{i}", "--globs", f"s{i}.py")

    jobs = [(str(repo), f"w-{i}", LOAD_CALLS) for i in range(LOAD_AGENTS // 2)]
    jobs += [(str(repo), LOAD_CALLS)] * (LOAD_AGENTS // 2)  # readers

    # Two `map_async` calls on ONE pool, started before either is awaited, so the
    # readers really are running while the writers are. (`spawn` cannot pickle a
    # closure, so a single heterogeneous map over both job shapes is not available.)
    ctx = mp.get_context("spawn")
    with ctx.Pool(processes=LOAD_AGENTS) as pool:
        writers = pool.map_async(_writer, [j for j in jobs if len(j) == 3])
        readers = pool.map_async(_reader, [j for j in jobs if len(j) == 2])
        try:
            w = writers.get(timeout=LOAD_TIMEOUT_S)
            r = readers.get(timeout=LOAD_TIMEOUT_S)
        except mp.TimeoutError:
            pool.terminate()
            pytest.fail("readers and writers deadlocked against each other")

    assert all(f == 0 for _a, _o, f, _s in w), "writes failed while readers were active"
    for ok, total in r:
        assert ok == total, f"only {ok}/{total} reads completed while writes were in flight"


def test_a_read_modify_write_loop_under_contention_loses_nothing(repo):
    """`next` then a write, from several agents at once — the check-then-act shape. Every
    lesson written must be present: a lost one means an append raced another."""
    run_cli(repo, "init")
    for i in range(10):
        run_cli(repo, "task", "add", f"T{i}", "--globs", f"t{i}.py")

    n = max(2, LOAD_AGENTS // 3)
    per = 5
    jobs = [(str(repo), f"mix-{i}", per) for i in range(n)]
    results = _pool_map(_mixed, jobs, LOAD_TIMEOUT_S)

    # Assert the calls SUCCEEDED before asserting their effects survived. Deriving
    # `expected` from the workers' own success counts and comparing it to the log makes
    # the all-failed case read as a pass: 0 written, 0 survived, "nothing was lost".
    failures = {a: f for a, _r, _w, f in results if f}
    assert not failures, f"writes failed under contention: {failures}"
    assert sum(w for _a, _r, w, _f in results) == n * per, "a worker wrote fewer than asked"
    assert all(r == per for _a, r, _w, _f in results), "a `next` call failed under contention"

    st = fold(EventLog(repo).read_all(), strict=False)
    assert len(st.lessons) == n * per, (
        f"{n * per} lessons were written and {len(st.lessons)} survived — a "
        f"read-modify-write under contention lost an append"
    )


# -- 3. and it has to stay fast as the log grows ---------------------------------------


def _time_read(repo: Path, n: int = 5) -> float:
    from ddflow.surfaces.mcp import Server

    srv = Server(repo)
    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "ddflow_status", "arguments": {}},
    }
    t0 = time.monotonic()
    for _ in range(n):
        srv.handle(dict(msg))
    return (time.monotonic() - t0) / n


def test_read_latency_grows_no_worse_than_linearly_with_the_log(repo):
    """Every state-reading call re-folds the whole log, so growth is expected and the
    honest assertion is about its SHAPE.

    Linear is the design. Superlinear means something inside the fold is itself scanning
    per event — an accidental O(n^2) that looks fine at 200 events and is unusable at
    20,000, which is squarely inside the range a 400-day project reaches.
    """
    run_cli(repo, "init")
    for i in range(50):
        run_cli(repo, "task", "add", f"A{i}", "--globs", f"a{i}.py")
    small = _time_read(repo)
    small_n = len(EventLog(repo).read_all())

    for i in range(450):
        run_cli(repo, "task", "add", f"B{i}", "--globs", f"b{i}.py")
    big = _time_read(repo)
    big_n = len(EventLog(repo).read_all())

    growth = big_n / max(small_n, 1)
    slowdown = big / max(small, 1e-6)
    assert slowdown < growth * GROWTH_TOLERANCE, (
        f"log grew {growth:.1f}x ({small_n} -> {big_n} events) and reads got "
        f"{slowdown:.1f}x slower — worse than linear by more than the "
        f"{GROWTH_TOLERANCE}x tolerance (DDFLOW_GROWTH_TOLERANCE). Something in the "
        f"fold is scanning per event."
    )


def test_the_cost_of_a_read_is_reported_so_a_regression_is_visible(repo, capsys):
    """Not an assertion — a MEASUREMENT, printed.

    There is no snapshot on the state-reading path: `Ctx.state()` folds everything, every
    call. That is a deliberate trade (one source of truth, no cache to invalidate) and it
    has a cost that grows. Printing it means a future change that makes it ten times
    worse is visible in the CI log instead of being discovered by an operator.
    """
    run_cli(repo, "init")
    for i in range(200):
        run_cli(repo, "task", "add", f"T{i}", "--globs", f"t{i}.py")
    n = len(EventLog(repo).read_all())
    per_read = _time_read(repo, n=10)
    with capsys.disabled():
        print(
            f"\n  [perf] {n} events -> {per_read * 1000:.1f} ms per ddflow_status "
            f"({per_read * 1e6 / max(n, 1):.1f} us/event)"
        )
    assert per_read > 0


# -- 4. the questions an operator will actually ask ------------------------------------


def test_one_server_process_serves_exactly_one_repository(repo, tmp_path):
    """Asserted because it is a real constraint operators hit, and finding it out by
    experiment is expensive.

    `repo` is fixed at process start and no tool takes a repo argument, so a second
    project needs a second server process. That is a deliberate design — a shared
    server would have to authorise every call against every project — but it must be
    DOCUMENTED by a test rather than by the absence of a feature.
    """
    from ddflow.surfaces.mcp import TOOLS, Server

    srv = Server(repo)
    assert srv.repo == Path(repo)
    takes_repo = [n for n, s in TOOLS.items() if "repo" in s.get("properties", {})]
    assert not takes_repo, (
        f"{takes_repo} accept a repo argument, so the one-process-one-project "
        f"invariant no longer holds and the isolation story needs rewriting"
    )


def test_concurrent_connections_in_one_process_do_not_share_identity(repo):
    """Two `Server` objects in ONE process, which is what a future threaded or async
    dispatcher would create. Identity lives on the connection, so it must not leak
    between them through any module-level state."""
    from ddflow.surfaces.mcp import Server

    run_cli(repo, "init")
    a, b = Server(repo), Server(repo)
    for srv, name in ((a, "left"), (b, "right")):
        srv.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "ddflow_identify", "arguments": {"agent": name}},
            }
        )
    assert (a.agent, b.agent) == ("left", "right")


def test_the_json_surface_stays_parseable_under_load(repo):
    """A torn write in the log shows up as unparseable JSON on the way out. Reading it
    back through the surface an agent uses is the check that matters."""
    run_cli(repo, "init")
    jobs = [(str(repo), f"agent-{i:02d}", 5) for i in range(LOAD_AGENTS)]
    _pool_map(_writer, jobs, LOAD_TIMEOUT_S)

    code, out, _err = run_cli(repo, "--json", "status")
    assert code == 0
    payload = json.loads(out)  # raises if a concurrent write tore the projection
    assert payload, "status came back empty after a concurrent write burst"
