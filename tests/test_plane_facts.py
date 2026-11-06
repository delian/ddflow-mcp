"""CP-verify-facts: control-plane facts feed the one generated facts table."""

from __future__ import annotations

import pytest

from ddflow.api.harness import harness_view
from ddflow.services import facts, harnessreg, planefacts

#: The questions CP-verify-facts was filed to answer: each needs an entry, even "unverified".
ASKED = {
    "paseo": ("hooks", "mcp_json_reader", "adopt_worktree"),
    "harness-remote": ("api", "auth", "root_limit", "mcp_inject"),
    "agent-os": ("adopt_worktree",),
    "orca": ("adopt_worktree",),
    "emdash": ("adopt_worktree",),
    "acp-claude": ("inject_mcp", "cwd"),
    "acp-codex": ("inject_mcp", "cwd"),
    "acp-opencode": ("inject_mcp", "cwd"),
    "acp-gemini": ("inject_mcp", "cwd"),
    "acp-kilo": ("inject_mcp", "cwd"),
}
GOOD = 's = "documented", src = "a", d = "2026-11-05"'


def doc(body: str) -> dict:
    import tomllib

    return tomllib.loads(f'[sources]\na = {{ url = "https://x.example" }}\n{body}')


def test_shipped_facts_are_valid_and_answer_every_question():
    data = planefacts.load()
    assert planefacts.problems(data) == []
    for plane, keys in ASKED.items():
        for key in keys:
            assert facts.is_fact(data["plane"][plane][key]), f"{plane}.{key}"


def test_a_fact_seen_running_is_the_only_verified_one_and_says_how():
    seen = [
        f
        for row in planefacts.load()["plane"].values()
        for f in row.values()
        if f["s"] == "verified"
    ]
    assert seen, "the live kilo probe is the floor"
    assert all(f.get("probe") and f.get("ver") for f in seen)


@pytest.mark.parametrize(
    ("body", "word"),
    [
        ('[plane.p]\nx = { v = "y", s = "documented", src = "b", d = "2026-11-05" }', "source"),
        (f"[plane.p]\nx = {{ {GOOD} }}", "needs a value"),
        ('[plane.p]\nx = "bare"', "not a fact"),
        ('[plane.p]\nx = { v = "y", s = "verified", src = "a", d = "2026-11-05" }', "probe"),
        ('[plane.p]\nx = { v = "y", s = "none", src = "a", d = "2026-11-05" }', "no value"),
    ],
)
def test_problems_name_each_defect(body, word):
    assert word in " ".join(planefacts.problems(doc(body)))


@pytest.mark.parametrize(
    "data", [{"plane": "x"}, {"sources": "x"}, {"plane": {"p": "x"}}, {"plane": {"p": []}}]
)
def test_a_wrong_shape_is_a_defect_not_a_crash(data):
    assert "is not a table" in " ".join(planefacts.problems(data))


def test_a_bad_sources_table_does_not_hide_a_plane_defect():
    found = planefacts.problems({"sources": "x", "plane": {"p": "y"}})
    assert any("[sources]" in f for f in found) and any("plane.p" in f for f in found)


def test_rows_of_a_plane_it_does_not_have_are_empty():
    assert planefacts.rows(planefacts.load(), "nosuch") == []


def test_plane_id_cannot_shadow_a_harness():
    taken = harnessreg.ids()[0]
    found = planefacts.problems(doc(f'[plane.{taken}]\nx = {{ v = "y", {GOOD} }}'))
    assert any("harness" in p for p in found)


def test_only_a_believable_fact_reads_as_a_value():
    plane = {
        "a": {"v": 1, "s": "documented"},
        "b": {"v": 2, "s": "unverified"},
        "c": {"s": "none"},
    }
    assert planefacts.usable(plane, "a") == 1
    assert planefacts.usable(plane, "b") is None
    assert planefacts.usable(plane, "c") is None
    assert planefacts.usable(plane, "gone") is None


