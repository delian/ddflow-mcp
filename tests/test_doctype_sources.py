"""Document sources: kind, query, fragments, file, cmd, registry and the placeholders; the
eight export kinds as shipped types whose output is byte for byte the kind's own."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ddflow.core.events import Event
from ddflow.services.doctypes import recordquery as RQ
from ddflow.services.doctypes import source_registry, spec
from ddflow.services.doctypes import sources as S
from ddflow.services.export import ExportError, Filters, query, registry

KINDS = ("bugs", "changelog", "decisions", "roadmap", "rules", "sessions", "status", "worklog")


def _log() -> query.Query:
    n = [0]

    def ev(kind: str, subject: str, ts: str, **data: object) -> Event:
        n[0] += 1
        return Event(kind, subject, data, agent="a1", lamport=n[0], ts=ts)

    return query.build(
        [
            ev("phase.added", "P1", "2026-09-01T08:00:00Z", title="Phase one"),
            ev("task.added", "T1", "2026-09-01T08:01:00Z", title="Write docs", parent="P1",
               tags=["todo"], priority=5),
            ev("task.added", "T2", "2026-09-01T08:02:00Z", title="Ship it", parent="P1",
               tags=["later"], priority=9),
            ev("task.added", "T3", "2026-09-02T08:02:00Z", title="Done one", parent="P1",
               tags=["todo"]),
            ev("item.completed", "T3", "2026-09-03T08:00:00Z"),
            ev("bug.found", "B1", "2026-09-04T08:00:00Z", summary="It breaks"),
            ev("bug.found", "B2", "2026-09-05T08:00:00Z", summary="Also breaks"),
            ev("bug.fixed", "B2", "2026-09-06T08:00:00Z", regression_test="tests/t.py::t"),
            ev("decision.recorded", "D1", "2026-09-07T09:00:00Z", title="Use TOML"),
        ]
    )  # fmt: skip


@pytest.fixture
def q() -> query.Query:
    return _log()


def _types() -> dict[str, spec.DocType]:
    return dict(spec.registry().items())


def _dt(source: str, **extra: str) -> spec.DocType:
    body = "".join(f"{k} = '''{v}'''\n" for k, v in extra.items())
    return spec.parse(
        f'[doctype]\nname = "t"\npath = "T.md"\n[[doctype.section]]\nid = "s"\n'
        f'ownership = "generated"\nsource = "{source}"\n{body}'
    )


# -- the eight kinds are types ---------------------------------------------------------


@pytest.mark.parametrize("name", KINDS)
def test_a_shipped_type_renders_byte_identical_to_the_kind(name, q, tmp_path):
    dt = _types()[name]
    assert dt.sections[0].source == f"kind:{name}"
    q.repo = _repo(tmp_path)  # the changelog reads git tags
    want = registry.render_body(name, q)
    assert want.strip()
    assert S.render_type(dt, S.Context(query=q, repo=q.repo)) == want


def test_the_export_config_template_override_still_applies(q, tmp_path):
    tpl = tmp_path / "mine.md.j2"
    tpl.write_text("custom {{ tasks_total }}\n")
    ctx = S.Context(query=q, repo=tmp_path, overrides={"status": "mine.md.j2"})
    assert S.render_type(_types()["status"], ctx) == "custom 3\n"
    assert registry.render_body("status", q, repo=tmp_path, overrides={"status": "mine.md.j2"}) == (
        "custom 3\n"
    )


def test_kind_key_yields_one_value(q):
    dt = _dt("kind:status.tasks_total", template="{{ value }} tasks")
    assert S.render_type(dt, S.Context(query=q)) == "3 tasks\n"


def test_kind_names_must_exist_at_load():
    with pytest.raises(spec.DocTypeError, match="unknown document kind 'nope'"):
        _dt("kind:nope")


# -- the record query ------------------------------------------------------------------


def _ids(text: str, q: query.Query) -> list[str]:
    return [r["id"] for r in RQ.run(RQ.parse(text), q)]


def test_query_filters_by_state_tags_and_sorts(q):
    assert _ids("kind=task state=open tags=todo", q) == ["T1"]
    assert _ids("kind=task sort=-priority", q) == ["T3", "T2", "T1"]
    assert _ids("kind=task sort=priority limit=1", q) == ["T1"]
    assert _ids("kind=bug state=open", q) == ["B1"]
    assert _ids("kind=bug state=fixed,open", q) == ["B1", "B2"]
    assert _ids("kind=phase", q) == ["P1"]
    assert _ids("kind=task phase=P1 limit=2", q) == ["T1", "T2"]
    assert _ids("kind=task phase=P9", q) == []
    assert _ids("kind=bug since=2026-09-05", q) == ["B2"]


def test_query_fields_select_columns(q):
    rows = RQ.run(RQ.parse("kind=task tags=todo fields=id,title"), q)
    assert rows and all(set(r) == {"id", "title"} for r in rows)


@pytest.mark.parametrize(
    "text, msg",
    [
        ("", "query kind"),
        ("kind=nope", "query kind"),
        ("kind=task colour=red", "unknown query key"),
        ("kind=task state", "not key=value"),
        ("kind=task limit=x", "query limit"),
        ("kind=task sort=nope", "query sort"),
        ("kind=task fields=nope", "query field"),
        ("kind=bug phase=P1", "does not apply"),
        ("kind=task since=2026-13-99", "since"),
        ("kind=task kind=bug", "twice"),
    ],
)
def test_a_bad_query_is_refused_at_load(text, msg):
    with pytest.raises(spec.DocTypeError, match=msg):
        _dt(f"query:{text}")


def test_a_todo_document_without_python(q):
    dt = _dt(
        "query:kind=task state=open tags=todo",
        template="{% for r in rows %}- {{ r.id }}\n{% endfor %}",
    )
    assert S.render_type(dt, S.Context(query=q)) == "- T1\n"


def test_default_query_template_lists_rows(q):
    out = S.render_type(_dt("query:kind=bug state=open"), S.Context(query=q))
    assert out == "- B1 It breaks (open)\n"


# -- placeholders and unknown kinds -----------------------------------------------------


@pytest.mark.parametrize("name", ["ask", "draft", "static"])
def test_placeholders_yield_nothing(name, q):
    assert S.render_type(_dt(name), S.Context(query=q)) == ""


def test_unknown_source_names_the_known_ones():
    with pytest.raises(spec.DocTypeError, match=r"unknown document source 'nope'.*query"):
        _dt("nope:x")


def test_a_placeholder_takes_no_argument():
    with pytest.raises(spec.DocTypeError, match="takes no argument"):
        _dt("ask:x")


# -- files -----------------------------------------------------------------------------


def _repo(tmp_path: Path) -> Path:
    def git(*a: str) -> None:
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "docs/fragments").mkdir(parents=True)
    (tmp_path / "docs/fragments/b.md").write_text("Second.\n")
    (tmp_path / "docs/fragments/a.md").write_text("First.\n")
    (tmp_path / "docs/fragments/README.md").write_text("how to add\n")
    (tmp_path / "docs/fragments/untracked.md").write_text("never\n")
    (tmp_path / "GUIDE.md").write_text(
        "# Guide\n\nintro\n\n## Install\n\nrun it\n\n```\n# not a heading\n```\n\n"
        "### Deep\n\ndeeper\n\n## Use\n\nuse it\n"
    )
    git("add", "docs/fragments/a.md", "docs/fragments/b.md", "docs/fragments/README.md", "GUIDE.md")
    git("commit", "-qm", "x")
    return tmp_path


def test_fragments_are_tracked_files_in_name_order(tmp_path):
    repo = _repo(tmp_path)
    out = S.render_type(_dt("fragments:docs/fragments"), S.Context(repo=repo))
    assert out == "First.\n\nSecond.\n"


def test_fragments_unavailable_without_a_repo():
    with pytest.raises(ExportError) as e:
        S.render_type(_dt("fragments:docs/fragments"), S.Context())
    assert e.value.code == 2


def test_file_whole_and_by_anchor(tmp_path):
    repo = _repo(tmp_path)
    ctx = S.Context(repo=repo)
    assert S.render_type(_dt("file:GUIDE.md"), ctx).startswith("# Guide\n")
    out = S.render_type(_dt("file:GUIDE.md#install"), ctx)
    assert out.startswith("## Install\n") and "### Deep" in out and "# not a heading" in out
    assert "## Use" not in out


def test_file_refuses_what_it_should(tmp_path):
    repo = _repo(tmp_path)
    ctx = S.Context(repo=repo, max_bytes=20)
    for bad in ("file:../x.md", "file:/etc/passwd"):
        with pytest.raises(spec.DocTypeError, match="not a path inside the repo"):
            _dt(bad)
    with pytest.raises(ExportError, match="no heading with the anchor #nope"):
        S.render_type(_dt("file:GUIDE.md#nope"), S.Context(repo=repo))
    with pytest.raises(ExportError, match="not a tracked file"):
        (repo / "loose.md").write_text("x")
        S.render_type(_dt("file:loose.md"), S.Context(repo=repo))
    with pytest.raises(ExportError, match="over the 20 cap"):
        S.render_type(_dt("file:GUIDE.md"), ctx)


def test_file_refuses_a_symlink_out_of_the_repo(tmp_path):
    repo = _repo(tmp_path / "r") if (tmp_path / "r").mkdir() is None else tmp_path
    outside = tmp_path / "secret.md"
    outside.write_text("secret\n")
    (repo / "link.md").symlink_to(outside)
    subprocess.run(["git", "add", "link.md"], cwd=repo, check=True)
    with pytest.raises(ExportError):
        S.render_type(_dt("file:link.md"), S.Context(repo=repo))


# -- cmd -------------------------------------------------------------------------------


def test_cmd_runs_a_declared_command(tmp_path):
    ctx = S.Context(repo=tmp_path, commands={"hello": "echo hi there"})
    assert S.render_type(_dt("cmd:hello"), ctx) == "hi there\n"


def test_cmd_redacts_and_caps(tmp_path):
    ctx = S.Context(
        repo=tmp_path, commands={"h": "echo secret"}, redactor=lambda t: t.replace("secret", "XXX")
    )
    assert S.render_type(_dt("cmd:h"), ctx) == "XXX\n"


def test_cmd_unavailable_is_never_empty(tmp_path):
    for cmds, name in (
        ({}, "gone"),
        ({"gone": "no-such-binary-xyz --x"}, "gone"),
        ({"f": "false"}, "f"),
    ):
        with pytest.raises(ExportError) as e:
            S.render_type(_dt(f"cmd:{name}"), S.Context(repo=tmp_path, commands=cmds))
        assert e.value.code == 2


def test_cmd_refuses_shell_syntax(tmp_path):
    with pytest.raises(ExportError, match="shell syntax"):
        S.render_type(_dt("cmd:x"), S.Context(repo=tmp_path, commands={"x": "echo a | cat"}))


def test_cmd_name_is_a_name_not_a_command():
    with pytest.raises(spec.DocTypeError, match="configured command"):
        _dt("cmd:rm -rf /")


# -- registry --------------------------------------------------------------------------


def test_registry_generators():
    source_registry.add("greet", lambda ctx: "hello")
    try:
        assert S.render_type(_dt("registry:greet"), S.Context()) == "hello\n"
        with pytest.raises(ExportError, match="no document generator 'zzz'"):
            S.render_type(_dt("registry:zzz"), S.Context())
    finally:
        source_registry.GENERATORS.unregister("greet")


# -- section shape ---------------------------------------------------------------------


def test_headed_sections_join_with_blank_lines(q):
    dt = spec.parse(
        '[doctype]\nname = "t"\npath = "T.md"\n'
        '[[doctype.section]]\nid = "a"\nheading = "Open bugs"\nownership = "generated"\n'
        'source = "query:kind=bug state=open"\n'
        '[[doctype.section]]\nid = "b"\nheading = "Hand written"\n'
        '[[doctype.section]]\nid = "c"\nheading = "Phases"\nownership = "generated"\n'
        'source = "query:kind=phase"\n'
    )
    assert S.render_type(dt, S.Context(query=q)) == (
        "## Open bugs\n\n- B1 It breaks (open)\n\n## Phases\n\n- P1 Phase one (open)\n"
    )


def test_a_template_file_must_stay_in_the_repo(q, tmp_path):
    with pytest.raises(ExportError, match="not inside the repo"):
        S.render_type(
            _dt("query:kind=bug", template="file:../x.j2"), S.Context(query=q, repo=tmp_path)
        )


def test_the_log_is_needed_for_log_sources():
    with pytest.raises(ExportError) as e:
        S.render_type(_dt("query:kind=bug"), S.Context())
    assert e.value.code == 2


def test_filters_object_default():
    assert S.Context().filters == Filters()


def test_cmd_failure_message_is_redacted(tmp_path):
    script = tmp_path / "fail.sh"
    script.write_text("#!/bin/sh\necho secret-token >&2\nexit 3\n")
    script.chmod(0o755)
    ctx = S.Context(
        repo=tmp_path,
        commands={"f": str(script)},
        redactor=lambda t: t.replace("secret-token", "XXX"),
    )
    with pytest.raises(ExportError) as e:
        S.render_type(_dt("cmd:f"), ctx)
    assert "XXX" in str(e.value) and "secret-token" not in str(e.value)


def test_read_capped_stays_in_the_repo(tmp_path):
    (tmp_path / "ok.txt").write_text("fine")
    assert S.read_capped(S.Context(repo=tmp_path), "ok.txt") == "fine"
    with pytest.raises(S.SourceUnavailable):
        S.read_capped(S.Context(), "ok.txt")
    with pytest.raises(S.SourceError):
        S.read_capped(S.Context(repo=tmp_path), "../outside.txt")
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "s.txt").write_text("x")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "link.txt").symlink_to(tmp_path / "out" / "s.txt")
    with pytest.raises(S.SourceError):
        S.read_capped(S.Context(repo=repo), "link.txt")


def test_bug_state_fix_wins_over_invalid_closure():
    def ev(kind: str, ts: str, **data: object) -> Event:
        return Event(
            kind, "B9", data, agent="a1", lamport=int(ts[-3:-1]), ts=f"2026-09-01T08:00:{ts}Z"
        )

    q = query.build(
        [ev("bug.found", "01", summary="x"), ev("bug.fixed", "02", regression_test="t.py::t"),
         ev("bug.invalid", "03", reason="r")]
    )  # fmt: skip
    assert [r["state"] for r in RQ.run(RQ.parse("kind=bug"), q)] == ["fixed"]


def test_contested_records_are_not_live():
    from types import SimpleNamespace as NS

    assert RQ._lesson_state(NS(superseded_by="", contested=[{"x": 1}])) == "contested"
    assert RQ._lesson_state(NS(superseded_by="", contested=[])) == "live"
    assert RQ._status(NS(status="accepted", contested=[{"x": 1}])) == "contested"
    assert RQ._status(NS(status="accepted", contested=[])) == "accepted"


def test_query_limit_must_be_decimal_and_fields_are_always_present(q):
    with pytest.raises(RQ.QueryError):
        RQ.parse("kind=task limit=²")
    rows = RQ.run(
        RQ.parse("kind=task fields=id,title"),
        q,
    )
    assert all(set(r) == {"id", "title"} for r in rows)


def test_fragments_directory_typo_is_refused_not_empty(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(ExportError, match="not a directory"):
        S.render_type(_dt("fragments:docs/fragment"), S.Context(repo=repo))
