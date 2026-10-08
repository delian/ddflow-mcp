"""textcut.clip: one clipper under nine call sites (D-unify 4, strangler).

`tests/golden/textkit_clip.json.gz` (gzipped JSON; `zcat` reads it) was recorded from the nine ORIGINAL clippers over one
corpus (`_corpus`: lengths around each budget, multibyte text, leading/trailing spaces,
redaction marks cut at, before and after the budget). Every call site now reproduces its
recorded output byte for byte, with one decided exception: a cut no longer ends inside a
`[REDACTED:...]` mark (D-unify 3: the one clipper protects redaction marks; before, only
`bugreport._cut` did). The tests below say which recorded outputs that touches.
"""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ddflow.core import textcut as T
from ddflow.infra import forge as FG
from ddflow.infra import store as ST
from ddflow.services import bugreport as BR
from ddflow.services import search as SE
from ddflow.services.export import frame as FR
from ddflow.services.export import registry as RG
from ddflow.surfaces import mcp_bound as MB
from ddflow.views import markdown as MD

GOLDEN = json.loads(
    gzip.decompress((Path(__file__).parent / "golden" / "textkit_clip.json.gz").read_bytes())
)

#: A cut that used to end inside a redaction mark ("xx[REDACTED:sec") and now stops before it.
_UNCLOSED = re.compile(r"\[REDACTED:[^\]\s…]*(?=$| \[\.\.\.\]$|…$| \.\.\.$)")


def _expected(old):
    return old if old is None else _UNCLOSED.sub("", old)


@pytest.mark.parametrize("s,old,count", GOLDEN["mcp_clip"][:-1])
def test_mcp_bound_clip(s, old, count):
    cut = [0]
    got = MB._clip(s, cut)
    assert got == _expected(old)
    assert cut[0] == count


def test_mcp_bound_clip_walks_lists_and_dicts():
    value, old, _n = GOLDEN["mcp_clip"][-1]
    assert MB._clip(value, [0]) == old


@pytest.mark.parametrize("s,old,note", GOLDEN["mcp_decisions"])
def test_mcp_bound_decision_text(s, old, note):
    body, got_note = MB.bound_decisions([{"id": "D", "decision": s, "context": "c"}], {})
    assert body[0].get("decision") == _expected(old)
    assert (got_note is None) == (note is None) and (got_note or "").endswith((note or "")[-20:])


@pytest.mark.parametrize("s,cap,old", GOLDEN["md_clip"])
def test_markdown_clip(s, cap, old):
    assert MD._clip(s, cap) == _expected(old)


@pytest.mark.parametrize("s,n,old", GOLDEN["bug_cut"])
def test_bugreport_cut(s, n, old):
    assert BR._cut(s, n) == old


def _tail_expected(s, n):
    """The recorded tail, except that one starting inside a redaction mark now starts after
    it. Worked out from the input with a regex, not with the code under test."""
    if len(s) <= n:
        return s
    start = len(s) - n
    for m in re.finditer(r"\[REDACTED:[^\]]*\]", s):
        if m.start() < start < m.end():
            start = m.end()
    return "[...]\n" + s[start:]


@pytest.mark.parametrize("s,n,old", GOLDEN["bug_tail"])
def test_bugreport_tail(s, n, old):
    assert BR._tail(s, n) == _tail_expected(s, n)
    if "[REDACTED:" not in s:
        assert BR._tail(s, n) == old


@pytest.mark.parametrize("parts,old", GOLDEN["forge_clip"])
def test_forge_clip(parts, old):
    assert FG._clip(parts) == _expected(old)


@pytest.mark.parametrize("table,row,width,old", GOLDEN["store_summary"])
def test_store_summary(table, row, width, old):
    head, body = ST.summarise_row(table, row, width)
    assert [head, body] == [old[0], _expected(old[1])]


@pytest.mark.parametrize("flat,span,old", GOLDEN["snippet"])
def test_search_snippet(flat, span, old):
    assert SE._snippet(flat, tuple(span) if span else None) == old


@pytest.mark.parametrize("s,limit,old", GOLDEN["truncate_text"])
def test_registry_truncate_text(s, limit, old):
    assert RG.truncate_text(s, limit) == _expected(old)


@pytest.mark.parametrize("s,limit,old", GOLDEN["one_line"])
def test_frame_one_line(s, limit, old):
    assert FR.one_line(s, limit) == _expected(old)


@pytest.mark.parametrize("body,cap,old", GOLDEN["frame_truncate"])
def test_frame_truncate(body, cap, old):
    assert FR.truncate(body, cap) == old


