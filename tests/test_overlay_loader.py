"""OverlayLoader (B-uni-overlay.1): precedence, provenance, eject, drift, validate, and
parity with the two resolvers that move onto it next (prompts, export templates)."""

from __future__ import annotations

import pytest

from ddflow.infra.paths import templates_dir
from ddflow.services import overlay as O
from ddflow.services import prompts as P
from ddflow.services.export import registry as R
from ddflow.services.export import templates as T


@pytest.fixture
def loader(tmp_path):
    shipped = tmp_path / "pkg"
    shipped.mkdir()
    (shipped / "alpha.md.j2").write_text("hello {{ x }}\n")
    return O.OverlayLoader("widget", shipped_dir=shipped, project_subdir="widgets", suffix=".md.j2")


def test_precedence_config_then_project_then_builtin(loader, tmp_path):
    repo = tmp_path / "repo"
    assert loader.resolve("alpha", repo).source == O.BUILTIN
    loader.project_path(repo, "alpha").parent.mkdir(parents=True)
    loader.project_path(repo, "alpha").write_text("mine\n")
    got = loader.resolve("alpha", repo)
    assert (got.source, got.text) == (O.PROJECT, "mine\n")
    (repo / "cfg.j2").write_text("configured\n")
    got = loader.resolve("alpha", repo, configured="cfg.j2")
    assert (got.source, got.text, got.path) == (O.CONFIG, "configured\n", repo / "cfg.j2")


def test_a_configured_path_that_is_missing_is_an_error_not_a_fallback(loader, tmp_path):
    with pytest.raises(O.OverlayError, match="does not exist"):
        loader.resolve("alpha", tmp_path, configured="nope.j2")


def test_a_missing_shipped_asset_is_named(loader):
    with pytest.raises(O.OverlayError, match="missing from the package"):
        loader.resolve("beta")


def test_names_are_the_shipped_and_the_project_ones_once_each(loader, tmp_path):
    repo = tmp_path / "repo"
    loader.project_dir(repo).mkdir(parents=True)
    loader.project_path(repo, "alpha").write_text("x")
    loader.project_path(repo, "gamma").write_text("x")
    assert loader.names(repo) == ["alpha", "gamma"]
    assert loader.names() == ["alpha"]


def test_eject_drift_and_edit_protection(loader, tmp_path):
    repo = tmp_path / "repo"
    assert loader.eject(repo, "alpha").action == "created"
    assert loader.eject(repo, "alpha").action == "unchanged"
    assert loader.drift(repo, "alpha") is None
    # the shipped text moves on: an unedited copy drifts and is refreshed freely
    shipped = loader.shipped_path("alpha")
    shipped.write_text("hello again {{ x }}\n")
    d = loader.drift(repo, "alpha")
    assert d is not None and not d.edited
    assert loader.eject(repo, "alpha").action == "updated"
    # an edited copy is refused without force
    p = loader.project_path(repo, "alpha")
    p.write_text(p.read_text() + "my edit\n")
    shipped.write_text("third {{ x }}\n")
    d = loader.drift(repo, "alpha")
    assert d is not None and d.edited
    with pytest.raises(O.OverlayError) as exc:
        loader.eject(repo, "alpha")
    assert exc.value.refused
    assert loader.eject(repo, "alpha", force=True).action == "overwritten"


def test_eject_does_not_write_through_a_symlink(loader, tmp_path):
    repo = tmp_path / "repo"
    (repo / ".ddflow").mkdir(parents=True)
    (repo / "elsewhere").mkdir()
    (repo / ".ddflow" / "widgets").symlink_to(repo / "elsewhere")
    with pytest.raises(O.OverlayError, match="symlink"):
        loader.eject(repo, "alpha")
    assert not list((repo / "elsewhere").iterdir())


def test_validate_names_file_and_line_for_jinja(loader, tmp_path):
    p = tmp_path / "bad.j2"
    p.write_text("fine\n{% if x %}\nnever closed\n")
    (problem,) = loader.validate(O.Asset("bad", p.read_text(), O.PROJECT, p))
    assert problem.path == p and problem.line == 2 and str(problem).startswith(f"{p}:2:")
    assert loader.validate(O.Asset("ok", "a {{ b }}\n", O.PROJECT, p)) == []


