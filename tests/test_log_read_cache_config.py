"""B86: the `[log] reuse_parsed` knob reaches the real read path and every call site."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ast

import pytest
from log_cache_support import _cold_cache, _no_version_stamp, _pkg_py_files, _uncached  # noqa: F401

from ddflow.config import Config, LogConfig
from ddflow.infra.log import EventLog, clear_parse_cache


def test_reuse_parsed_false_is_honoured_and_reaches_the_real_read_path(repo):
    """The dead-knob class: a knob that loads and changes nothing.

    Asserts against the CACHE, not against a return value — both settings return the
    same events, which is exactly why a parity assertion would pass either way.
    """
    from ddflow.infra import log as L

    off = _uncached(repo)
    off.append("task.added", "T1", {"title": "x", "kind": "task"})
    off.read_all()
    assert not L._PARSE_CACHE, "reuse_parsed=false still populated the cache"

    on = EventLog(repo, "a1", log_cfg=LogConfig(reuse_parsed=True))
    on.read_all()
    assert L._PARSE_CACHE, "reuse_parsed=true did not populate the cache"


def test_reuse_parsed_false_does_not_read_a_cache_another_reader_filled(repo):
    """The other half of the knob, and the one a parity assertion cannot see.

    Guarding only the WRITE side leaves a disabled reader still consulting an entry that
    an enabled one put there — so `reuse_parsed = false` would not actually disable the
    cache. Found by mutation M5: removing the read guard left every knob test green.

    Poisons the cache deliberately. The first assertion is the control arm proving the
    poison bites; without it this test could pass because the poison did nothing.
    """
    from ddflow.infra import log as L

    on = EventLog(repo, "a1", log_cfg=LogConfig(reuse_parsed=True))
    on.append("task.added", "T1", {"title": "x", "kind": "task"})
    assert {e.subject for e in on.read_all()} == {"T1"}

    entry = L._PARSE_CACHE[on.shard]
    # Keeps the DIGEST, so the validity check passes and the (empty) events are trusted.
    L._PARSE_CACHE[on.shard] = L._Parsed(entry.consumed, entry.digest, [], 0)
    assert on.read_all() == [], "the poison did not bite, so this test proves nothing"

    off = _uncached(repo)
    assert {e.subject for e in off.read_all()} == {"T1"}, (
        "reuse_parsed=false read from the cache anyway"
    )


def test_the_config_knob_is_honoured_by_the_real_entry_points(repo):
    """Drives the REAL commands, because the hand-built version proved nothing.

    The first version of this test was named for the real wiring and then built the log
    by hand — so it passed while three shipped call sites (`api.loops`, `api.progress`,
    the MCP obligation footer) constructed `EventLog(repo)` with no `log_cfg` and ignored
    `[log] reuse_parsed = false` entirely. Found by roborev on 8b3ce8f, after a subagent
    reviewer and the cross-family critic both missed it: a test that asserts the thing it
    is named for is the only thing that would have caught it.
    """
    from ddflow.api import reporting
    from ddflow.infra import log as L

    cfgpath = repo / ".ddflow" / "config.toml"
    cfgpath.parent.mkdir(parents=True, exist_ok=True)
    cfgpath.write_text("[log]\nreuse_parsed = false\n")
    cfg = Config.load(repo)
    assert cfg.log.reuse_parsed is False
    assert cfg.sources["log.reuse_parsed"] == "file"

    seed = EventLog(repo, "a1", log_cfg=LogConfig(reuse_parsed=False))
    seed.append("task.added", "T1", {"title": "x", "kind": "task"})

    def _footer() -> None:
        """The MCP obligation footer, the third shipped site that built a bare EventLog.

        Added because roborev mutation-tested the commit that fixed it and found only the
        AST ratchet went red — so the commit message's claim that "both the behavioural
        test and the ratchet go red" was true for the two API paths and false for this one.
        A static guard cannot see a `log_cfg` threaded to the wrong object.
        """
        from ddflow.surfaces import mcp as M

        server = M.Server(repo)
        server._calls_since_footer = 10_000
        server._last_footer_at = 0.0
        M._obligation_footer(server)

    # Both knobs, and the SAME real entry points for each. The =false direction passes
    # trivially when a call site hard-codes LogConfig(reuse_parsed=False), and a =true
    # check that hand-picks `reporting.loops` leaves every other entry point untested — so
    # only this loop sees a site that drops the =true knob (rubber_duck qwen3.8-flash
    # #2/#6: the added block sat outside the loop and hard-coded reporting.loops).
    entry_points = (
        ("api.loops", lambda: reporting.loops(repo)),
        ("api.progress", lambda: reporting.progress(repo)),
        ("mcp obligation footer", _footer),
    )
    for setting, want_cache in (("false", False), ("true", True)):
        cfgpath.write_text(f"[log]\nreuse_parsed = {setting}\n")
        for label, call in entry_points:
            clear_parse_cache()
            call()
            assert bool(L._PARSE_CACHE) is want_cache, (
                f"{label} ignored [log] reuse_parsed = {setting}"
            )


def event_log_calls_without_config(src: str) -> list[int]:
    """Line numbers of `EventLog(...)` constructions in ``src`` that omit `log_cfg`.

    Injectable so the detector can be SHOWN to work. A scan whose only input is the live
    tree passes identically once it has silently stopped looking — which is exactly what
    happened here: the first version matched only a bare `ast.Name`, so mutating the name
    it compares against changed nothing, because no qualified call existed to miss.
    """

    out = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        # `EventLog(...)` and `L.EventLog(...)` alike. roborev found the Attribute form
        # unguarded on 844bad2 while the docstring promised "every call site".
        func = node.func
        if isinstance(func, ast.Name):
            called = func.id
        elif isinstance(func, ast.Attribute):
            called = func.attr
        else:
            continue
        if called != "EventLog":
            continue
        # `log_cfg=None` falls back to `LogConfig()` exactly like omitting it, so it drops
        # the `[log]` knobs too. `EventLog.__init__` uses `log_cfg or LogConfig()`, so ANY
        # falsy literal keyed `log_cfg` is an offender, not a pass.
        if any(
            k.arg == "log_cfg" and not (isinstance(k.value, ast.Constant) and not k.value.value)
            for k in node.keywords
        ):
            continue
        out.append(node.lineno)
    return out


@pytest.mark.parametrize(
    ("src", "offends"),
    [
        ("EventLog(repo)", True),
        ("L.EventLog(repo)", True),
        ("infra_log.EventLog(repo, agent)", True),
        ('EventLog(repo, "a1", lock_timeout_s=5)', True),
        ("EventLog(repo, log_cfg=cfg.log)", False),
        ("L.EventLog(repo, log_cfg=cfg.log)", False),
        ('EventLog(repo, "a1", log_cfg=LogConfig(reuse_parsed=False))', False),
        # `log_cfg=None` falls back to LogConfig() like omitting it, so it drops the knobs.
        ("EventLog(repo, log_cfg=None)", True),
        ("L.EventLog(repo, log_cfg=None)", True),
        ("SomethingElse(repo)", False),
    ],
)
def test_the_call_site_detector_sees_both_call_forms(src, offends):
    assert bool(event_log_calls_without_config(src)) is offends, src


def test_the_pycache_skip_is_by_component_not_substring(tmp_path):
    """The whole-path `"__pycache__" in str(f)` skip once skipped every file when the
    checkout itself lived under a directory whose name contains `__pycache__`; the ratchet
    then passed on nothing. The skip must be a COMPONENT of the file's path inside the
    package, not a substring of the checkout path."""
    pkg = tmp_path / "fakecheckout__pycache__dir" / "ddflow"
    pkg.mkdir(parents=True)
    (pkg / "mod.py").write_text("EventLog(repo)", encoding="utf-8")
    assert (pkg / "mod.py") in _pkg_py_files(pkg), (
        "an `__pycache__` substring in an ancestor skipped the whole package"
    )


def test_every_event_log_call_site_passes_the_log_config():
    """A ratchet, because threading a knob through N call sites is how knobs get dropped.

    Three of the eight sites were missed on the first pass and the behavioural test could
    not see them. This one names the offender directly, and the allowlist may only shrink.
    """
    import pathlib as _p

    #: Call sites that legitimately take no `log_cfg`, each with its reason.
    allowed: dict[str, str] = {}

    pkg = _p.Path(__file__).resolve().parents[1] / "ddflow"
    offenders = []
    for f in _pkg_py_files(pkg):
        for line in event_log_calls_without_config(f.read_text("utf-8")):
            where = f"{f.relative_to(pkg.parent)}:{line}"
            if where not in allowed:
                offenders.append(where)
    assert not offenders, (
        "these EventLog call sites drop `[log]` config, so `reuse_parsed` and "
        f"`max_cached_events` silently do nothing there: {offenders}"
    )
