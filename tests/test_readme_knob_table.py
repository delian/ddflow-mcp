"""The README's knob table and every knob count it states match the config
(B-uni-knobs-readme)."""

from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path

import pytest

from ddflow.config import KNOB_CHOICES, Config
from ddflow.core.digest import content_digest
from ddflow.views import knob_table as KT

README = Path(__file__).resolve().parents[1] / "README.md"


def _readme() -> str:
    return README.read_text("utf-8")


def _counts() -> tuple[int, int]:
    cfg = Config()
    sections = list(cfg._sections())
    return sum(len(fields(getattr(cfg, s))) for s in sections), len(sections)


def test_the_readme_knob_table_is_what_the_declarations_render():
    """The generated block is current. When this fails, a knob, its default or its values
    changed: run `uv run python -m ddflow.views.knob_table README.md` and commit."""
    readme = _readme()
    assert readme.count(f"<!-- ddflow:begin {KT.REGION} sha=") == 1
    assert readme.count(KT.END) == 1
    assert not KT.hand_edited(readme)
    assert KT.replace(readme, KT.render()) == readme, (
        "README.md's knob table is stale: `uv run python -m ddflow.views.knob_table README.md`"
    )


def test_the_table_has_one_row_per_knob_with_its_values():
    block = KT.render()
    knobs, sections = _counts()
    rows = [line for line in block.splitlines() if line.startswith("| `")]
    assert len(rows) == knobs
    assert f"All {knobs} knobs across {sections} sections" in block
    keys = [re.match(r"\| `([^`]+)`", r).group(1) for r in rows]
    assert len(set(keys)) == knobs
    for key, choices in KNOB_CHOICES.items():
        row = rows[keys.index(key)]
        assert row.endswith(" | " + " \\| ".join(f"`{c}`" for c in choices) + " |"), row


def test_a_cell_escapes_a_pipe_and_points_a_long_default_at_explain():
    assert KT._default("a|b") == '`"a\\|b"`'
    assert KT._default(["x" * KT.SHOWN]) == "(long: see `ddflow config --explain`)"
    assert KT._default({"k": 1}) == "`{k = 1}`"
    assert KT._default("a`b") == '`` "a`b" ``'
    assert KT._default("a``b`") == '``` "a``b`" ```'


#: A begin marker as an older render left it: any 12-hex digest.
OLD_BEGIN = f"<!-- ddflow:begin {KT.REGION} sha=0123456789ab -->"


def test_the_region_uses_the_one_marker_grammar_with_the_body_digest():
    """D-doc-regions: `ddflow:begin <doc>/<region> sha=<12hex>` ... `ddflow:end <doc>/<region>`,
    the sha being the digest of the body between the markers."""
    begin, *body, end = KT.render().split("\n")
    assert end == f"<!-- ddflow:end {KT.REGION} -->"
    m = re.fullmatch(rf"<!-- ddflow:begin {KT.REGION} sha=([0-9a-f]{{12}}) -->", begin)
    assert m and m.group(1) == content_digest("\n".join(body), length=12)


def test_replace_swaps_only_the_region_and_refuses_a_readme_without_one():
    text = f"head\n{OLD_BEGIN}\nold\n{KT.END}\ntail\n"
    assert KT.replace(text, "NEW") == "head\nNEW\ntail\n"
    with pytest.raises(ValueError):
        KT.replace("no markers here", "NEW")
    with pytest.raises(ValueError):
        KT.replace(f"{OLD_BEGIN}\nbut no end", "NEW")
    with pytest.raises(ValueError):
        KT.replace(f"{KT.END}\n{OLD_BEGIN}\n", "NEW")
    # A CRLF checkout: the markers are still whole lines.
    assert KT.replace(text.replace("\n", "\r\n"), "NEW") == "head\r\nNEW\r\ntail\r\n"


def test_main_rewrites_a_stale_region_and_leaves_a_current_one(tmp_path, capsys):
    """A region whose body still hashes to its sha is stale, not edited: rewritten."""
    path = tmp_path / "README.md"
    old = KT.render().replace("All ", "Every ", 1)
    old = re.sub(
        r"sha=[0-9a-f]{12}",
        "sha=" + content_digest(old.split("\n", 1)[1].rsplit("\n", 1)[0], length=12),
        old,
    )
    path.write_text(f"x\n{old}\n", "utf-8")
    assert not KT.hand_edited(path.read_text("utf-8"))
    assert KT.main([str(path)]) == 0
    assert path.read_text("utf-8") == f"x\n{KT.render()}\n"
    assert KT.main([str(path)]) == 0
    assert "already current" in capsys.readouterr().out


def test_main_refuses_a_hand_edited_region_unless_forced(tmp_path, capsys):
    """D-doc-regions: the body no longer matches the sha its marker recorded, so a rewrite
    would silently drop someone's edit."""
    path = tmp_path / "README.md"
    edited = f"x\n{OLD_BEGIN}\nmy note\n{KT.END}\n"
    path.write_text(edited, "utf-8")
    assert KT.hand_edited(edited)
    assert KT.main([str(path)]) == 3
    assert path.read_text("utf-8") == edited
    assert "edited by hand" in capsys.readouterr().err
    assert KT.main([str(path), "--force"]) == 0
    assert path.read_text("utf-8") == f"x\n{KT.render()}\n"


def _count_claims(text: str) -> list[tuple[str, str]]:
    """Every knob count `text` states, as (knobs, sections or ""). The contract: "N knobs
    [across M sections]"; and "knobs (K of the N)" -- the word, then only spaces, bold
    markers or one line wrap, then the parenthetical. A wrapped line is still the claim;
    punctuation between them (`knobs, (...)`) is another sentence's aside, not a count."""
    claims = re.findall(r"(\d+) knobs(?: across (\d+) sections)?", text)
    claims += [(n, "") for n in re.findall(r"knobs\**[ ]*\n?[ ]*\(\d+ of the (\d+)\)", text)]
    return claims


def test_the_count_claims_follow_their_contract():
    """One case per clause of `_count_claims`' contract."""
    assert _count_claims("**The `[export]` knobs** (5 of the 150): ...") == [("150", "")]
    assert _count_claims("the `[export]` knobs**\n(5 of the 150): ...") == [("150", "")]
    assert _count_claims("All 70 knobs across 15 sections") == [("70", "15")]
    assert _count_claims("See the knobs of the 15 sections.") == []
    assert _count_claims("the knobs, (2 of the 70) are ...") == []
    assert _count_claims("the knobs\n\n(2 of the 70)") == []


def test_the_readme_knob_counts_match_the_config():
    """Both numbers were stale when `[log]` was added — one said 58, the other "61
    across 12 sections", and the truth was 70 across 15. Then "(5 of the 150)" survived
    beside 189, because this ratchet only read "N knobs" (B55895bdfc7): it reads the
    "knobs (K of the N)" form too.

    Pinned rather than corrected-and-hoped: a hand-maintained count in prose drifts the
    first time anyone adds a knob, and a reader who finds a wrong number trusts it.
    """
    knobs, sections = _counts()
    claims = _count_claims(_readme())
    assert claims, "the README no longer states a knob count; this ratchet has gone blind"
    for stated_knobs, stated_sections in claims:
        assert int(stated_knobs) == knobs, (
            f"README says {stated_knobs} knobs, the config has {knobs}"
        )
        if stated_sections:
            assert int(stated_sections) == sections, (
                f"README says {stated_sections} sections, the config has {sections}"
            )
