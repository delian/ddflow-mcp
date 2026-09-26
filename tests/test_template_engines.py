"""Both rendering engines, held to the same behaviour.

`services.prompts.render` has two implementations: Jinja2 when it is importable, and
`_render_stdlib` — a regex engine covering Jinja's common subset — when it is not. The
second exists so ddflow keeps working in a stripped environment, and that is a property
worth having.

What it is not worth having is a SECOND, DIFFERENT template language nobody tests. On
2026-09-26 the shipped `mcp_instructions.md` rendered correctly under Jinja2 and failed
under the fallback, and the whole handshake for an unadopted repository — the first
thing a new user ever sees — degraded to:

    ddflow's instruction template could not be loaded: template variable
    'actionable_companions' is not defined.

Nobody noticed because `jinja2` was not a declared dependency: developers had it in
their ambient interpreter and the project venv did not, so `python -m pytest` was green
and `uv run pytest` was red, on the same commit. Two divergences caused it, both now
fixed and both pinned below:

* `{% for %}` over an absent name was FATAL, while `{% if %}` over one was merely
  false. Since the regex engine resolves innermost-first, it evaluated a loop nested
  inside an `{% if adopted %}` that Jinja never enters.
* `{# comment #}` blocks were not stripped at all, so the template's own 34-line
  header was emitted verbatim into the agent's context.

The generalising test is `test_every_shipped_template_renders_under_both_engines`: the
class is "the two engines disagree", and pinning only the two known cases would leave
the next divergence to be found by a user.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest

from ddflow.services import prompts as P

ENGINES = ("jinja2", "stdlib")


@pytest.fixture(params=ENGINES)
def engine(request, monkeypatch):
    """Render through one engine or the other, by name.

    The fallback is selected by making `import jinja2` raise, which is what actually
    happens in the environment it exists for. Patching `render` itself would test a
    different function than the one that ships.
    """
    if request.param == "stdlib":
        monkeypatch.setitem(sys.modules, "jinja2", None)
    else:
        pytest.importorskip("jinja2")
    return request.param


def test_the_engine_fixture_actually_selects_the_engine(engine):
    """A parametrisation that silently ran one engine twice would make every test
    below vacuous — the failure mode this whole file exists to prevent."""
    try:
        import jinja2  # noqa: F401

        available = True
    except ImportError:
        available = False
    assert available is (engine == "jinja2"), f"{engine} did not take effect"


# -- the two divergences that shipped ----------------------------------------------------


def test_the_two_engines_agree_about_an_absent_name(engine):
    """Jinja runs with `StrictUndefined`, so an absent name is an ERROR, not an empty
    string. The fallback used to be strict for `{{ x }}`, fatal for `{% for %}` and
    lenient for `{% if %}` — three answers to one question, none of them Jinja's.

    Parity is the property, and Jinja is the reference. This test previously asserted
    the opposite (that a loop over an absent name renders empty) and failed under BOTH
    engines, which is how the wrong premise was caught rather than encoded.
    """
    with pytest.raises(P.TemplateError):
        P.render("{% for x in nope %}{{ x }}{% endfor %}")
    with pytest.raises(P.TemplateError):
        P.render("{{ nope }}")


def test_a_loop_nested_in_a_false_conditional_is_never_evaluated(engine):
    """The exact shape that broke the handshake: a loop the enclosing `{% if %}`
    excludes, over a name that only exists on the branch not taken."""
    out = P.render(
        "{% if shown %}{% for c in absent %}{{ c }}{% endfor %}{% endif %}ok", shown=False
    )
    assert out.strip() == "ok"


def test_comment_blocks_are_stripped(engine):
    """`{# ... #}` is documentation for whoever edits the template. Emitting it put 34
    lines of internal commentary into the model's context — and the commentary
    described states the project was not in, which is worse than noise."""
    assert P.render("{# invisible #}visible").strip() == "visible"


def test_a_multiline_comment_block_is_stripped(engine):
    out = P.render("{#\n  several\n  lines\n#}kept")
    assert "several" not in out
    assert "kept" in out


def test_a_condition_that_is_not_a_bare_name_is_evaluated(engine):
    """`{% if a or b %}` — the third divergence, and the quietest.

    The old engine's `{% if %}` pattern matched a single name, so a compound condition
    matched NOTHING: the tag, its body and its `{% endif %}` were copied into the
    output as literal template source. `mcp_instructions.md` has exactly one such
    condition, which is how raw `{% endif %}` reached a client.
    """
    assert P.render("{% if a or b %}yes{% endif %}", a=False, b=True) == "yes"
    assert P.render("{% if a or b %}yes{% endif %}", a=False, b=False) == ""
    assert P.render("{% if a and b %}yes{% endif %}", a=True, b=True) == "yes"
    assert P.render("{% if not a %}yes{% endif %}", a=False) == "yes"


def test_an_unsupported_construct_raises_rather_than_leaking_source(monkeypatch):
    """A fallback that cannot do something must SAY so.

    The regex engine's failure mode was to emit what it did not recognise, unchanged,
    into text destined for a model's context. An error names the problem and the remedy
    ("install Jinja2"); a silent passthrough delivers `{% endif %}` to an agent as if it
    were an instruction. Stdlib-only: Jinja supports these perfectly well.
    """
    monkeypatch.setitem(sys.modules, "jinja2", None)
    for source in ("{% set x = 1 %}", "{% raw %}x{% endraw %}", "{{ name|upper }}"):
        with pytest.raises(P.TemplateError) as exc:
            P.render(source, name="x")
        assert "Jinja2" in str(exc.value), f"{source}: the error should name the remedy"


def test_an_unclosed_block_is_an_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "jinja2", None)
    with pytest.raises(P.TemplateError, match="unclosed"):
        P.render("{% if a %}dangling", a=True)


# -- the generalising ratchet ------------------------------------------------------------


def test_every_shipped_template_behaves_identically_under_both_engines(repo):
    """The class, not the instance — and iterated, not hand-listed.

    A parity test already existed (`test_prompts.py`), but it named three templates in
    a dict and `mcp_instructions` was never added to it. That is the allowlist-that-
    drifted: the check was real, the newest and largest template simply was not in it.
    This walks the registry, so a template cannot be added without being covered.

    Compares OUTCOMES, not just success: same text, or the same refusal. Templates
    needing variables this does not supply raise under both engines, and agreeing to
    raise is agreement.
    """
    jinja2 = pytest.importorskip("jinja2")
    from ddflow.surfaces import mcp

    variables = mcp._instruction_vars(repo, "")
    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined, trim_blocks=True, lstrip_blocks=True, autoescape=False
    )

    def attempt(fn):
        try:
            return ("ok", fn())
        except Exception:
            return ("raised", None)

    checked = 0
    for tmpl in P.list_all():
        # Bound as defaults: these run immediately, but a late-binding closure over
        # a loop variable is the kind of thing that stops being immediate later.
        mine = attempt(lambda t=tmpl: P._render_stdlib(t.text, variables))
        theirs = attempt(lambda t=tmpl: env.from_string(t.text).render(**variables))
        assert mine[0] == theirs[0], f"{tmpl.name}: the fallback {mine[0]} where Jinja {theirs[0]}"
        if mine[0] == "ok":
            assert mine[1] == theirs[1], (
                f"{tmpl.name} renders differently under the two engines:\n"
                f"fallback: {mine[1][:300]!r}\njinja:    {theirs[1][:300]!r}"
            )
        checked += 1
    assert checked, "no templates were compared, so this proves nothing"


def test_the_unadopted_handshake_is_the_adoption_offer_under_both_engines(engine, repo):
    """The first thing a new user sees, on the path that has no configuration yet.

    Asserted on `_instructions` rather than on the template, because the bug was the
    pair — a variable the adopted branch defines and the early return does not, read by
    an engine that does not short-circuit.
    """
    from ddflow.surfaces import mcp

    text = mcp._instructions(repo)
    assert "does not use ddflow yet" in text, text[:300]
    assert "could not be loaded" not in text, text[:300]


def test_the_handshake_never_leaks_template_source(engine, repo):
    """Whatever the state of the repository, the instructions are prose for a model —
    never the template's own syntax."""
    from ddflow.surfaces import mcp

    for text in (mcp._instructions(repo),):
        for marker in ("{%", "{{", "{#", "#}"):
            assert marker not in text, f"unrendered {marker!r} reached the client: {text[:300]}"