def test_one_table_holds_harnesses_and_planes(tmp_path):
    out = harness_view(tmp_path, what="facts")
    agents = {r["agent"] for r in out.data["facts"]}
    assert {"claude", "paseo", "acp-kilo"} <= agents
    assert "| paseo | hooks |" in out.data["message"]


def test_plane_id_selects_its_rows_and_unknown_names_both(tmp_path):
    out = harness_view(tmp_path, what="facts", id="paseo")
    assert {r["agent"] for r in out.data["facts"]} == {"paseo"}
    bad = harness_view(tmp_path, what="facts", id="nosuch")
    assert bad.exit != 0
    assert "paseo" in bad.reason and "claude" in bad.reason


def test_unverified_keeps_the_open_questions(tmp_path):
    out = harness_view(tmp_path, what="facts", id="acp-claude", unverified=True)
    rows = out.data["facts"]
    assert rows and {r["status"] for r in rows} == {"unverified"}


def test_project_file_overrides_one_fact_and_keeps_the_rest(tmp_path):
    local = tmp_path / ".ddflow"
    local.mkdir()
    (local / planefacts.FILE).write_text(
        '[sources]\nmine = { note = "ran it here" }\n'
        '[plane.paseo.mcp_json_live]\nv = true\ns = "verified"\nsrc = "mine"\n'
        'd = "2026-11-06"\nver = "0.15.2"\nprobe = "paseo run"\n',
        encoding="utf-8",
    )
    rows = harness_view(tmp_path, what="facts", id="paseo").data["facts"]
    got = {r["fact"]: r for r in rows}
    assert got["mcp_json_live"]["status"] == "verified"
    assert got["mcp_json_live"]["source"] == "mine"
    assert "hooks" in got


def test_project_can_mark_a_shipped_fact_absent(tmp_path):
    local = tmp_path / ".ddflow"
    local.mkdir()
    (local / planefacts.FILE).write_text(
        '[plane.paseo.hooks]\ns = "none"\nsrc = "paseo-src"\nd = "2026-11-06"\n',
        encoding="utf-8",
    )
    got = {r["fact"]: r for r in harness_view(tmp_path, what="facts", id="paseo").data["facts"]}
    assert got["hooks"]["status"] == "none"
    assert got["hooks"]["value"] == ""


def test_a_shipped_defect_is_not_blamed_on_the_project(tmp_path):
    shipped = doc('[plane.p]\nx = "bare"')
    mine = doc('[plane.mine.x]\ns = "none"\nsrc = "a"\nd = "2026-11-06"\n')

    class Io:
        def read(self, path):
            return shipped if "templates" in str(path) else mine

    assert "mine" in planefacts.load(tmp_path, Io())["plane"]


def test_bad_project_file_is_a_named_failure_not_a_traceback(tmp_path):
    local = tmp_path / ".ddflow"
    local.mkdir()
    (local / planefacts.FILE).write_text("[plane.paseo\n", encoding="utf-8")
    out = harness_view(tmp_path, what="facts")
    assert out.exit != 0
    assert planefacts.FILE in out.reason


@pytest.mark.parametrize("text", ['sources = "x"\n', 'plane = "x"\n', 'plane = { paseo = "x" }\n'])
def test_wrong_shape_in_the_project_file_is_named(tmp_path, text):
    local = tmp_path / ".ddflow"
    local.mkdir()
    (local / planefacts.FILE).write_text(text, encoding="utf-8")
    out = harness_view(tmp_path, what="facts")
    assert out.exit != 0
    assert "is not a table" in out.reason
    assert str(local) in out.reason


def test_invalid_project_fact_is_refused(tmp_path):
    local = tmp_path / ".ddflow"
    local.mkdir()
    (local / planefacts.FILE).write_text('[plane.paseo]\nhooks = "guess"\n', encoding="utf-8")
    out = harness_view(tmp_path, what="facts")
    assert out.exit != 0
    assert "not a fact" in out.reason
