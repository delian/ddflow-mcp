"""The full-lifecycle demo's forwarded ledger example is internally consistent
(B-lifecycle-demo-inconsistencies): its `Entry.total()` seeds the total from the first
posting's currency instead of a USDA default, act 4 skips the docs gate without first
recording it as passed, and act 8's "code is genuinely gone" filter ignores `.ddflow`
exactly where the delete loop spares it."""

from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "demos"), str(ROOT)]


def _ledger_example():
    """exec the ledger example templates with a fake `ledger` package so
    `from ledger.core.money import Money` resolves, and return the classes."""
    import lifecycle_data as LD

    led = types.ModuleType("ledger")
    led.__dict__["__path__"] = []
    core = types.ModuleType("ledger.core")
    core.__dict__["__path__"] = []
    money = types.ModuleType("ledger.core.money")
    exec(LD.MONEY_PY, money.__dict__)
    sys.modules["ledger"] = led
    sys.modules["ledger.core"] = core
    sys.modules["ledger.core.money"] = money
    entry_ns = {"__builtins__": __builtins__}
    exec(LD.ENTRY_PY, entry_ns)
    return money.__dict__["Money"], entry_ns["Entry"], entry_ns["Posting"]


def test_entry_total_seeds_the_currency_not_usd():
    """`Entry.total()` must total a non-USD entry, not raise on a USD-dollar zero seed."""
    Money, Entry, Posting = _ledger_example()
    entry = Entry(
        "sale",
        [Posting("cash", Money(100, "EUR")), Posting("sales", Money(-100, "EUR"))],
    )
    assert entry.total() == Money(0, "EUR")


def test_act4_skips_docs_but_no_longer_passes_it():
    """act 4 deliberately skips docs; it must not also record it as passed first."""
    src = (ROOT / "demos" / "lifecycle_acts_change.py").read_text("utf-8")
    assert '("docs", "README documents' not in src
    assert '"ddflow_gate_skip"' in src and 'gate="docs"' in src


def test_act8_code_gone_filter_ignores_dotddflow():
    """The "code is genuinely gone" filter ignores `.ddflow` wherever the delete loop
    spares it, so a surviving `.ddflow` file is not reported as surviving code."""
    src = (ROOT / "demos" / "lifecycle_acts_finish.py").read_text("utf-8")
    line = next(l for l in src.splitlines() if "remaining = [" in l)
    assert '".ddflow" not in p.parts' in line
