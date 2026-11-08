"""Scenario 6 data: the invented `ledger` project's scaffold, the operator's words and the
source files the agents write. Pure constants; see scenario_full_lifecycle.py."""

from __future__ import annotations

SCAFFOLD = {
    ".gitignore": "__pycache__/\n*.pyc\n.pytest_cache/\n.ddflow-worktrees/\n",
    "README.md": "# ledger\n\nDouble-entry bookkeeping primitives.\n",
    "ledger/__init__.py": "",
    "ledger/core/__init__.py": "",
    "ledger/report/__init__.py": "",
    "tests/__init__.py": "",
}

# --- what the operator actually said, in order --------------------------------------
#
# Recorded verbatim as session prompts, because the claim being tested at the end is
# that the project can be rebuilt from these plus the decisions — so they have to be
# the real input, not a summary written afterwards.

U1 = (
    "Build me a small double-entry bookkeeping library. Phase one is the core: a money "
    "type, an account model, and journal entries on top of them. Phase two is a "
    "reporting layer that reads the core."
)
U2 = "Money must never be a float. Rounding errors in a ledger are unacceptable."
U3 = "While you're in the reporting phase, I also need CSV export. It reads the summary."
U4 = (
    "The summary task is doing two things — aggregating and formatting. Split it; "
    "the formatting can't start until the aggregation shape is settled."
)
U5 = "What's the status of the project? What's done and what's left?"

MONEY_PY = '''
"""Money as integer minor units. Never a float — see decision D1."""


class Money:
    __slots__ = ("minor", "currency")

    def __init__(self, minor, currency="USD"):
        if not isinstance(minor, int) or isinstance(minor, bool):
            raise TypeError("minor units must be an int; a float is the bug this guards")
        self.minor, self.currency = minor, currency

    @classmethod
    def parse(cls, text, currency="USD"):
        """'12.34' -> Money(1234). Rejects more precision than the currency has."""
        whole, _, frac = str(text).strip().partition(".")
        if len(frac) > 2:
            raise ValueError(f"{text!r} has more precision than {currency} does")
        sign = -1 if whole.startswith("-") else 1
        return cls(sign * (abs(int(whole)) * 100 + int((frac or "0").ljust(2, "0"))), currency)

    def __add__(self, other):
        if self.currency != other.currency:
            raise ValueError(f"cannot add {self.currency} to {other.currency}")
        return Money(self.minor + other.minor, self.currency)

    def __neg__(self):
        return Money(-self.minor, self.currency)

    def __eq__(self, other):
        return (
            isinstance(other, Money)
            and self.minor == other.minor
            and self.currency == other.currency
        )

    def __repr__(self):
        return f"Money({self.minor}, {self.currency!r})"
'''

MONEY_TEST = """
import pytest

from ledger.core.money import Money


def test_parses_two_decimal_places():
    assert Money.parse("12.34") == Money(1234)
    assert Money.parse("-0.05") == Money(-5)
    assert Money.parse("7") == Money(700)


def test_refuses_more_precision_than_the_currency_has():
    with pytest.raises(ValueError):
        Money.parse("1.234")


def test_refuses_a_float_outright():
    with pytest.raises(TypeError):
        Money(12.34)


def test_addition_is_currency_checked():
    assert Money(100) + Money(50) == Money(150)
    with pytest.raises(ValueError):
        Money(100, "USD") + Money(100, "EUR")
"""

ACCOUNT_PY = '''
"""Accounts, and which side of the book increases them."""

DEBIT_NORMAL = ("asset", "expense")
CREDIT_NORMAL = ("liability", "equity", "revenue")


class Account:
    def __init__(self, code, name, kind):
        if kind not in DEBIT_NORMAL + CREDIT_NORMAL:
            raise ValueError(f"unknown account kind {kind!r}")
        self.code, self.name, self.kind = code, name, kind

    @property
    def normal_side(self):
        return "debit" if self.kind in DEBIT_NORMAL else "credit"

    def __repr__(self):
        return f"Account({self.code!r}, {self.kind!r})"
'''

ACCOUNT_TEST = """
import pytest

from ledger.core.account import Account


def test_normal_side_follows_the_kind():
    assert Account("1000", "Cash", "asset").normal_side == "debit"
    assert Account("4000", "Sales", "revenue").normal_side == "credit"


def test_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        Account("9999", "Mystery", "vibes")
"""

ENTRY_PY = '''
"""Journal entries: a list of postings that must balance."""

from ledger.core.money import Money


class Posting:
    def __init__(self, account, amount):
        if not isinstance(amount, Money):
            raise TypeError("a posting amount is Money, never a number")
        self.account, self.amount = account, amount


class Entry:
    def __init__(self, description, postings):
        self.description, self.postings = description, list(postings)

    def total(self):
        if not self.postings:
            return Money(0)
        total = Money(0, self.postings[0].amount.currency)
        for p in self.postings:
            total = total + p.amount
        return total
'''