def test_a_cut_never_ends_inside_a_redaction_mark_at_any_site():
    """The decided change: before, only bugreport._cut held back a half-written mark."""
    text = "x" * 150 + "[REDACTED:secret]" + "tail" * 40
    for got in (
        MB._clip(text, [0]),
        MD._clip(text, 160),
        FG._clip([text * 30]),
        ST.summarise_row("items", {"id": "I", "title": "t", "body": text}, 160)[1],
        BR._tail(text, 100),
        SE._snippet(text, (150, 160)),
        SE._snippet(text, (120, 125)),
        FR.truncate("y" * 5 + text, 160),
    ):
        for m in re.finditer(r"\[REDACTED:", got):
            assert "]" in got[m.start() :], got[m.start() :]


# -- the clipper itself -------------------------------------------------------------------


def test_clip_options():
    assert T.clip("abcdef", 6) == "abcdef"
    assert T.clip("abcdefg", 6) == "abcdef"
    assert T.clip("abcdefg", 6, marker="…", keep=5) == "abcde…"
    assert T.clip("ab  defg", 4, marker="!") == "ab!"  # trimmed before the marker
    assert T.clip("ab  defg", 4, marker="!", rstrip=False) == "ab  !"
    assert T.clip("ab cdefg", 5, boundary="word", rstrip=False, marker="~") == "ab~"
    assert T.clip("abcdefg", 4, side="tail", marker=">") == ">defg"
    assert T.clip("éééé", 4, unit="bytes") == "éé"
    assert T.clip("éé", 4, unit="bytes") == "éé"


@given(st.text(max_size=80), st.integers(0, 90), st.sampled_from(["chars", "bytes"]))
def test_clip_never_exceeds_the_budget_and_is_a_prefix(text, limit, unit):
    out = T.clip(text, limit, unit=unit, rstrip=False)
    assert text.startswith(out)
    size = len(out.encode()) if unit == "bytes" else len(out)
    assert size <= max(limit, len(text) if size <= limit else 0)
    if (len(text.encode()) if unit == "bytes" else len(text)) <= limit:
        assert out == text


@given(st.text(alphabet="ab []:REDACTEDsecret", max_size=60), st.integers(1, 70))
def test_clip_never_leaves_a_half_written_mark(text, limit):
    out = T.clip(text, limit, rstrip=False)
    if out != text:
        start = out.rfind("[REDACTED:")
        assert start == -1 or "]" in out[start:]


@given(st.text(max_size=300), st.integers(1, 120))
def test_clip_lines_fits_or_says_why(text, cap):
    out = T.clip_lines(text, cap, lambda n: f"[+{n}]\n")
    assert out == text or len(out.encode()) <= cap or out.startswith("[+")


def test_window_marks_each_open_side():
    assert T.window("0123456789", 0, 4) == "0123…"
    assert T.window("0123456789", 3, 4) == "…3456…"
    assert T.window("0123456789", 6, 4) == "…6789"
    assert T.window("012", 0, 4) == "012"


def test_window_and_tail_do_not_start_or_end_inside_a_mark():
    text = "a" * 20 + "[REDACTED:secret]" + "b" * 20
    # window ends inside the mark: it stops before it
    assert T.window(text, 0, 30) == "a" * 20 + "…"
    # window starts inside the mark: it begins after the closing bracket
    first = T.window(text, 25, 20)
    assert first.startswith("…bbb") and "ACTED" not in first
    # a tail that would start inside the mark starts after it
    assert T.clip(text, 22, side="tail", marker=">") == ">" + "b" * 20
    # a line clip whose first line is cut mid-mark stops before the mark
    out = T.clip_lines(text + "\n", 40, lambda n: f"[+{n}]\n")
    assert "[REDACTED:" not in out or "]" in out[out.rfind("[REDACTED:") :]


def test_a_cut_inside_the_opening_of_a_mark_keeps_the_fragment():
    """The documented limit of the protection (and what bugreport._cut always did): only a
    cut AFTER `[REDACTED:` is held back."""
    assert T.clip("abc[REDACTED:email]xyz", 12, marker="…") == "abc[REDACTED…"
    assert T.clip("abc[REDACTED:email]xyz", 14, marker="…") == "abc…"


def test_bugreport_cut_drops_an_unfinished_mark_even_when_nothing_was_cut():
    assert BR._cut("user [REDACTED:tok", 100) == "user "


def test_the_recorded_inputs_hold_only_whole_marks_without_spaces():
    inputs = []
    for rows in GOLDEN.values():
        for r in rows:
            inputs.append(json.dumps(r[0], ensure_ascii=False))
    for text in inputs:
        for m in re.finditer(r"\[REDACTED:([^\]\\]*)", text):
            assert m.group(0).endswith(("secret", "host")) and " " not in m.group(1)
