"""Prompt templates: extend by writing text, not code."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from orchard import prompts as P


def test_every_registered_template_ships_with_the_package():
    for name in P.TEMPLATE_NAMES:
        t = P.resolve(name)
        assert t.source == "builtin" and t.text.strip(), f"{name} is missing or empty"


def test_a_project_template_overrides_the_shipped_one(repo):
    run_cli(repo, "init")
    d = repo / ".orchard" / "prompts"
    d.mkdir(parents=True, exist_ok=True)
    (d / "review_system.md").write_text("MY PROJECT PROMPT\n")
    t = P.resolve("review_system", repo)
    assert t.source == "project" and "MY PROJECT PROMPT" in t.text


def test_prompt_override_from_config_is_honoured(repo, tmp_path):
    """Covers the four `[prompts].*` knobs, which the name-based dead-knob scan cannot
    see because they are read via `dataclasses.fields`."""
    custom = tmp_path / "elsewhere.md"
    custom.write_text("FROM AN EXPLICIT CONFIG PATH\n")
    for name in P.TEMPLATE_NAMES:
        t = P.resolve(name, repo, {name: str(custom)})
        assert t.source == "config", f"{name}: config override ignored"
        assert "EXPLICIT CONFIG PATH" in t.text


def test_a_configured_override_that_is_missing_is_an_error_not_a_fallback(repo):
    """The operator asked for THEIR prompt; silently serving the default would leave
    them believing an edit was live when it was not."""
    with pytest.raises(P.TemplateError, match="does not exist"):
        P.resolve("review_system", repo, {"review_system": "/no/such/file.md"})


def test_an_unknown_template_name_fails_loudly():
    with pytest.raises(P.TemplateError, match="unknown template"):
        P.resolve("not_a_real_template")


def test_an_undefined_variable_raises_rather_than_rendering_empty():
    """A prompt silently missing the diff it was meant to carry is the vacuous review
    in template form: the model dutifully reviews nothing and reports no findings."""
    with pytest.raises(P.TemplateError):
        P.render(P.resolve("review_user"), intent="x")


# -- the stdlib renderer, which is what runs where Jinja2 is not installed -------------


def test_stdlib_renderer_handles_the_documented_subset():
    r = P._render_stdlib
    assert r("hello {{ name }}", {"name": "world"}) == "hello world"
    assert r("{% if flag %}yes{% endif %}", {"flag": True}) == "yes"
    assert r("{% if flag %}yes{% else %}no{% endif %}", {"flag": False}) == "no"
    assert r("{% for x in xs %}[{{ x }}]{% endfor %}", {"xs": [1, 2]}) == "[1][2]"
    assert r("{{ a.b }}", {"a": {"b": "nested"}}) == "nested"


def test_stdlib_renderer_is_strict_about_unknown_variables():
    with pytest.raises(P.TemplateError, match="not defined"):
        P._render_stdlib("{{ nope }}", {"other": 1})


def test_stdlib_renderer_matches_jinja_on_the_shipped_templates():
    """The shipped templates must render identically under both engines, or a project
    that installs Jinja2 silently gets different prompts than one that does not."""
    jinja2 = pytest.importorskip("jinja2")
    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined, trim_blocks=True, lstrip_blocks=True, autoescape=False
    )
    cases = {
        "review_system": {"extra_rules": "be terse"},
        "review_user": {
            "intent": "i",
            "context": "c",
            "diff": "d",
            "chunk_index": 1,
            "chunk_total": 2,
        },
        "session_brief_header": {"recovery": []},
    }
    for name, vars in cases.items():
        text = P.resolve(name).text
        got = P._render_stdlib(text, vars)
        want = env.from_string(text).render(**vars)
        assert got.split() == want.split(), (
            f"{name} renders differently under the two engines:\n"
            f"stdlib: {got[:200]!r}\njinja:  {want[:200]!r}"
        )


def test_eject_writes_editable_copies(repo):
    run_cli(repo, "init")
    code, _out, _ = run_cli(repo, "prompts", "eject")
    assert code == 0
    for name in P.TEMPLATE_NAMES:
        assert (repo / ".orchard" / "prompts" / f"{name}.md").is_file()
    assert P.resolve("review_system", repo).source == "project"


def test_eject_does_not_clobber_without_force(repo):
    run_cli(repo, "init")
    run_cli(repo, "prompts", "eject", "review_system")
    path = repo / ".orchard" / "prompts" / "review_system.md"
    path.write_text("MINE\n")
    run_cli(repo, "prompts", "eject", "review_system")
    assert path.read_text() == "MINE\n"
    run_cli(repo, "prompts", "eject", "review_system", "--force")
    assert path.read_text() != "MINE\n"


def test_the_review_user_template_is_actually_used(repo, tmp_path, monkeypatch):
    """An override that reports success and changes nothing is the worst kind.

    `review()` resolved the `review_user` template and then built the message inline
    anyway, so both override paths silently did nothing. Caught by ruff's F841 on the
    unused local — a lint rule finding a behavioural bug.
    """
    from orchard.reviewer import Reviewer, review

    custom = tmp_path / "u.md"
    custom.write_text("SENTINEL-TEMPLATE {{ intent }} :: {{ diff }}\n")

    seen: list[str] = []

    def fake_chat(rev, system, user, timeout_s):
        seen.append(user)
        return "STATUS: NO FINDINGS", ""

    monkeypatch.setattr("orchard.reviewer._chat", fake_chat)
    review(
        Reviewer(name="r", base_url="http://x/v1", model="m"),
        "diff --git a/x b/x\n+y\n",
        intent="my-intent",
        repo=repo,
        prompt_overrides={"review_user": str(custom)},
    )
    assert seen, "the reviewer never sent a message"
    assert "SENTINEL-TEMPLATE" in seen[0], (
        f"the configured template was ignored; message began: {seen[0][:120]!r}"
    )
    assert "my-intent" in seen[0]
