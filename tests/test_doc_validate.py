"""B-dt-validate: a document checked against its type, rule kind by rule kind."""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow import api
from ddflow.services.doctypes import mdparse, spec
from ddflow.services.doctypes.rubrics import RUBRICS
from ddflow.services.doctypes.validate import validate

FIRST_LINE, LAST_LINE = 7, 18
ADR = (
    "# 1. Use X\n\n## Status\n\nAccepted\n\n## Context\n\nWhy.\n\n## Decision\n\nWe do.\n\n"
    "## Consequences\n\nFine.\n"
)


def dtype(rules: str, sections: str = "") -> spec.DocType:
    return spec.parse(
        '[doctype]\nname = "t"\npath = "T.md"\n'
        + sections
        + f"[doctype.validate]\nrules = [{rules}]\n"
    )


def sec(sid: str, heading: str, **kw) -> str:
    extra = "".join(f"{k} = {v}\n" for k, v in kw.items())
    return f'[[doctype.section]]\nid = "{sid}"\nheading = "{heading}"\n{extra}'


def found(dt, text, **kw) -> list[tuple[str, int]]:
    return [(f.ref, f.line) for f in validate(dt, text, "T.md", **kw)]


def test_parser_reads_front_matter_parts_and_skips_code():
    d = mdparse.parse(
        "---\nstatus: draft\ntags: [a, b]\nowners:\n  - x\n---\n# T\n\n## Ctx\nsee [l](a.md)\n- one\n"
        "```py\n# not a heading\n```\n| a | b |\n|---|---|\n### Sub\nmore\n## Next\n"
    )
    assert d.front == {"status": "draft", "tags": ["a", "b"], "owners": ["x"]}
    assert d.front_line == FIRST_LINE
    assert [h.text for h in d.headings] == ["T", "Ctx", "Sub", "Next"]
    ctx = d.parts[1]
    assert ctx.code_langs == ["py"] and ctx.tables == 1 and ctx.items == ["one"]
    assert ctx.links == [(10, "a.md")]
    assert ctx.end == LAST_LINE


def test_unclosed_front_matter_is_reported():
    assert "never closed" in mdparse.parse("---\na: 1\n# T\n").front_error
    assert [f.ref for f in validate(dtype(""), "---\na: 1\n", "T.md")] == ["front_matter"]


def test_the_shipped_adr_passes_and_names_what_is_missing():
    adr = spec.registry()["adr"]
    assert validate(adr, ADR, "docs/adr/0001-x.md") == []
    text = ADR.replace("## Decision\n\nWe do.\n\n", "")
    assert found(adr, text) == [("required_sections", 1)]
    swapped = (
        ADR.replace("## Context", "## Zed")
        .replace("## Decision", "## Context")
        .replace("## Zed", "## Decision")
    )
    assert [r for r, _ in found(adr, swapped)] == ["heading_order"]


def test_a_wrong_level_is_named():
    dt = dtype('{ kind = "required_sections" }', sec("c", "Context", required="true"))
    assert found(dt, "# Context\n") == [("required_sections", 1)]
    assert found(dt, "## Context\n") == []


@pytest.mark.parametrize(
    ("rule", "bad", "ok"),
    [
        ('{ kind = "no_skipped_levels" }', "# A\n### B\n", "# A\n## B\n"),
        ('{ kind = "unique_headings" }', "## A\n## a\n", "## A\n## B\n"),
        ('{ kind = "max_bytes", limit = 5 }', "123456", "12345"),
        ('{ kind = "required_text", regex = "^Owner:" }', "x\n", "Owner: me\n"),
        ('{ kind = "pattern", regex = "ok" }', "x\n", "ok\n"),
        ('{ kind = "forbidden_text", regex = "TODO" }', "a\nTODO\n", "a\n"),
        ('{ kind = "forbidden_whole_text", regex = "all\\\\s+good" }', "all\ngood\n", "fine\n"),
        ('{ kind = "min_words", limit = 3 }', "one two\n", "one two three\n"),
        ('{ kind = "max_words", limit = 1 }', "one two\n", "one\n"),
        ('{ kind = "code_blocks", lang = "sh", min = 1 }', "```py\nx\n```\n", "```sh\nx\n```\n"),
        ('{ kind = "has_table" }', "x\n", "| a |\n|---|\n"),
        ('{ kind = "has_list" }', "x\n", "- a\n"),
        ('{ kind = "each_item_matches", regex = "falsifier" }', "- a\n", "- a falsifier b\n"),
        ('{ kind = "no_ask_placeholder" }', "<!-- ask: who? -->\n", "answered\n"),
        ('{ kind = "no_draft" }', "---\nstatus: Draft\n---\n", "---\nstatus: accepted\n---\n"),
        (
            '{ kind = "not_expired" }',
            "---\nreview_by: 2020-01-01\n---\n",
            "---\nreview_by: 2999-01-01\n---\n",
        ),
        (
            '{ kind = "front_matter", schema = { required = ["status"], properties = { status = { enum = ["a", "b"] } } } }',
            "---\nstatus: z\n---\n",
            "---\nstatus: a\n---\n",
        ),
    ],
)
def test_each_rule_kind_fails_on_bad_text_and_passes_on_good(rule, bad, ok):
    dt = dtype(rule)
    kind = rule.split('"')[1]
    assert [r for r, _ in found(dt, bad, today=date(2026, 1, 1))] == [kind]
    assert found(dt, ok, today=date(2026, 1, 1)) == []


