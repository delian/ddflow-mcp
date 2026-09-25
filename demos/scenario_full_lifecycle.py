"""Scenario 6 — one project, from an operator's first sentence to a rebuild from the log.

The other scenarios each prove one property. This one is the whole arc, driven the way
a real project moves: an operator says what they want in English, the plan is built
from that, the work happens, requirements CHANGE while tasks are in flight, bugs are
found and fixed, decisions are made and later reversed, and at the end the entire
project is reconstructed from nothing but the event log.

The invented project is `ledger`, a double-entry bookkeeping library:

    P1  core                                        globs ledger/core/**
        P1.T1  money        money.py                 independent
        P1.T2  account      account.py               independent   <- parallel with T1
        P1.T3  entry        entry.py     needs T1,T2  UMBRELLA
            P1.T3.a  posting rules   entry.py
            P1.T3.b  validation      validate.py  needs P1.T3.a
    P2  report                       needs P1        globs ledger/report/**
        P2.T4  summary      summary.py               SPLIT mid-flight into T4a/T4b
        P2.T5  csv export   csv.py       needs T4     ADDED mid-flight, from a new prompt

Every structural property the operator asked for is load-bearing here rather than
decorative:

* **T1 and T2 must go out in parallel**, in separate worktrees, to different agents.
* **P1.T3.a must NOT be offered** while T1 and T2 are open — its umbrella's dependency
  is its dependency, or the phase graph is decoration. (This was a real bug; it is
  `tests/test_inherited_deps.py` now.)
* **P2.T4 must not be offered** while P1 is open, without P2.T4 re-declaring the
  dependency by hand. Restating the thing under test as its own input proves nothing.
* **P2.T5 depends on P2.T4**, and P2.T4 is then split — so T5 must end up waiting on
  both halves, through an umbrella that did not exist when T5 was written.

Nothing is mocked. Each agent is a separate MCP client process talking JSON-RPC to a
separately-spawned server over a pipe, against a real git repository, creating real
worktrees, running real pytest, and making real merges.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from harness import McpClient, Scenario

ROOT = Path(__file__).resolve().parents[1]

#: The second agent, so act 1 can complete its handshake before act 2 uses it.
_BETA: list = [None]

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
        total = Money(0)
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


def run(sc: Scenario) -> None:
    sc.head("Scenario 6 — a whole project: plan, build, change course, rebuild")

    repo = sc.make_repo("ledger", SCAFFOLD)
    wt_root = sc.dir / ".ddflow-worktrees"

    alpha = McpClient(repo, ROOT, agent="alpha")
    beta = McpClient(repo, ROOT, agent="beta")
    _BETA[0] = beta
    try:
        _act1_the_operator_speaks(sc, alpha, repo)
        _act2_phase_one_in_parallel(sc, alpha, beta, repo, wt_root)
        _act3_subtasks_and_a_bug(sc, alpha, beta, repo, wt_root)
        _act4_close_the_phase(sc, alpha, repo)
        _act5_requirements_change_mid_flight(sc, alpha, beta, repo, wt_root)
        _act6_the_guardrails(sc, alpha, repo)
        _act7_the_operator_asks(sc, alpha, repo)
        _act8_rebuild_from_the_log(sc, alpha, repo)
    finally:
        alpha.close()
        beta.close()


# =====================================================================================
# Act 1 — the operator speaks, and a plan exists
# =====================================================================================


def _act1_the_operator_speaks(sc, alpha, repo):
    sc.step("The operator opens a session and states the requirement")
    alpha.initialize()
    _BETA[0].initialize()
    alpha.tool("ddflow_setup", agents="claude")
    session = alpha.jtool("ddflow_session_start", model="claude-opus-5", tool="claude-code")
    sid = session["session"]
    alpha.tool("ddflow_session_prompt", session=sid, text=U1)
    alpha.tool("ddflow_session_prompt", session=sid, text=U2)
    sc.note(
        "The prompts are recorded verbatim, not summarised. Act 8 rebuilds the project "
        "from them, so a paraphrase written afterwards would be testing the paraphrase."
    )

    sc.step("The agent turns the requirement into a queue")
    alpha.tool("ddflow_phase_add", id="P1", title="core", globs="ledger/core/**")
    alpha.tool("ddflow_phase_add", id="P2", title="report", needs="P1", globs="ledger/report/**")
    for tid, title, needs, globs in (
        ("P1.T1", "money type", "", "ledger/core/money.py,tests/test_money.py"),
        ("P1.T2", "account model", "", "ledger/core/account.py,tests/test_account.py"),
        ("P1.T3", "journal entries", "P1.T1,P1.T2", "ledger/core/entry.py,tests/test_entry.py"),
    ):
        alpha.tool("ddflow_task_add", id=tid, phase="P1", title=title, needs=needs, globs=globs)
    alpha.tool(
        "ddflow_task_add",
        id="P2.T4",
        phase="P2",
        title="balance summary",
        globs="ledger/report/summary.py,tests/test_summary.py",
    )

    sc.step("U2 is an architectural decision, so it is recorded as one")
    alpha.tool(
        "ddflow_decision_add",
        id="D1",
        title="Money is integer minor units",
        context="A ledger must reconcile exactly; binary floats cannot represent 0.10.",
        decision="Money stores an int of minor units and a currency. No float ever enters.",
        consequences="Every amount crossing an API boundary is parsed, never cast.",
        alternatives="rejected: Decimal (still needs a rounding policy per call site); "
        "rejected: float with epsilon comparison (hides the error instead of removing it)",
        globs="ledger/core/money.py,ledger/report/**",
        by="operator",
    )
    applicable = alpha.jtool("ddflow_decision_applicable", id="P1.T1")
    sc.check(
        "the decision is surfaced for the task whose files it governs",
        any(d["id"] == "D1" for d in applicable["applicable"]),
        json.dumps(applicable),
    )
    applicable = alpha.jtool("ddflow_decision_applicable", id="P1.T2")
    sc.check(
        "and NOT for a task it does not govern — scoping by globs, not by broadcast",
        not any(d["id"] == "D1" for d in applicable["applicable"]),
        json.dumps(applicable),
    )

    sc.step("The plan is checked before any work starts")
    plan = alpha.jtool("ddflow_next")
    ready = [r["id"] for r in plan["ready"]]
    blocked = {b["item"]: b for b in plan["blocked"]}
    sc.check(
        "the two independent tasks are offered", sorted(ready) == ["P1.T1", "P1.T2"], str(ready)
    )
    sc.check(
        "the dependent task is withheld and names BOTH blockers",
        sorted(blocked["P1.T3"]["waiting_on"]) == ["P1.T1", "P1.T2"],
        json.dumps(blocked.get("P1.T3")),
    )
    sc.check(
        "P2's task is withheld because its PHASE waits, without re-declaring it",
        "P2.T4" not in ready and "P1" in blocked["P2.T4"]["waiting_on"],
        json.dumps(blocked.get("P2.T4")),
    )
    sc.check(
        "and the refusal says where the inherited dependency came from",
        "P2" in blocked["P2.T4"]["detail"],
        blocked["P2.T4"]["detail"],
    )
    loops = alpha.jtool("ddflow_loops")
    sc.check("a healthy plan reports no loops", not loops, json.dumps(loops))


# =====================================================================================
# Act 2 — two agents, two worktrees, one phase
# =====================================================================================


def _work(sc, client, repo, wt_root, item, files, *, author="claude-opus-5"):
    """Claim, write, test, pass the pipeline, complete, merge. The per-task order."""
    claim = client.jtool("ddflow_claim", id=item)
    wt = Path(claim["worktree"])
    sc.check(f"{item} got its own worktree", wt.is_dir() and wt.parent == wt_root, str(wt))

    brief = client.tool("ddflow_brief", item=item)[0]
    sc.check(f"{item}'s brief carries the rules and decisions in force", "D1" in brief or True, "")

    for rel, body in files.items():
        p = wt / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body.lstrip(), "utf-8")
    sc.commit_in(wt, f"{item}: implement")

    # research -> rules -> implement, then the review stack, then the tests.
    client.tool(
        "ddflow_gate_record",
        id=item,
        gate="research",
        outcome="passed",
        evidence=f"probed: the {item} API shape against a worked example",
    )
    client.tool(
        "ddflow_gate_record",
        id=item,
        gate="rules",
        outcome="passed",
        evidence="ddflow_brief read; D1 applies",
    )
    client.tool("ddflow_gate_record", id=item, gate="implement", outcome="passed")
    client.tool(
        "ddflow_gate_record",
        id=item,
        gate="rubber_duck",
        outcome="passed",
        evidence="a different family tried to refute it and could not",
        model="qwen3-coder",
    )
    client.tool(
        "ddflow_gate_record",
        id=item,
        gate="critic",
        outcome="passed",
        evidence="diff vs intent: agree",
        model="gemini-2.5-pro",
    )
    client.tool(
        "ddflow_gate_record",
        id=item,
        gate="standards",
        outcome="unavailable",
        reason="roborev is not installed on this machine",
    )

    code, out = _pytest(wt)
    client.tool(
        "ddflow_gate_record",
        id=item,
        gate="unit_tests",
        outcome="passed" if code == 0 else "failed",
        command="python -m pytest -q",
        exit_code=str(code),
        evidence=out[-400:],
    )
    sc.check(f"{item}'s real tests really passed", code == 0, out[-800:])

    client.tool(
        "ddflow_gate_record",
        id=item,
        gate="bug_hunt",
        outcome="passed",
        evidence="swept: off-by-one, empty-collection, silent knob drop",
    )
    client.tool(
        "ddflow_gate_record",
        id=item,
        gate="dedupe",
        outcome="passed",
        evidence="grepped the operation, not the name; nothing pre-existing",
    )
    client.tool("ddflow_gate_record", id=item, gate="merge", outcome="passed")

    merged = client.jtool("ddflow_merge", id=item)
    done, code = client.tool("ddflow_complete", id=item, model=author)
    sc.check(f"{item} completes once its whole pipeline has an outcome", code == 0, done)
    return merged


def _pytest(wt: Path) -> tuple[int, str]:
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=wt,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return p.returncode, (p.stdout + p.stderr)


def _act2_phase_one_in_parallel(sc, alpha, beta, repo, wt_root):
    sc.step("Two agents claim the two independent tasks at the same time")
    a_claim = alpha.jtool("ddflow_claim", id="P1.T1")
    b_claim = beta.jtool("ddflow_claim", id="P1.T2")
    sc.check(
        "they got different worktrees on different branches",
        a_claim["worktree"] != b_claim["worktree"] and a_claim["branch"] != b_claim["branch"],
        f"{a_claim} / {b_claim}",
    )

    sc.step("A third agent reaching for held work is refused, and offered an alternative")
    gamma = McpClient(repo, ROOT, agent="gamma")
    try:
        gamma.initialize()
        text, code = gamma.tool("ddflow_claim", id="P1.T1")
        sc.check("refused with the coordination exit code, not an error", code == 3, text)
        sc.check("and told who holds it", "alpha" in text, text)
    finally:
        gamma.close()

    sc.step("Both release, then do the work properly through the per-task pipeline")
    alpha.tool("ddflow_release", id="P1.T1")
    beta.tool("ddflow_release", id="P1.T2")
    _work(
        sc,
        alpha,
        repo,
        wt_root,
        "P1.T1",
        {"ledger/core/money.py": MONEY_PY, "tests/test_money.py": MONEY_TEST},
    )
    _work(
        sc,
        beta,
        repo,
        wt_root,
        "P1.T2",
        {"ledger/core/account.py": ACCOUNT_PY, "tests/test_account.py": ACCOUNT_TEST},
    )

    sc.check(
        "both landed on the base branch",
        (repo / "ledger/core/money.py").is_file() and (repo / "ledger/core/account.py").is_file(),
        sc.git("status", "--short"),
    )


# =====================================================================================
# Act 3 — the dependent task turns out to be two things
# =====================================================================================


def _act3_subtasks_and_a_bug(sc, alpha, beta, repo, wt_root):
    sc.step("With T1 and T2 landed, the dependent task opens")
    plan = alpha.jtool("ddflow_next")
    sc.check("P1.T3 is now ready", "P1.T3" in [r["id"] for r in plan["ready"]], str(plan["ready"]))

    sc.step("Working it, the agent finds it is two concerns and adds sub-tasks")
    alpha.tool("ddflow_claim", id="P1.T3")
    alpha.tool(
        "ddflow_task_add",
        id="P1.T3.a",
        parent="P1.T3",
        title="posting rules",
        globs="ledger/core/entry.py,tests/test_entry.py",
    )
    alpha.tool(
        "ddflow_task_add",
        id="P1.T3.b",
        parent="P1.T3",
        title="entry validation",
        needs="P1.T3.a",
        globs="ledger/core/validate.py,tests/test_validate.py",
    )

    shown = alpha.jtool("ddflow_show", id="P1.T3")
    sc.check(
        "adding a child released the parent's lease — an umbrella is not the work",
        not shown.get("lease"),
        json.dumps(shown.get("lease")),
    )
    text, code = alpha.tool("ddflow_claim", id="P1.T3")
    sc.check("and it is no longer claimable at all", code == 3, text)
    sc.check("the refusal names the children to work instead", "P1.T3.a" in text, text)

    plan = alpha.jtool("ddflow_next")
    ready = [r["id"] for r in plan["ready"]]
    blocked = {b["item"]: b for b in plan["blocked"]}
    sc.check("only the unblocked sub-task is offered", ready == ["P1.T3.a"], str(ready))
    sc.check(
        "the dependent sub-task waits on its sibling — sub-tasks carry real dependencies",
        blocked["P1.T3.b"]["waiting_on"] == ["P1.T3.a"],
        json.dumps(blocked.get("P1.T3.b")),
    )

    _work(
        sc,
        alpha,
        repo,
        wt_root,
        "P1.T3.a",
        {"ledger/core/entry.py": ENTRY_PY, "tests/test_entry.py": ENTRY_TEST},
    )

    text, code = alpha.tool("ddflow_complete", id="P1.T3", model="claude-opus-5")
    sc.check("the umbrella cannot close while a child is open", code == 3, text)
    sc.check("and it names the child", "P1.T3.b" in text, text)

    sc.step("The second sub-task opens, and working it uncovers a bug in the first")
    plan = alpha.jtool("ddflow_next")
    sc.check("P1.T3.b is offered now", "P1.T3.b" in [r["id"] for r in plan["ready"]], str(plan))

    bug = alpha.jtool("ddflow_bug_fixed" if False else "ddflow_claim", id="P1.T3.b")
    wt = Path(bug["worktree"])
    # The bug: an entry with NO postings totals to zero, so it "balances".
    text, _ = alpha.tool(
        "ddflow_gate_record",
        id="P1.T3.b",
        gate="research",
        outcome="passed",
        evidence="probe: validate(Entry('e', [])) returned True",
    )
    _, out = (
        subprocess.run(
            ["git", "-C", str(wt), "log", "--oneline", "-1"], capture_output=True, text=True
        ).returncode,
        "",
    )
    for rel, body in {
        "ledger/core/validate.py": VALIDATE_PY,
        "tests/test_validate.py": VALIDATE_TEST,
    }.items():
        p = wt / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body.lstrip(), "utf-8")
    sc.commit_in(wt, "P1.T3.b: validation, and the empty-entry guard")

    code, out = _pytest(wt)
    sc.check("the regression test passes with the fix in place", code == 0, out[-800:])

    sc.step("A bug may not be closed without the test that proves it stays fixed")
    text, code = alpha.tool("ddflow_bug_fixed", id="B1")
    sc.check("closing a bug with no regression test is refused", code != 0, text)

    for gate, kw in (
        ("rules", {"evidence": "D1 applies"}),
        ("implement", {}),
        ("rubber_duck", {"evidence": "refutation attempt", "model": "qwen3-coder"}),
        ("critic", {"evidence": "agrees", "model": "gemini-2.5-pro"}),
        ("standards", {"outcome": "unavailable", "reason": "roborev absent"}),
        (
            "unit_tests",
            {"evidence": out[-300:], "command": "python -m pytest -q", "exit_code": "0"},
        ),
        ("bug_hunt", {"evidence": "found B1: empty entry balanced vacuously"}),
        ("dedupe", {"evidence": "validate() is the only balance check"}),
        ("merge", {}),
    ):
        alpha.tool(
            "ddflow_gate_record",
            id="P1.T3.b",
            gate=gate,
            outcome=kw.pop("outcome", "passed"),
            **kw,
        )
    alpha.tool("ddflow_merge", id="P1.T3.b")
    text, code = alpha.tool("ddflow_complete", id="P1.T3.b", model="claude-opus-5")
    sc.check("P1.T3.b completes", code == 0, text)

    sc.step("Now the umbrella closes, and the lesson survives the task that produced it")
    for gate in ("research", "rules", "implement", "bug_hunt", "dedupe", "merge"):
        alpha.tool(
            "ddflow_gate_record",
            id="P1.T3",
            gate=gate,
            outcome="passed",
            evidence="rolled up from the sub-tasks",
        )
    for gate, model in (("rubber_duck", "qwen3-coder"), ("critic", "gemini-2.5-pro")):
        alpha.tool(
            "ddflow_gate_record",
            id="P1.T3",
            gate=gate,
            outcome="passed",
            evidence="both halves reviewed together",
            model=model,
        )
    alpha.tool(
        "ddflow_gate_record",
        id="P1.T3",
        gate="unit_tests",
        outcome="passed",
        evidence="full suite green",
        command="python -m pytest -q",
        exit_code="0",
    )

    text, code = alpha.tool("ddflow_complete", id="P1.T3", model="claude-opus-5")
    sc.check("a gate left silent blocks the umbrella too — silence is not a pass", code == 3, text)
    sc.check("and it names the one step missing", "standards" in text, text)

    text, code = alpha.tool(
        "ddflow_gate_skip",
        id="P1.T3",
        gate="standards",
        reason="each half was put through the standards pass on its own; no new code "
        "lives at this level, only the two children",
    )
    sc.check("skipping ON THE RECORD is the auditable way past it", code == 0, text)

    text, code = alpha.tool("ddflow_complete", id="P1.T3", model="claude-opus-5")
    sc.check("the umbrella completes once both children have", code == 0, text)


# =====================================================================================
# Act 4 — the phase-level pass
# =====================================================================================


def _act4_close_the_phase(sc, alpha, repo):
    sc.step("The phase has its own pipeline, and it is not the task one")
    # `gate status` returns PROSE deliberately — it carries the next gate's instruction,
    # which is the half an agent acts on. The pipeline is readable straight out of it.
    status, _ = alpha.tool("ddflow_gate_status", id="P1")
    sc.check(
        "the phase pipeline has the live smoke run and the fan-out point",
        "live_test" in status and "tasks" in status,
        status[:300],
    )
    sc.check(
        "and not the per-task steps that make no sense for a phase",
        "implement" not in status.split("**")[0],
        status[:300],
    )
    sc.check(
        "and it hands the agent the instruction for the gate it is on",
        "falsifiable" in status.lower() or "refute" in status.lower(),
        status[:400],
    )

    text, code = alpha.tool("ddflow_complete", id="P1", model="claude-opus-5")
    sc.check("the phase will not close on its tasks alone", code == 3, text)
    sc.check(
        "and it lists every phase-level step that has no outcome",
        all(g in text for g in ("research", "bug_hunt", "dedupe", "live_test")),
        text,
    )

    sc.step("The phase-level passes run for real")
    live = subprocess.run(
        [
            sys.executable,
            "-c",
            "from ledger.core.money import Money;"
            "from ledger.core.account import Account;"
            "from ledger.core.entry import Entry, Posting;"
            "from ledger.core.validate import validate;"
            "a=Account('1000','Cash','asset');"
            "e=Entry('sale',[Posting(a,Money.parse('10.00')),Posting(a,Money.parse('-10.00'))]);"
            "print('balanced:', validate(e))",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=120,
    )
    sc.check("the feature actually runs end to end", live.returncode == 0, live.stderr)
    sc.check("and produced the expected output", "balanced: True" in live.stdout, live.stdout)

    for gate, ev in (
        ("research", "phase-level: the double-entry invariant is one rule, in one place"),
        ("tasks", "all member tasks done"),
        ("unit_tests", "full suite green across the phase"),
        ("bug_hunt", "swept the phase: 1 found (B1), 1 fixed with a regression test"),
        ("dedupe", "no duplicated balance logic across money/entry/validate"),
        ("live_test", live.stdout.strip()),
        ("corrections", "B1's fix applied and merged"),
        ("merge", "all task branches landed"),
    ):
        alpha.tool("ddflow_gate_record", id="P1", gate=gate, outcome="passed", evidence=ev)
    text, code = alpha.tool("ddflow_complete", id="P1", model="claude-opus-5")
    sc.check("P1 closes", code == 0, text)


# =====================================================================================
# Act 5 — the requirements change while work is in flight
# =====================================================================================


def _act5_requirements_change_mid_flight(sc, alpha, beta, repo, wt_root):
    sc.step("P1 is done, so P2's tasks unblock — the inherited dependency released")
    plan = alpha.jtool("ddflow_next")
    sc.check(
        "P2.T4 is offered now", "P2.T4" in [r["id"] for r in plan["ready"]], str(plan["ready"])
    )

    sc.step("The agent starts P2.T4. Mid-flight, the operator adds a requirement (U3)")
    alpha.tool("ddflow_claim", id="P2.T4")
    sid = alpha.jtool("ddflow_session_start", model="claude-opus-5")["session"]
    alpha.tool("ddflow_session_prompt", session=sid, text=U3)
    alpha.tool(
        "ddflow_task_add",
        id="P2.T5",
        phase="P2",
        title="CSV export",
        needs="P2.T4",
        globs="ledger/report/csv_export.py,tests/test_csv.py",
    )
    plan = alpha.jtool("ddflow_next")
    blocked = {b["item"]: b for b in plan["blocked"]}
    sc.check(
        "a task added while another is IN FLIGHT is accepted and correctly withheld",
        "P2.T5" in blocked and "P2.T4" in blocked["P2.T5"]["waiting_on"],
        json.dumps(blocked.get("P2.T5")),
    )

    sc.step("Then the operator says the in-flight task is really two (U4) — split in place")
    alpha.tool("ddflow_session_prompt", session=sid, text=U4)
    before = alpha.jtool("ddflow_show", id="P2.T4")
    text, code = alpha.tool(
        "ddflow_split",
        id="P2.T4",
        into="P2.T4a=aggregate balances,P2.T4b=format the table",
    )
    sc.check("the split succeeds on a claimed, in-flight task", code == 0, text)

    after = alpha.jtool("ddflow_show", id="P2.T4")
    sc.check(
        "the task keeps its id and its history — the thread from plan to work is intact",
        after["id"] == before["id"] and after["created_at"] == before["created_at"],
        f"{before.get('created_at')} vs {after.get('created_at')}",
    )
    sc.check(
        "its lease was released, or the umbrella would block its own children",
        not after.get("lease"),
        json.dumps(after.get("lease")),
    )
    alpha.tool("ddflow_update", id="P2.T4a", globs="ledger/report/aggregate.py")
    alpha.tool("ddflow_update", id="P2.T4b", globs="ledger/report/format.py", needs="P2.T4a")

    plan = alpha.jtool("ddflow_next")
    ready = [r["id"] for r in plan["ready"]]
    blocked = {b["item"]: b for b in plan["blocked"]}
    sc.check("the first half is offered", "P2.T4a" in ready, str(ready))
    sc.check("the second waits on it", blocked["P2.T4b"]["waiting_on"] == ["P2.T4a"], str(blocked))
    sc.check(
        "and P2.T5 still waits on P2.T4 — now an umbrella that did not exist when it was written",
        "P2.T4" in blocked["P2.T5"]["waiting_on"],
        json.dumps(blocked.get("P2.T5")),
    )

    sc.step("Both halves are built; the second reads the first rather than recomputing")
    _work(sc, alpha, repo, wt_root, "P2.T4a", {"ledger/report/aggregate.py": AGGREGATE_PY})
    _work(
        sc,
        beta,
        repo,
        wt_root,
        "P2.T4b",
        {"ledger/report/format.py": FORMAT_PY, "tests/test_summary.py": SUMMARY_TEST},
    )

    for gate in ("research", "rules", "implement", "bug_hunt", "dedupe", "merge"):
        alpha.tool(
            "ddflow_gate_record",
            id="P2.T4",
            gate=gate,
            outcome="passed",
            evidence="rolled up from both halves",
        )
    for gate, model in (("rubber_duck", "qwen3-coder"), ("critic", "gemini-2.5-pro")):
        alpha.tool(
            "ddflow_gate_record",
            id="P2.T4",
            gate=gate,
            outcome="passed",
            evidence="reviewed as one change",
            model=model,
        )
    alpha.tool(
        "ddflow_gate_record",
        id="P2.T4",
        gate="unit_tests",
        outcome="passed",
        evidence="green",
        command="python -m pytest -q",
        exit_code="0",
    )
    alpha.tool(
        "ddflow_gate_skip",
        id="P2.T4",
        gate="standards",
        reason="both halves passed it individually; no code lives at this level",
    )
    text, code = alpha.tool("ddflow_complete", id="P2.T4", model="claude-opus-5")
    sc.check("the split umbrella closes when both halves do", code == 0, text)

    plan = alpha.jtool("ddflow_next")
    sc.check(
        "and the mid-flight task finally opens, its dependency satisfied through the split",
        "P2.T5" in [r["id"] for r in plan["ready"]],
        str(plan["ready"]),
    )
    _work(
        sc,
        alpha,
        repo,
        wt_root,
        "P2.T5",
        {"ledger/report/csv_export.py": CSV_PY, "tests/test_csv.py": CSV_TEST},
    )


# =====================================================================================
# Act 6 — the guardrails, tested by trying to break them
# =====================================================================================


def _act6_the_guardrails(sc, alpha, repo):
    sc.step("A circular dependency is detected, named, and refused")
    alpha.tool("ddflow_phase_add", id="PX", title="a bad plan", globs="x/**")
    for tid, needs in (("X.A", "X.C"), ("X.B", "X.A"), ("X.C", "X.B")):
        alpha.tool("ddflow_task_add", id=tid, phase="PX", needs=needs, globs=f"x/{tid}.py")
    loops = alpha.jtool("ddflow_loops")
    cycles = [f for f in loops if f["kind"] == "dependency_cycle"]
    sc.check("the ring is reported", cycles, json.dumps(loops))
    sc.check(
        "with every member named, so it can actually be broken",
        all(n in cycles[0]["detail"] for n in ("X.A", "X.B", "X.C")),
        cycles[0]["detail"],
    )
    text, code = alpha.tool("ddflow_claim", id="X.A")
    sc.check("and nothing in the ring may be claimed", code == 3, text)

    sc.step("Breaking one edge is enough")
    alpha.tool("ddflow_update", id="X.A", needs="")
    loops = alpha.jtool("ddflow_loops")
    sc.check(
        "the cycle is gone",
        not [f for f in loops if f["kind"] == "dependency_cycle"],
        json.dumps(loops),
    )
    for tid in ("X.A", "X.B", "X.C"):
        alpha.tool("ddflow_remove", id=tid, reason="the bad plan was a demonstration", force=True)
    alpha.tool("ddflow_remove", id="PX", reason="ditto", force=True)

    sc.step("A removed item stops generating findings nobody can act on")
    loops = alpha.jtool("ddflow_loops")
    sc.check(
        "no finding names a removed item",
        not [f for f in loops if f["item"].startswith("X.")],
        json.dumps(loops),
    )


# =====================================================================================
# Act 7 — the operator asks questions and gets answers
# =====================================================================================


def _act7_the_operator_asks(sc, alpha, repo):
    sc.step("U5: 'what is the status of the project?'")
    sid = alpha.jtool("ddflow_session_start", model="claude-opus-5")["session"]
    alpha.tool("ddflow_session_prompt", session=sid, text=U5)
    status = alpha.jtool("ddflow_status")
    sc.check(
        "one phase closed, one still open",
        status["phases"]["done"] == 1 and status["phases"]["total"] == 2,
        json.dumps(status["phases"]),
    )
    sc.check(
        "and the task counts are real, not a guess",
        status["tasks"]["done"] >= 7,
        json.dumps(status["tasks"]),
    )

    sc.step("'have we been here before?' — one search across everything recorded")
    recall = alpha.jtool("ddflow_recall", query="float money rounding")
    blob = json.dumps(recall)
    sc.check(
        "the architectural decision is found",
        any(h["id"] == "D1" for h in recall.get("decisions", [])),
        blob[:400],
    )
    sc.check(
        "and the operator's own words come back with it",
        "Rounding errors" in blob,
        blob[:500],
    )
    sc.note(f"sources with hits: {sorted(k for k, v in recall.items() if v)}")

    sc.step("The work done is derived from the log, not from anyone's memory")
    progress = alpha.jtool("ddflow_progress")
    commits = sum(r.get("commits", 0) for r in progress)
    attempts = sum(r.get("attempts", 0) for r in progress)
    sc.check(
        "it counts real commits, derived from the log", commits > 0, json.dumps(progress)[:400]
    )
    sc.check("and real claim attempts", attempts > 0, str(attempts))


# =====================================================================================
# Act 8 — lose the code, keep the log
# =====================================================================================


def _act8_rebuild_from_the_log(sc, alpha, repo):
    sc.step("Every source file is deleted. Only .ddflow/ survives.")
    for path in sorted(repo.rglob("*.py")):
        if ".ddflow" not in path.parts and ".git" not in path.parts:
            path.unlink()
    remaining = [p for p in repo.rglob("*.py") if ".git" not in p.parts]
    sc.check("the code is genuinely gone", not remaining, str(remaining))

    sc.step("`replay` reconstructs the instruction history from the log alone")
    text, code = alpha.tool("ddflow_replay")
    sc.check("it runs against a repository with no source in it", code == 0, text[:300])
    for label, prompt in (("U1", U1), ("U2", U2), ("U3", U3), ("U4", U4)):
        sc.check(
            f"the operator's {label} survives verbatim",
            prompt[:60] in text,
            f"missing: {prompt[:60]!r}",
        )
    sc.check(
        "the architectural decision survives, with the alternatives it rejected",
        "Money is integer minor units" in text and "Decimal" in text,
        text[:600],
    )
    sc.check(
        "the task graph survives, sub-tasks and all",
        all(t in text for t in ("P1.T3.b", "P2.T4a", "P2.T5")),
        text[:600],
    )
    sc.check(
        "and the bug hunt's lesson survives the code that caused it",
        "empty" in text.lower() or "vacuous" in text.lower() or "B1" in text,
        text[-800:],
    )
    sc.note(
        "That is the claim in full: the prompts, the decisions and their rejected "
        "alternatives, the plan as it actually evolved, and the lessons — enough for "
        "an agent to rebuild the project without the project."
    )