def test_validate_names_file_and_line_for_toml(tmp_path):
    toml = O.OverlayLoader(
        "sched", shipped_dir=tmp_path, project_subdir="schedules", suffix=".toml", syntax="toml"
    )
    p = tmp_path / "s.toml"
    (problem,) = toml.validate(O.Asset("s", "a = 1\nb = = 2\n", O.PROJECT, p))
    assert problem.line == 2
    (tmp_path / "x.toml").write_text("k = 1\n")
    # the marker is a TOML comment, so a marked copy parses and its lines stay put
    assert toml.validate(O.Asset("x", toml.ejected_text("x"), O.PROJECT, p)) == []


def test_a_toml_marker_round_trips(tmp_path):
    toml = O.OverlayLoader(
        "sched", shipped_dir=tmp_path, project_subdir="schedules", suffix=".toml", syntax="toml"
    )
    (tmp_path / "x.toml").write_text("k = 1\n")
    recorded, body = toml.split_mark(toml.ejected_text("x"))
    assert recorded == O.digest("k = 1\n") and body == "k = 1\n"


# -- parity with the resolvers that move onto the loader next --------------------------


EXPORT = O.OverlayLoader(
    "export template",
    shipped_dir=templates_dir() / "export",
    project_subdir="templates/export",
    suffix=".md.j2",
)


@pytest.mark.parametrize("kind", R.names())
def test_export_templates_resolve_and_eject_as_before(kind, tmp_path):
    assert EXPORT.ejected_text(kind) == T.ejected_text(kind)
    assert EXPORT.project_path(tmp_path, kind) == T.project_path(tmp_path, kind)
    old = R.resolve_template(kind, tmp_path)
    new = EXPORT.resolve(kind, tmp_path)
    assert (new.source, new.text, new.path) == (old.source, old.text, old.path)
    T.eject(tmp_path, kind)
    old = R.resolve_template(kind, tmp_path)
    new = EXPORT.resolve(kind, tmp_path)
    assert (new.source, new.text, new.path) == (old.source, old.text, old.path)


PROMPTS = O.OverlayLoader(
    "prompt", shipped_dir=P.builtin_dir(), project_subdir="prompts", suffix=".md", syntax="jinja"
)


@pytest.mark.parametrize("name", P.TEMPLATE_NAMES)
def test_prompts_resolve_as_before(name, tmp_path):
    old = P.resolve(name, tmp_path)
    new = PROMPTS.resolve(name, tmp_path)
    assert (new.source, new.text, new.path) == (old.source, old.text, old.path)
    PROMPTS.project_path(tmp_path, name).parent.mkdir(parents=True, exist_ok=True)
    PROMPTS.project_path(tmp_path, name).write_text("mine\n")
    old = P.resolve(name, tmp_path)
    new = PROMPTS.resolve(name, tmp_path)
    assert (new.source, new.text, new.path) == (old.source, old.text, old.path)


def test_a_marked_copy_keeps_the_files_line_numbers(loader, tmp_path):
    p = tmp_path / "bad.j2"
    text = loader.ejected_text("alpha").replace("hello", "{% if x %}hello")
    (problem,) = loader.validate(O.Asset("alpha", text, O.PROJECT, p))
    assert problem.line == 2  # line 1 is the marker


def test_eject_does_not_write_through_a_symlinked_intermediate_directory(tmp_path):
    nested = O.OverlayLoader(
        "export template",
        shipped_dir=tmp_path / "pkg",
        project_subdir="templates/export",
        suffix=".md.j2",
    )
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.md.j2").write_text("x\n")
    repo = tmp_path / "repo"
    (repo / ".ddflow").mkdir(parents=True)
    (repo / "elsewhere").mkdir()
    (repo / ".ddflow" / "templates").symlink_to(repo / "elsewhere")
    with pytest.raises(O.OverlayError, match="symlink"):
        nested.eject(repo, "a")
    assert not list((repo / "elsewhere").rglob("*"))


# -- B-uni-overlay.2: prompts.resolve runs on the loader, with its own words ---------------