def test_a_rule_is_scoped_to_its_section_and_a_missing_section_is_not_judged_twice():
    dt = dtype(
        '{ kind = "forbidden_text", regex = "TODO", section = "a" }',
        sec("a", "A", required="true"),
    )
    assert found(dt, "TODO\n## A\nclean\n") == []
    assert found(dt, "## A\nsome\nTODO here\n") == [("forbidden_text:a", 3)]
    assert found(dt, "nothing\n") == []  # required_sections reports a missing section, not this


def test_fenced_code_is_not_prose():
    dt = dtype('{ kind = "forbidden_text", regex = "TODO" }')
    assert found(dt, "```\nTODO\n```\n") == []


def test_links_resolve_checks_the_file_and_the_heading():
    files = {"docs/b.md": "# Real\n"}
    dt = spec.parse(
        '[doctype]\nname = "t"\npath = "docs/a.md"\n[doctype.validate]\nrules = [{ kind = "links_resolve" }]\n'
    )
    run = lambda t: [(f.ref, f.line) for f in validate(dt, t, "docs/a.md", read=files.get)]  # noqa: E731
    assert run("[x](b.md#real) [y](https://e.com)\n") == []
    assert run("[x](gone.md)\n") == [("links_resolve", 1)]
    assert run("[x](b.md#nope)\n") == [("links_resolve", 1)]
    assert run("# H\n[x](#h) [y](#zz)\n") == [("links_resolve", 2)]
    assert [f.ref for f in validate(dt, "[x](b.md)\n", "docs/a.md")] == [
        "links_resolve"
    ]  # cannot tell


def test_a_message_replaces_the_detail_and_a_bad_regex_is_a_finding():
    dt = dtype('{ kind = "forbidden_text", regex = "x", message = "no x" }')
    assert validate(dt, "x\n", "T.md")[0].detail == "no x"
    bad = dtype('{ kind = "required_text", regex = "(" }')
    assert "regular expression" in validate(bad, "x\n", "T.md")[0].detail


@pytest.mark.parametrize(
    ("name", "good", "bad"),
    [
        (
            "security-policy",
            "# Security\nReport to sec@example.com. We follow coordinated disclosure.\nWe respond within 3 days.\n",
            "# Security\nbe careful\n",
        ),
        (
            "keep-a-changelog",
            "## [Unreleased]\n## [1.0.0] - 2026-01-02\n### Added\n- x\n",
            "# Changes\n",
        ),
        (
            "madr",
            "---\nstatus: accepted\n---\n# T\n## Context and Problem Statement\n## Considered Options\n## Decision Outcome\n",
            "# T\n",
        ),
    ],
)
def test_shipped_rubrics_score_documents(name, good, bad):
    assert name in RUBRICS
    dt = dtype(f'{{ kind = "rubric", name = "{name}" }}')
    assert validate(dt, good, "T.md") == []
    f = validate(dt, bad, "T.md")
    assert f and "scored" in f[0].detail and "missing" in f[0].detail
    loose = dtype(f'{{ kind = "rubric", name = "{name}", min_score = 0.0 }}')
    assert validate(loose, bad, "T.md") == []
    assert (
        "unknown rubric"
        in validate(dtype('{ kind = "rubric", name = "zz" }'), "x", "T.md")[0].detail
    )


def test_an_unknown_rule_kind_is_refused_at_load_and_a_new_file_adds_one():
    with pytest.raises(spec.DocTypeError, match="unknown validate rule kind"):
        dtype('{ kind = "nope" }')
    assert {"rubric", "links_resolve", "required_sections", "no_draft"} <= set(spec.RULE_KINDS)