ENTRY_TEST = """
from ledger.core.account import Account
from ledger.core.entry import Entry, Posting
from ledger.core.money import Money


def test_a_balanced_entry_totals_to_zero():
    cash = Account("1000", "Cash", "asset")
    sales = Account("4000", "Sales", "revenue")
    e = Entry("a sale", [Posting(cash, Money(1000)), Posting(sales, Money(-1000))])
    assert e.total() == Money(0)
"""

VALIDATE_PY = '''
"""Entry validation, separated from the posting rules it checks."""

from ledger.core.money import Money


class Unbalanced(ValueError):
    pass


def validate(entry):
    """Raise unless the entry balances and has at least two postings."""
    if len(entry.postings) < 2:
        raise Unbalanced(f"{entry.description!r}: an entry needs at least two postings")
    if entry.total() != Money(0, entry.postings[0].amount.currency):
        raise Unbalanced(f"{entry.description!r} does not balance: {entry.total()}")
    return True
'''

VALIDATE_TEST = '''
import pytest

from ledger.core.account import Account
from ledger.core.entry import Entry, Posting
from ledger.core.money import Money
from ledger.core.validate import Unbalanced, validate


def _entry(*amounts):
    acct = Account("1000", "Cash", "asset")
    return Entry("e", [Posting(acct, Money(a)) for a in amounts])


def test_a_balanced_entry_validates():
    assert validate(_entry(100, -100)) is True


def test_an_unbalanced_entry_is_refused():
    with pytest.raises(Unbalanced):
        validate(_entry(100, -99))


def test_a_single_posting_is_refused():
    with pytest.raises(Unbalanced):
        validate(_entry(0))


def test_an_empty_entry_is_refused_rather_than_passing_vacuously():
    """The bug this file exists to pin: `sum([]) == 0` balances, so an entry with NO
    postings validated clean. An empty collection satisfying a check about its contents
    is the vacuous-truth class, and a ledger that accepts an empty journal entry has
    lost the one invariant it has."""
    with pytest.raises(Unbalanced):
        validate(_entry())
'''

AGGREGATE_PY = '''
"""Aggregation: entries in, balances per account out."""

from ledger.core.money import Money


def balances(entries):
    out = {}
    for e in entries:
        for p in e.postings:
            cur = out.get(p.account.code) or Money(0, p.amount.currency)
            out[p.account.code] = cur + p.amount
    return out
'''

FORMAT_PY = '''
"""Formatting: balances in, a human table out. Reads aggregate; never recomputes."""


def render(balances):
    lines = ["ACCOUNT    BALANCE"]
    for code in sorted(balances):
        m = balances[code]
        lines.append(f"{code:<10s} {m.minor / 100:>8.2f} {m.currency}")
    return "\\n".join(lines)
'''

SUMMARY_TEST = """
from ledger.core.account import Account
from ledger.core.entry import Entry, Posting
from ledger.core.money import Money
from ledger.report.aggregate import balances
from ledger.report.format import render


def _sale():
    cash = Account("1000", "Cash", "asset")
    sales = Account("4000", "Sales", "revenue")
    return Entry("a sale", [Posting(cash, Money(1000)), Posting(sales, Money(-1000))])


def test_balances_accumulate_per_account():
    b = balances([_sale(), _sale()])
    assert b["1000"] == Money(2000)
    assert b["4000"] == Money(-2000)


def test_render_is_sorted_and_stable():
    out = render(balances([_sale()]))
    assert out.splitlines()[1].startswith("1000")
    assert "10.00 USD" in out
"""

CSV_PY = '''
"""CSV export, built on the aggregation. One source of truth for the numbers."""

import csv
import io


def to_csv(balances):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\\n")
    w.writerow(["account", "minor_units", "currency"])
    for code in sorted(balances):
        m = balances[code]
        w.writerow([code, m.minor, m.currency])
    return buf.getvalue()
'''

CSV_TEST = '''
from ledger.core.account import Account
from ledger.core.entry import Entry, Posting
from ledger.core.money import Money
from ledger.report.aggregate import balances
from ledger.report.csv_export import to_csv


def test_csv_carries_minor_units_not_a_rendered_float():
    """Decision D1 reaches the export surface too: a CSV consumer must get the exact
    integer, not a string that has been through a float."""
    cash = Account("1000", "Cash", "asset")
    b = balances([Entry("x", [Posting(cash, Money(1000)), Posting(cash, Money(-1))])])
    rows = to_csv(b).splitlines()
    assert rows[0] == "account,minor_units,currency"
    assert rows[1] == "1000,999,USD"
'''
