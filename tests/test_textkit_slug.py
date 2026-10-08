"""B-uni-textkit.2-slug: the one slug module returns, for every rule it replaced, exactly what
the replaced code returned. Each reference below is the original expression, kept verbatim."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ddflow.core import slug as S
from ddflow.core.flow import safe_name
from ddflow.services import docscheck as DC
from ddflow.services import importer as IM
from ddflow.services import importer_harness as IH

SAMPLES = [
    "",
    "-",
    "B-uni-x.1",
    "a/b c",
    "  Hello,  World!  ",
    "Phase 160 — the drafter",
    "The `rules`, and *where* it came",
    "[link](http://x) <b>bold</b> ~~s~~",
    "naïve ÉCOLE ß straße",
    "İstanbul",
    "x" * 120 + "-" + "y" * 10,
    "agent:claude-code/abc 123",
    "tab\there\nnewline",
    "__under__ and -- dashes",
    "日本語 text 123",
    "a" * 29 + "-" + "b" * 5,
]


@pytest.mark.parametrize("s", SAMPLES)
def test_safe_name_is_what_flow_always_returned(s):
    assert safe_name(s) == (re.sub(r"[^A-Za-z0-9._-]", "-", s).strip("-") or "item")


@pytest.mark.parametrize("s", SAMPLES)
def test_filename_variants_match_the_replaced_expressions(s):
    assert S.safe_filename(s, repl="", max=48) == re.sub(r"[^A-Za-z0-9._-]", "", s)[:48]
    assert S.safe_filename(s, repl="_") == re.sub(r"[^A-Za-z0-9._-]", "_", s)
    uni = "".join(c if c.isalnum() or c in "-_." else "_" for c in s)
    assert S.safe_filename(s, repl="_", unicode=True) == uni
    assert S.safe_filename(s, repl="_", unicode=True, max=80) == uni[:80]
    assert S.claude_project_slug(s) == re.sub(r"[^A-Za-z0-9]", "-", s)
    assert IH.project_slug(Path(s)) == re.sub(r"[^A-Za-z0-9]", "-", str(Path(s)))


@pytest.mark.parametrize("s", SAMPLES)
@pytest.mark.parametrize("limit", [0, 5, 12, 30, 48])
def test_slug_variants_match_the_replaced_expressions(s, limit):
    old = re.sub(r"[^\w\s-]", "", s.lower()).strip()
    old = re.sub(r"[\s_-]+", "-", old)
    assert S.slug(s, limit) == (old[:limit].strip("-") or "item")
    assert IM._slug(s, limit) == S.slug(s, limit)
    assert S.ascii_slug(s, limit) == re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()[
        :limit
    ].strip("-")
    assert IH._id_fragment(s, limit) == re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:limit]


@pytest.mark.parametrize("s", SAMPLES)
def test_github_anchor_keeps_githubs_rule(s):
    h = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)
    h = re.sub(r"<[^>]+>", "", h)
    h = re.sub(r"[`*~]", "", h).strip().lower()
    h = re.sub(r"[^\w\- ]", "", h)
    assert S.github_anchor(s) == h.replace(" ", "-") == DC.slugify(s)


def test_the_documented_examples():
    assert DC.slugify("Phase 160 — the drafter") == "phase-160--the-drafter"
    assert S.claude_project_slug("/home/d/src/run_nemo_run") == "-home-d-src-run-nemo-run"
    assert S.safe_filename("///", default="item", strip="-") == "item"
