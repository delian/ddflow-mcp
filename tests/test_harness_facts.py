"""HX-facts-core: one fact record, descriptor [facts] overlay, `ddflow harness facts`."""

from __future__ import annotations

import pytest

from ddflow.api.harness import harness_view
from ddflow.services import context_families as cf
from ddflow.services import facts, harnessfacts, harnessreg
from ddflow.services.harnessreg import DescriptorError, parse
from tests.test_harness_descriptor_v2 import BASE

HEAD = '[headless]\nargv = ["acme", "-p"]\nmodel_flag = "--model"\n'
OK = '[facts."headless.argv"]\ns = "verified"\nsrc = "https://x.example"\nd = "2026-10-30"\nver = "1.2.3"\n'


def p(extra: str):
    return parse(BASE + extra, "acme")


def test_one_vocabulary_shared_with_context_families():
    assert cf.STATUSES is facts.STATUSES and cf.READABLE is facts.READABLE
    assert "ver" in facts.FACT_KEYS and set(cf.FACT_KEYS) == set(facts.FACT_KEYS)


def test_fact_problems_name_each_defect():
    bad = facts.problems("x", {"s": "maybe", "src": "", "d": "yesterday", "zzz": 1})
    text = " ".join(bad)
    for word in ("status", "source", "date", "unknown field"):
        assert word in text
    assert facts.problems("x", {"s": "verified", "src": "a", "d": "2026-10-30", "ver": "1"}) == []
    assert facts.problems("x", {"s": "verified", "src": "a", "d": "2026-10-30", "ver": 1})


def test_overlay_parses_and_reads():
    h = p(HEAD + OK)
    assert h.facts["headless.argv"]["s"] == "verified"
    rows = {r["fact"]: r for r in harnessfacts.rows(h)}
    assert rows["headless.argv"]["status"] == "verified"
    assert rows["headless.argv"]["tool_version"] == "1.2.3"
    # no overlay: honest default, a lead, never believed
    assert rows["headless.model_flag"]["status"] == "unverified"
    assert rows["headless.model_flag"]["source"] == "https://example.com"


@pytest.mark.parametrize(
    "extra, word",
    [
        (HEAD + '[facts."headless.argv"]\nsrc = "a"\nd = "2026-10-30"\n', "status"),
        (HEAD + '[facts."headless.argv"]\ns = "bogus"\nsrc = "a"\nd = "2026-10-30"\n', "status"),
        (
            HEAD + '[facts."headless.nope"]\ns = "verified"\nsrc = "a"\nd = "2026-10-30"\n',
            "no such",
        ),
        ('[facts."headless.argv"]\ns = "verified"\nsrc = "a"\nd = "2026-10-30"\n', "no such"),
        (HEAD + '[facts."headless.argv"]\ns = "none"\nsrc = "a"\nd = "2026-10-30"\n', "none"),
        (
            '[headless]\nnone = true\n[facts.headless]\ns = "verified"\nsrc = "a"\nd = "2026-10-30"\n',
            "value",
        ),
    ],
)
def test_bad_overlay_is_an_error(extra, word):
    with pytest.raises(DescriptorError, match=word):
        p(extra)


def test_none_section_is_a_documented_absence_row():
    h = p('[headless]\nnone = true\n[facts.headless]\ns = "none"\nsrc = "a"\nd = "2026-10-30"\n')
    (row,) = [r for r in harnessfacts.rows(h) if r["fact"] == "headless"]
    assert row["status"] == "none" and row["value"] == "none"


def test_every_shipped_descriptor_fact_has_a_valid_status():
    for h in harnessreg.load():
        for r in harnessfacts.rows(h):
            assert r["status"] in facts.STATUSES, (h.id, r)
            assert r["source"] and r["date"], (h.id, r)


def test_absent_section_listed_only_when_asked():
    h = p(HEAD)
    assert not any(r["fact"] == "caps" for r in harnessfacts.rows(h))
    assert any(
        r["fact"] == "caps" and r["status"] == "unverified"
        for r in harnessfacts.rows(h, absent=True)
    )


def test_view_facts_table_filters(tmp_path):
    out = harness_view(tmp_path, what="facts", id="opencode")
    assert out.exit == 0 and "opencode" in out.data["message"]
    assert out.data["facts"] and all(r["agent"] == "opencode" for r in out.data["facts"])
    un = harness_view(tmp_path, what="facts", unverified=True)
    assert un.data["facts"] and all(r["status"] == "unverified" for r in un.data["facts"])
    bad = harness_view(tmp_path, what="facts", id="nope")
    assert bad.exit != 0


def test_table_cells_cannot_break_the_columns():
    h = p(HEAD + OK.replace("https://x.example", "a|b"))
    for line in harnessfacts.table(harnessfacts.rows(h)).splitlines():
        assert line.count("|") == 9


def test_falsy_value_is_a_fact_when_an_overlay_names_it():
    zero = '[caps]\nmcp_tools = 5\n[facts."caps.subagent_depth"]\ns = "verified"\nsrc = "a"\nd = "2026-10-30"\n'
    rows = {r["fact"]: r for r in harnessfacts.rows(p(zero))}
    assert rows["caps.subagent_depth"]["status"] == "verified"
    assert rows["caps.subagent_depth"]["value"] == "0"
