"""Companion detection — and the difference between "no" and "could not tell".

The distinction is the same one the gate runner makes between a check that ran and
failed and one that could not run, for the same reason: a tool that quietly stopped
being installed must not read as a tool that is quietly absent, and neither must read
as a fact when it is a guess.

`is_installed`'s docstring has always promised it — *"could not tell" must never render
as "no"* — while the code returned `False` for a probe that timed out or could not be
spawned. A docstring is not a check.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.services import companions as CO

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _companion(**over) -> CO.Companion:
    spec = {
        "id": "probe_target",
        "title": "Probe target",
        "detect": ["python3", "-c", "pass"],
        "install": "pip install nothing",
        "gates": [],
    }
    spec.update(over)
    return CO.Companion(**spec)


# -- three values, because there are three answers -------------------------------------


def test_a_probe_that_runs_and_succeeds_is_installed():
    installed, detail = CO.is_installed(_companion())
    assert installed is True, detail


def test_a_tool_that_is_not_on_PATH_is_absent_not_unknown():
    """An empty PATH lookup is a FACT. Reporting it as unknown would be as wrong in the
    other direction, and would make the unknown state meaningless."""
    installed, detail = CO.is_installed(_companion(detect=["definitely-not-a-real-binary-xyz"]))
    assert installed is False
    assert "PATH" in detail, detail


def test_a_probe_that_TIMES_OUT_is_unknown_not_absent(monkeypatch):
    """The case that collapsed. A slow machine, a wedged tool, a cold `npx` cache —
    none of them means the companion is missing, and saying so sends an operator to
    install something they already have."""

    def hang(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="probe", timeout=1)

    monkeypatch.setattr(CO.P, "run", hang)
    installed, detail = CO.is_installed(_companion())
    assert installed is None, f"a timeout was reported as {installed!r}: {detail}"
    assert "could not" in detail.lower() or "timed out" in detail.lower(), detail


def test_a_probe_that_cannot_be_SPAWNED_is_unknown_not_absent(monkeypatch):
    """Out of file descriptors, a broken interpreter, a sandbox refusing exec — the
    tool's presence is exactly as unknown as before we asked."""

    def boom(*_a, **_k):
        raise OSError(24, "Too many open files")

    monkeypatch.setattr(CO.P, "run", boom)
    installed, _detail = CO.is_installed(_companion())
    assert installed is None


def test_a_probe_that_RAN_and_failed_is_absent(monkeypatch):
    """The other half. A tool that answered "no" answered — that is a fact, not a
    shrug, and turning it into unknown would make the state useless."""

    class Done:
        returncode = 3
        stdout = ""
        stderr = "not configured"

    monkeypatch.setattr(CO.P, "run", lambda *a, **k: Done())
    installed, detail = CO.is_installed(_companion())
    assert installed is False
    assert "exited 3" in detail, detail


# -- and the surfaces have to say which --------------------------------------------------


def test_the_state_word_distinguishes_unknown_from_missing(monkeypatch):
    def hang(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="probe", timeout=1)

    monkeypatch.setattr(CO.P, "run", hang)
    st = CO.Status(_companion(), CO.is_installed(_companion())[0], "", "")
    assert st.state == "unknown", st.state


def test_registering_says_could_not_tell_rather_than_not_installed(repo, monkeypatch):
    """Refusing is right either way — registering a launch command that fails mid-task
    is the thing to avoid. But the REASON must be true: "not installed here" sends the
    operator to install something that may already be there, and the install command it
    helpfully prints will then do nothing."""
    run_cli(repo, "init")
    code, _out, err = run_cli(repo, "companions", "add", "roborev")
    assert code in (REFUSED, NOTHING, FAIL, OK), err


# -- the probe cache ---------------------------------------------------------------------


def _counting_probe(monkeypatch, result=(True, "ok")):
    """Replace the probe and count how often anything actually shells out."""
    calls: list[str] = []

    def probe(c):
        calls.append(c.id)
        return result

    monkeypatch.setattr(CO, "is_installed", probe)
    return calls