def test_prompts_resolve_pins_precedence_provenance_and_messages(tmp_path, monkeypatch):
    """Literal expectations, so the migration cannot hide behind comparing a loader with
    itself: layer, path, relative-path handling and every message `resolve` ever said."""
    built = P.builtin_dir() / "review_system.md"
    got = P.resolve("review_system", tmp_path)
    assert (got.source, got.path, got.kind) == ("builtin", built, "template")
    assert P.resolve("review_system").source == "builtin"  # no repo: the shipped default
    mine = tmp_path / ".ddflow" / "prompts" / "review_system.md"
    mine.parent.mkdir(parents=True)
    mine.write_text("mine\n")
    got = P.resolve("review_system", tmp_path)
    assert (got.source, got.path, got.text) == ("project", mine, "mine\n")
    (tmp_path / "rel.md").write_text("configured\n")
    got = P.resolve("review_system", tmp_path, {"review_system": "rel.md"})
    assert (got.source, got.path, got.text) == ("config", tmp_path / "rel.md", "configured\n")
    got = P.resolve("review_system", None, {"review_system": str(tmp_path / "rel.md")})
    assert (got.source, got.path) == ("config", tmp_path / "rel.md")
    assert P.resolve("review_system", tmp_path, {"review_system": ""}).source == "project"
    with pytest.raises(P.TemplateError) as exc:
        P.resolve("review_system", tmp_path, {"review_system": "nope.md"})
    assert str(exc.value) == (
        f"[prompts].review_system points at {tmp_path / 'nope.md'}, which does not exist"
    )
    with pytest.raises(P.TemplateError) as exc:
        P.resolve("not_a_template", tmp_path)
    assert str(exc.value).startswith("unknown template 'not_a_template'. Known: review_system, ")
    monkeypatch.setattr(P, "builtin_dir", lambda: tmp_path / "no-such-pkg")
    mine.write_text("a literal {{ never closed\n")  # resolving does not parse the text
    assert P.resolve("review_system", tmp_path).text == "a literal {{ never closed\n"
    mine.unlink()
    with pytest.raises(P.TemplateError) as exc:
        P.resolve("review_system", tmp_path)
    assert str(exc.value) == "shipped template review_system.md is missing from the package"


def test_an_undecodable_prompt_is_a_template_error_not_a_traceback(tmp_path):
    """B-undecodable-prompt: `resolve` read the file raw, so a binary override escaped as
    UnicodeDecodeError through callers that promise a TemplateError (the review gate)."""
    (tmp_path / "bad.md").write_bytes(b"\xff\xfe")
    with pytest.raises(P.TemplateError, match=r"could not read .*bad\.md"):
        P.resolve("review_system", tmp_path, {"review_system": "bad.md"})
    mine = tmp_path / ".ddflow" / "prompts" / "review_system.md"
    mine.parent.mkdir(parents=True)
    mine.write_bytes(b"\xff")
    with pytest.raises(P.TemplateError, match="could not read"):
        P.resolve("review_system", tmp_path)


# -- B-uni-overlay.3: export/templates.py runs on the loader, messages unchanged -------------


def test_the_loader_names_paths_and_the_force_flag_as_its_caller_asks(tmp_path):
    shipped = tmp_path / "pkg"
    shipped.mkdir()
    (shipped / "a.j2").write_text("x\n")
    ld = O.OverlayLoader(
        "widget",
        shipped_dir=shipped,
        project_subdir="w",
        suffix=".j2",
        display=lambda repo, p: "<" + p.relative_to(repo).as_posix() + ">",
        force_flag="--force",
    )
    repo = tmp_path / "repo"
    ld.eject(repo, "a")
    ld.project_path(repo, "a").write_text("mine\n")
    with pytest.raises(O.OverlayError) as e:
        ld.eject(repo, "a")
    assert str(e.value).startswith("<.ddflow/w/a.j2> was edited") and "(--force replaces" in str(
        e.value
    )
    assert e.value.refused


def test_export_eject_refusals_keep_their_exit_codes_and_words(tmp_path):
    kind = R.names()[0]
    T.eject(tmp_path, kind)
    p = T.project_path(tmp_path, kind)
    p.write_text(p.read_text() + "edit\n")
    with pytest.raises(T.ExportError) as e:
        T.eject(tmp_path, kind)
    assert e.value.code == 3
    assert str(e.value) == (
        f".ddflow/templates/export/{kind}.md.j2 was edited since it was ejected; refusing to "
        "overwrite it (--force replaces it with the shipped default, losing your edits)"
    )
    p.unlink()
    p.mkdir()
    with pytest.raises(T.ExportError) as e:
        T.eject(tmp_path, kind)
    assert e.value.code == 2 and "could not read" in str(e.value)
