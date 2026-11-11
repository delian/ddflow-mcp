"""The shared output budget's own tests: under and over the cap, the footer, and that a
`--json` body (a non-string payload) is unchanged unless the caller asks for the bounded
form (TE-bounded-outputs)."""

from __future__ import annotations

from types import SimpleNamespace

import ddflow.services.output_budget as B


def _cfg(chars: int) -> SimpleNamespace:
    return SimpleNamespace(output=SimpleNamespace(max_chars=chars))


def test_under_both_caps_is_unchanged_with_no_footer():
    text = "one\ntwo\nthree\n"
    out = B.apply(text, cfg=_cfg(1000), cli="ddflow show X", mcp="ddflow_show id=X", bounded=True)
    assert out.text == text
    assert out.footer == ""
    assert (out.cut_chars, out.cut_lines) == (0, 0)


def test_over_the_line_cap_is_cut_and_footered():
    text = "\n".join(f"line {i}" for i in range(100))
    out = B.apply(
        text,
        cfg=_cfg(10_000),
        cli="ddflow recall q",
        mcp="ddflow_recall query=q",
        lines=10,
        bounded=True,
    )
    assert out.cut_lines == 90
    assert len(out.text.splitlines()) == 10
    assert "90 more line(s) not shown" in out.footer
    assert "`ddflow recall q`" in out.footer
    assert "`ddflow_recall query=q`" in out.footer


def test_over_the_char_cap_is_cut_on_a_line_boundary_and_footered():
    text = "\n".join(f"row {i}" for i in range(20))  # 20 short lines, > 100 chars total
    out = B.apply(text, cfg=_cfg(50), cli="ddflow list", mcp="ddflow_list", bounded=True)
    assert out.cut_chars > 0
    assert out.cut_chars == len(text) - len(out.text)
    assert text.startswith(out.text), "a character cut keeps a prefix of full lines"
    assert "character(s) cut" in out.footer


def test_a_json_body_is_unchanged_unless_bounded_asked():
    body = {"item": {"id": "X", "body": "x" * 500}, "rows": list(range(500))}
    out = B.apply(body, cfg=_cfg(10), a=SimpleNamespace(brief=False), bounded=False)
    assert out.text is body
    assert out.footer == ""


def test_a_json_body_is_unchanged_even_on_a_budget():
    # A non-string body is never silently cut (its TEXT form is what the budget holds).
    body = {"rows": list(range(500))}
    out = B.apply(body, cfg=_cfg(10), bounded=True)
    assert out.text is body
    assert out.footer == ""


def test_zcap_lifts_the_character_cap():
    text = "\n".join(f"row {i}" for i in range(200))
    out = B.apply(text, cfg=_cfg(0), cli="ddflow show X", lines=10, bounded=True)
    # A character budget of 0 lifts the character cap; the line budget still holds.
    assert len(out.text.splitlines()) == 10
    assert out.cut_chars == 0


def test_wants_bounded_reads_brief_or_agent():
    assert B.wants_bounded(SimpleNamespace(brief=True)) is True
    assert B.wants_bounded(SimpleNamespace(brief=False)) is B.is_agent()
    assert B.wants_bounded({"brief": True}) is True


def test_no_config_uses_the_shipped_default_char_budget():
    # A surface render without a config must still cap (finding: cfg=None walked the cap
    # off because the default was never read): one over-long line is cut to DEFAULT_CHARS.
    assert B.budget_chars(None) == B.DEFAULT_CHARS
    huge = "x" * (B.DEFAULT_CHARS + 100)
    out = B.apply(huge, cfg=None, bounded=True)
    assert out.cut_chars == 100 + (len(huge) - B.DEFAULT_CHARS - 100)
    assert "character(s) cut" in out.footer


def test_a_character_cut_stops_at_the_line_boundary_not_mid_line():
    # The boundary at index 0 must be kept (finding: `at > 0` sliced mid-line).
    out = B.apply("\n" + "y" * 50, cfg=_cfg(10), bounded=True)
    assert out.text == "\n"
    assert out.cut_chars == 50