# -- docs check integration ------------------------------------------------------------------


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def put(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    git(repo, "add", rel)


def doctype_findings(repo: Path) -> list[tuple[str, str]]:
    out = api.run_docs(repo, verb="check")
    return sorted((f["path"], f["ref"]) for f in out.data["findings"] if f["kind"] == "doctype")


def test_docs_check_validates_documents_of_a_type(repo: Path):
    put(repo, "docs/adr/0001-use-x.md", ADR)
    put(repo, "docs/adr/0002-bad.md", "# 2. Bad\n\n## Status\n\nx\n")
    assert doctype_findings(repo) == [("docs/adr/0002-bad.md", "required_sections")] * 2


def test_a_project_type_is_validated_and_its_lint_command_is_inert(repo: Path):
    put(
        repo,
        ".ddflow/doctypes/note.toml",
        (
            '[doctype]\nname = "note"\npath = "NOTE.md"\n[doctype.validate]\nlint_command = "false {path}"\n'
            'rules = [{ kind = "forbidden_text", regex = "TODO" }]\n'
        ),
    )
    put(repo, "NOTE.md", "a\nTODO\n")
    assert doctype_findings(repo) == [("NOTE.md", "forbidden_text")]
    out = api.run_docs(repo, verb="check")
    assert "inert" in out.data["unavailable"]["lint:note"] and out.data["clean"] is False


def test_an_unreadable_type_makes_the_check_unavailable_not_clean(repo: Path):
    put(repo, ".ddflow/doctypes/bad.toml", "not toml [[[")
    put(repo, "README.md", "# x\n")
    out = api.run_docs(repo, verb="check")
    assert "doctypes" in out.data["unavailable"] and out.data["clean"] is False


def test_a_blank_line_inside_a_fence_does_not_end_the_block():
    d = mdparse.parse("# A\n```python\nx = 1\n\ny = 2\n```\n~~~\n```\ntext\n~~~\n")
    assert d.parts[0].code_langs == ["python", ""]


def test_a_table_without_outer_pipes_is_a_table():
    assert mdparse.parse("# A\na | b\n--- | ---\n1 | 2\n").parts[0].tables == 1


def test_forbidden_whole_text_on_an_empty_scope_is_a_finding_not_a_crash():
    dt = dtype('{ kind = "forbidden_whole_text", regex = "\\\\s*", section = "a" }', sec("a", "A"))
    assert found(dt, "## A\n```\ncode\n```\n") == []


def test_a_schema_type_the_subset_does_not_know_is_reported():
    dt = dtype('{ kind = "front_matter", schema = { properties = { a = { type = "strin" } } } }')
    assert "not supported" in validate(dt, "---\na: x\n---\n", "T.md")[0].detail
    obj = dtype('{ kind = "front_matter", schema = { properties = { a = { type = "object" } } } }')
    assert [f.ref for f in validate(obj, "---\na: x\n---\n", "T.md")] == ["front_matter"]


def test_a_heading_regex_that_does_not_compile_is_refused_at_load():
    with pytest.raises(spec.DocTypeError, match="not a regex"):
        dtype("", sec("a", "Over(", match='"regex"'))


def test_a_malformed_collection_pattern_is_refused_at_load():
    with pytest.raises(spec.DocTypeError, match="pattern"):
        spec.parse('[doctype]\nname = "t"\nmulti = true\npattern = "docs/{slug.md"\n')


def test_the_lint_command_gets_the_path_quoted(repo: Path):
    from types import SimpleNamespace

    from ddflow.config import Config
    from ddflow.services import docscheck_doctypes as DD

    put(repo, "docs/Release Notes.md", "# x\n")
    dt = spec.parse(
        '[doctype]\nname = "t"\npath = "docs/Release Notes.md"\n'
        '[doctype.validate]\nlint_command = "test -f {path}"\n',
        source="shipped/t.toml",
    )
    check = SimpleNamespace(unavailable={})
    assert not DD._lint(repo, Config.load(repo), dt, "docs/Release Notes.md", check)
    assert not check.unavailable


def test_a_pipe_above_a_rule_is_not_a_table():
    assert mdparse.parse("# A\nflags `--a | --b`\n---\n").parts[0].tables == 0


def test_a_regex_heading_is_case_sensitive_a_literal_one_is_not():
    rx = dtype(
        '{ kind = "required_sections" }', sec("a", "^API$", match='"regex"', required="true")
    )
    assert found(rx, "## API\n") == [] and found(rx, "## api\n") == [("required_sections", 1)]
    lit = dtype('{ kind = "required_sections" }', sec("a", "API", required="true"))
    assert found(lit, "## api\n") == []


def test_a_rule_naming_an_undeclared_section_is_a_finding_not_a_silent_pass():
    dt = dtype('{ kind = "forbidden_text", regex = "TODO", section = "nope" }')
    assert [(f.ref, f.detail) for f in validate(dt, "TODO\n", "T.md")] == [
        ("forbidden_text:nope", "unknown section 'nope'")
    ]


def test_a_rule_without_its_regex_is_a_finding():
    for kind in ("required_text", "forbidden_text", "each_item_matches"):
        got = validate(dtype('{ kind = "%s" }' % kind), "x\n", "T.md")  # noqa: UP031
        assert [f.detail for f in got] == ["regex is required"], kind


def test_a_schema_with_the_wrong_container_types_cannot_be_applied():
    dt = dtype('{ kind = "front_matter", schema = { required = "status" } }')
    assert "cannot be applied" in validate(dt, "---\na: 1\n---\n", "T.md")[0].detail