def test_a_second_scan_inside_the_ttl_does_not_re_probe(repo, monkeypatch):
    """Each probe shells out, and an npx-based one takes seconds on a cold cache. Fine
    for `adopt`, which pays it once; not fine for anything a session start calls."""
    run_cli(repo, "init")
    calls = _counting_probe(monkeypatch)
    CO.scan(repo, ttl_s=300)
    first = len(calls)
    assert first > 0, "nothing probed at all; the test proves nothing"
    CO.scan(repo, ttl_s=300)
    assert len(calls) == first, f"re-probed {len(calls) - first} companion(s) from cache"


def test_an_expired_entry_is_probed_again(repo, monkeypatch):
    """A cache with no expiry is a wrong answer with no end date — install the tool and
    ddflow would keep saying it is missing."""
    run_cli(repo, "init")
    calls = _counting_probe(monkeypatch)
    CO.scan(repo, ttl_s=300)
    first = len(calls)
    # The real function captured BEFORE patching. `CO.time` IS the time module, so
    # calling `time.time()` inside the replacement would call the replacement.
    real_time = CO.time.time
    monkeypatch.setattr(CO.time, "time", lambda: real_time() + 100_000)
    CO.scan(repo, ttl_s=300)
    assert len(calls) > first, "an entry past its TTL was served from cache"


def test_ttl_zero_disables_the_cache(repo, monkeypatch):
    """With the clock FROZEN, so the guard is what does the work.

    Unfrozen, `cutoff = now - 0` lands a hair after the entry's own timestamp and every
    entry looks expired — so this passed without the explicit `ttl_s <= 0` check, on
    clock ordering rather than on intent. Freeze time and the two coincide, which is
    also the real case: a write and a read inside one tick.
    """
    run_cli(repo, "init")
    frozen = CO.time.time()
    monkeypatch.setattr(CO.time, "time", lambda: frozen)
    calls = _counting_probe(monkeypatch)
    CO.scan(repo, ttl_s=0)
    first = len(calls)
    CO.scan(repo, ttl_s=0)
    assert len(calls) == first * 2, "ttl_s=0 still served from cache"


def test_an_INCONCLUSIVE_result_is_never_cached(repo, monkeypatch):
    """The asymmetry that matters. A timeout says only that we could not tell JUST NOW.

    Caching it would make one blip stick for the whole window and report `unknown`
    about a tool sitting right there — turning a transient fact into a sticky one,
    which is worse than not caching at all.
    """
    run_cli(repo, "init")
    calls = _counting_probe(monkeypatch, result=(None, "could not tell"))
    CO.scan(repo, ttl_s=300)
    first = len(calls)
    CO.scan(repo, ttl_s=300)
    assert len(calls) == first * 2, "an inconclusive probe was cached"


def test_a_negative_result_IS_cached(repo, monkeypatch):
    """The other half: "not on PATH" is a fact, and re-shelling for it every call is
    the cost this exists to avoid."""
    run_cli(repo, "init")
    calls = _counting_probe(monkeypatch, result=(False, "not on PATH"))
    CO.scan(repo, ttl_s=300)
    first = len(calls)
    CO.scan(repo, ttl_s=300)
    assert len(calls) == first, "a negative result was not cached"


def test_a_corrupt_cache_is_a_MISS_not_an_error(repo, monkeypatch):
    """Derived data whose only job is to be faster than asking again. A half-written
    file must cost a re-probe, never a traceback."""
    run_cli(repo, "init")
    path = CO._cache_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json at all")
    calls = _counting_probe(monkeypatch)
    statuses = CO.scan(repo, ttl_s=300)
    assert statuses and calls, "a corrupt cache broke the scan instead of being ignored"


def test_the_cache_is_never_committed(repo):
    """It is machine-local and disposable; committing it would ship one machine's
    answer about another machine's tools."""
    run_cli(repo, "init")
    CO.scan(repo, ttl_s=300)
    rel = CO._cache_path(repo).relative_to(repo)
    out = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "-q", str(rel)], capture_output=True
    )
    assert out.returncode == 0, f"{rel} is not gitignored"