# -- the contract between the variables and the template ---------------------------------


def test_instruction_vars_defines_every_name_the_template_can_reach(repo):
    """The root cause, stated as an invariant rather than as one missing key.

    `_instruction_vars` returns early for an unadopted repository, and the companions
    block that defines `actionable_companions` sits after that return. Under Jinja the
    omission is invisible (the enclosing `{% if adopted %}` is false); under the
    fallback it is fatal. Either way the variable contract should not depend on which
    branch ran.
    """
    import re

    from ddflow.surfaces import mcp

    tmpl = P.resolve("mcp_instructions")
    referenced = set(
        re.findall(r"\{%\s*(?:if|for\s+\w+\s+in)\s+([A-Za-z_][A-Za-z0-9_]*)", tmpl.text)
    )
    referenced |= set(re.findall(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*[}.|]", tmpl.text))
    # Loop variables are bound by their own `{% for %}`, not by the caller.
    bound = set(re.findall(r"\{%\s*for\s+(\w+)\s+in", tmpl.text))

    provided = set(mcp._instruction_vars(repo, ""))  # unadopted: the early-return path
    missing = referenced - bound - provided
    assert not missing, (
        f"mcp_instructions.md reads {sorted(missing)}, which `_instruction_vars` does "
        f"not define on the unadopted path. Seed it in the defaults dict."
    )


# -- differential fuzzing --------------------------------------------------------------


def _templates():
    """Small templates exercising the constructs the shipped ones use, plus the
    whitespace arrangements `trim_blocks`/`lstrip_blocks` are about.

    Enumerated rather than randomised: the interesting axis is WHERE the tag sits
    relative to the newlines and indentation around it, and that space is small enough
    to cover exhaustively. A random generator would spend its budget on the middle of
    it and still miss "tag at end of file with no trailing newline".
    """
    bodies = ["x", "{{ name }}", "a{{ name }}b", ""]
    out = []
    for b in bodies:
        out += [
            "{% if flag %}" + b + "{% endif %}",
            "{% if flag %}" + b + "{% else %}other{% endif %}",
            "{% if not flag %}" + b + "{% endif %}",
            "{% if flag or second %}" + b + "{% endif %}",
            "{% if flag and second %}" + b + "{% endif %}",
            # MIXED precedence. `or` binds loosest, so `a or b and c` is
            # `a or (b and c)` -- which a renderer that split on `and` first would get
            # backwards. Without these the precedence branch of `_truthy` is
            # unexercised and the differential test says nothing about it.
            "{% if flag or second and third %}" + b + "{% endif %}",
            "{% if flag and second or third %}" + b + "{% endif %}",
            "{% if not flag and second %}" + b + "{% endif %}",
            "{% if not flag or second %}" + b + "{% endif %}",
            "{% if flag and not second %}" + b + "{% endif %}",
            "{% for item in items %}" + b + "{% endfor %}",
            "{% for item in items %}{{ item }}{% endfor %}",
            "{% if flag %}{% for item in items %}{{ item }}{% endfor %}{% endif %}",
            "{% for item in items %}{% if flag %}{{ item }}{% endif %}{% endfor %}",
        ]
    # The whitespace axis: tags alone on a line, indented, at the start, at the end,
    # with and without a trailing newline.
    out += [
        "before\n{% if flag %}\nin\n{% endif %}\nafter\n",
        "before\n  {% if flag %}\n  in\n  {% endif %}\nafter\n",
        "{% if flag %}\nin\n{% endif %}",
        "a\n{% for item in items %}\n- {{ item }}\n{% endfor %}\nz\n",
        "  {# c #}  \nkept\n",
        "{# c #}",
        "line\n{# c #}\nline2\n",
        "trailing newline\n",
        "no trailing newline",
        "{{ name }}\n",
    ]
    return out


VARIABLES = {"flag": True, "second": False, "third": True, "name": "N", "items": ["i1", "i2"]}


@pytest.mark.parametrize("source", _templates())
@pytest.mark.parametrize("flag", [True, False])
def test_the_fallback_matches_jinja_character_for_character(source, flag):
    """Differential test: Jinja2 is the reference implementation, the fallback must
    reproduce it exactly.

    Every divergence that broke 0.1.1 was a case somebody had not thought to check by
    hand. Comparing against the real engine does not require thinking of the case —
    only of the construct.
    """
    jinja2 = pytest.importorskip("jinja2")

    variables = {**VARIABLES, "flag": flag}
    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined, trim_blocks=True, lstrip_blocks=True, autoescape=False
    )
    want = env.from_string(source).render(**variables)
    got = P._render_stdlib(source, variables)
    assert got == want, (
        f"engines disagree on {source!r} (flag={flag}):\nfallback: {got!r}\njinja:    {want!r}"
    )
