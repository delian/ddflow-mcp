"""A companion is an MCP server or a command-line tool, and the two are not the same.

Every companion used to be an MCP server by assumption. `entry()` would build a launch
block for any `command`, `companions add` would write it into an agent's config, and the
agent would fail the JSON-RPC handshake the first time a gate reached for the tool — a
registration that reads as done and is not, which is the vacuous-pass class in config
form.

OptMem is the live example that forced the distinction: a genuinely useful memory tool,
recommended on purpose, and not an MCP server. `memo` has no `mcp` subcommand.

The three places the distinction has to hold are the three that a `registered`-shaped
test would silently get wrong for a tool that can never reach that state:

* `companions add` must REFUSE it, and say why;
* the report must not print an `add` line it knows will not work;
* gate coverage and the default-gap check must treat INSTALLED as the goal state,
  because `registered` is unreachable.

The last one is the one that hides. A `cli` companion would sit in the registry serving
`rules` forever, be installed, and never count toward that gate's coverage — so the
report would say the gate has nothing behind it while the tool it names sits on the
PATH. Same shape as the bug where `gaps` judged it by a state it cannot reach.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.services import companions as CO

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _companion(**over) -> CO.Companion:
    spec = {
        "id": "probe_target",
        "title": "Probe target",
        "detect": ["python3", "-c", "pass"],
        "install": "pip install nothing",
        "gates": ["rules"],
        "command": "probe_target",
    }
    spec.update(over)
    return CO.Companion(**spec)


# -- the type itself -------------------------------------------------------------------


def test_a_companion_is_an_mcp_server_unless_it_says_otherwise():
    """The default has to stay `mcp`, or every existing registry entry and every
    project's `.ddflow/companions.toml` silently stops being registrable."""
    assert _companion().kind == "mcp"
    assert _companion().is_mcp is True


def test_a_cli_companion_has_no_mcp_entry_and_refuses_to_invent_one():
    """`entry()` would happily produce a plausible-looking block from any `command`.
    Written into a config, it fails at the worst moment — mid-task, as an agent reaches
    for the tool a gate told it to use."""
    c = _companion(kind="cli")
    assert c.is_mcp is False
    with pytest.raises(ValueError) as e:
        c.entry()
    assert "not an MCP server" in str(e.value)
    # and it names the way out, rather than only the refusal
    assert "pip install nothing" in str(e.value)


def test_an_mcp_companion_still_builds_its_entry():
    e = _companion(command="x", args=["y"]).entry()
    assert e == {"command": "x", "args": ["y"]}


def test_an_unknown_kind_is_an_error_not_a_silent_non_server(tmp_path, monkeypatch):
    """`kind = "MCP"` is neither of the two values, so `is_mcp` is False and the
    companion quietly stops being registrable with no error anywhere. The loader
    rejects an unknown FIELD; it cannot know this field has a closed vocabulary."""
    repo = tmp_path / "r"
    (repo / ".ddflow").mkdir(parents=True)
    (repo / ".ddflow" / "companions.toml").write_text(
        '[[companion]]\nid = "typo"\nkind = "MCP"\ncommand = "x"\n'
    )
    with pytest.raises(ValueError) as e:
        CO.load(repo)
    assert "kind" in str(e.value) and "typo" in str(e.value)


# -- the three places it has to hold ---------------------------------------------------


def test_gate_coverage_counts_an_installed_cli_tool(tmp_path):
    """The one that hides.

    `registered` is a state a cli companion cannot reach. Judging coverage by it would
    report `rules` as having nothing behind it while the tool serving `rules` sits on
    the PATH — the report contradicting the line directly above it.
    """
    cli = CO.Status(_companion(id="optmem", kind="cli", gates=["rules"]), True, "", "")
    cover = CO.gate_coverage(tmp_path, [cli], ["rules", "implement"])
    assert cover["rules"] == ["optmem"], "an installed cli companion did not count"
    assert cover["implement"] == []


def test_gate_coverage_does_not_count_a_cli_tool_that_is_absent_or_unknown(tmp_path):
    """The other half, and the reason this is not just `if not is_mcp: count it`.

    A tool that is not there covers nothing, and one we could not probe is exactly as
    unknown as before we asked — counting either would be the unavailable-as-success
    class, which is the thing gate coverage exists to expose.
    """
    for installed in (False, None):
        st = CO.Status(_companion(id="optmem", kind="cli", gates=["rules"]), installed, "", "")
        cover = CO.gate_coverage(tmp_path, [st], ["rules"])
        assert cover["rules"] == [], f"a cli companion with installed={installed!r} counted"


def test_an_unregistered_mcp_server_still_does_not_count(tmp_path):
    """Unchanged behaviour, asserted so the fix above cannot quietly widen to servers:
    an MCP server that is installed but not registered is one command away from being
    usable and is NOT usable yet."""
    st = CO.Status(_companion(id="roborev", gates=["rules"]), True, "", "")
    assert CO.gate_coverage(tmp_path, [st], ["rules"])["rules"] == []


def test_companions_add_refuses_a_cli_tool_and_says_why(repo):
    """Explicitly asking for it is different from it being swept up by a default run:
    the operator asked for something specific, so answer the question."""
    run_cli(repo, "init")
    (repo / ".ddflow" / "companions.toml").write_text(
        '[[companion]]\nid = "clitool"\nkind = "cli"\ncommand = "true"\n'
        'detect = ["true"]\ninstall = "brew install clitool"\ndefault = true\n'
    )
    code, _out, err = run_cli(repo, "companions", "add", "--id", "clitool", "--force")
    assert code == REFUSED, f"expected a refusal, got {code}: {err}"
    assert "not an MCP server" in err
    assert "brew install clitool" in err, "refused without naming the way forward"


def test_a_default_add_does_not_sweep_up_a_cli_tool(repo):
    """With no `--id`, `add` takes every installed default. A cli tool among them would
    turn the whole call into a refusal and block the servers that CAN be registered."""
    run_cli(repo, "init")
    (repo / ".ddflow" / "companions.toml").write_text(
        '[[companion]]\nid = "clitool"\nkind = "cli"\ncommand = "true"\n'
        'detect = ["true"]\ninstall = "brew install clitool"\ndefault = true\n'
    )
    code, _out, err = run_cli(repo, "companions", "add")
    assert code != REFUSED, f"a cli companion blocked the default add: {err}"


def test_the_report_never_offers_to_register_a_cli_tool(repo):
    """An instruction the tool knows will fail is worse than no instruction: whoever
    follows it gets a config entry that breaks a gate later, far from here."""
    run_cli(repo, "init")
    (repo / ".ddflow" / "companions.toml").write_text(
        '[[companion]]\nid = "clitool"\nkind = "cli"\ncommand = "true"\n'
        'detect = ["true"]\ninstall = "brew install clitool"\ndefault = true\n'
    )
    _code, out, _err = run_cli(repo, "companions")
    assert "clitool" in out
    assert "companions add --id clitool" not in out


def test_the_json_surface_says_which_kind_each_one_is(repo):
    """An agent reading JSON has to be able to tell them apart too, or it writes the
    same broken config a human would have been warned off."""
    run_cli(repo, "init")
    code, out, _err = run_cli(repo, "--json", "companions")
    assert code in (OK, NOTHING)
    import json

    kinds = {c["id"]: c["kind"] for c in json.loads(out)["companions"]}
    assert kinds["optmem"] == "cli", kinds
    assert kinds["sequential"] == "mcp", kinds


# -- the registry entries this was built for -------------------------------------------


def test_the_shipped_registry_recommends_sequential_thinking_and_optmem(tmp_path):
    reg = {c.id: c for c in CO.load(tmp_path)}
    assert "sequential" in reg, "sequential-thinking is not in the shipped registry"
    assert reg["sequential"].kind == "mcp"
    assert reg["sequential"].default is True, "recommended means proposed by adopt"
    assert "optmem" in reg
    assert reg["optmem"].kind == "cli", "OptMem has no MCP mode; claiming one would lie"


def test_every_shipped_companion_declares_gates_that_exist(tmp_path):
    """A companion serving a gate nobody runs is a recommendation that buys nothing —
    the same inert-requirement shape `complete` already refuses for `[gates].required`.
    """
    from ddflow.config import Config

    cfg = Config.load(tmp_path)
    known = set(cfg.gates.task_pipeline) | set(cfg.gates.phase_pipeline)
    for c in CO.load(tmp_path):
        unknown = [g for g in c.gates if g not in known]
        assert not unknown, f"companion {c.id} serves unknown gate(s) {unknown}"


def test_every_shipped_companion_has_an_install_line_and_a_url(tmp_path):
    """The report prints both, and "ask the operator, then: " followed by nothing is
    how a recommendation becomes noise."""
    for c in CO.load(tmp_path):
        assert c.install.strip(), f"{c.id} has no install command"
        assert c.url.startswith("http"), f"{c.id} has no URL to read before installing"


# -- detection must not be an installer ------------------------------------------------


def test_no_detection_probe_installs_anything(tmp_path):
    """`npx -y` FETCHES AND INSTALLS the package in order to run it.

    As a detect probe that means `ddflow companions` — which the MCP handshake calls,
    and `adopt` calls, and a session start can call — downloads software onto the
    operator's machine. The module's first stated rule is that nothing is installed
    automatically, and three shipped entries broke it.

    It also destroys the ANSWER. With `-y` the probe stops meaning "is this installed
    here" and starts meaning "can npm reach the registry" — a question that cannot be
    answered no on any networked machine, so the companion counts toward its gates'
    coverage everywhere. An always-yes detector is the vacuous-pass class.

    *Found by roborev on 031313a, CONFIRMED. `context7` and `memory` had the same shape
    and predate this change; the class is swept, not just the new entry.*
    """
    for c in CO.load(tmp_path):
        assert "-y" not in c.detect, (
            f"companion {c.id!r} detects with `npx -y`, which installs the package. "
            f"Use `npx --no-install`."
        )
        assert "--yes" not in c.detect, f"companion {c.id!r} detects with `npx --yes`"
        for installer in ("install", "add", "pull", "get"):
            assert installer not in c.detect, (
                f"companion {c.id!r} has {installer!r} in its detect probe, which runs "
                f"on every `ddflow companions` call"
            )


def test_an_unreadable_registry_is_an_ERROR_not_an_empty_report(repo):
    """The closed-vocabulary check raises, and the CLI must not answer with a traceback.

    Worse was the handshake: `_instructions` wraps the scan in `except Exception: pass`,
    so one typo in `.ddflow/companions.toml` silently deleted the entire companions and
    gate-gap section — "nobody could look" rendering as "no gaps", inside the report
    whose whole purpose is to expose exactly that.
    """
    run_cli(repo, "init")
    (repo / ".ddflow" / "companions.toml").write_text(
        '[[companion]]\nid = "typo"\nkind = "MCP"\ncommand = "x"\n'
    )
    code, _out, err = run_cli(repo, "companions")
    assert code == FAIL, f"an unreadable registry did not fail: {code}"
    assert "kind" in err and "typo" in err, err
    assert "Traceback" not in err, "answered with a traceback instead of the message"


def test_the_handshake_says_the_registry_is_unreadable_rather_than_going_quiet(repo):
    run_cli(repo, "init")
    (repo / ".ddflow" / "companions.toml").write_text(
        '[[companion]]\nid = "typo"\nkind = "MCP"\ncommand = "x"\n'
    )
    # Asserted against the SPECIFIC signal, not against the word "companion" appearing
    # somewhere in six thousand characters of standing instructions. The first version
    # of this test matched the unrelated `research-companions` paragraph and passed
    # while the section it was about was silently absent -- the exact failure it exists
    # to catch, in the test that catches it.
    from ddflow.surfaces.mcp import _instruction_vars, _instructions

    todo = " ".join(_instruction_vars(repo).get("setup_todo", []))
    assert "companion registry could not be read" in todo.lower(), (
        f"the handshake went quiet about an unreadable registry; setup_todo was: {todo!r}"
    )
    assert "typo" in todo or "MCP" in todo, "reported the failure without naming the cause"
    assert _instructions(repo), "the handshake itself must still render"


def test_the_handshake_never_tells_an_agent_to_register_a_cli_tool(repo):
    """`missing_companions` judged by `registered`, which a cli companion cannot reach,
    so `optmem` was in it on every connection — and the template presents that list as
    "something to DO", instructing the agent to run `ddflow_companions_add`. The agent
    obeys the server's own instructions and gets a refusal."""
    run_cli(repo, "init")
    (repo / ".ddflow" / "companions.toml").write_text(
        '[[companion]]\nid = "clitool"\nkind = "cli"\ncommand = "true"\n'
        'detect = ["true"]\ninstall = "brew install clitool"\ndefault = true\n'
    )
    from ddflow.surfaces.mcp import _instructions

    # Asserted against the RENDERED TEXT, which is what an agent receives. The first
    # version of this test read `unregistered_companions` — a variable the template
    # referenced nowhere, so the fix computed three new lists, rendered none of them,
    # and this test went green over an instruction block that had not changed a word.
    # A test that reads an intermediate value proves the intermediate value.
    text = _instructions(repo)
    assert "clitool" in text, "the companion is not mentioned at all; the test proves nothing"
    block = text[text.index("clitool") - 400 : text.index("clitool") + 400]
    assert "companions_add" not in block, f"the handshake offers to register a cli tool:\n{block}"


def test_the_handshake_calls_an_unprobed_companion_UNCHECKED_not_missing(repo):
    """The three-valued collapse, one layer out.

    `_instruction_vars` scans with `probe=False` so a session start does not wait on
    `npx`, which leaves every `installed` as None. Treating that as "not usable" made
    the handshake list tools as missing on every connection — including ones sitting on
    the PATH — and report their gates as having nothing behind them.
    """
    run_cli(repo, "init")
    from ddflow.surfaces.mcp import _instruction_vars, _instructions

    v = _instruction_vars(repo)
    assert v["unchecked_companions"], "nothing was reported as unchecked despite probe=False"
    assert not v["missing_companions"], (
        f"unprobed companions were reported as KNOWN missing: "
        f"{[c['id'] for c in v['missing_companions']]}"
    )
    # The rendered claim, not a heading: every companion is listed with a state word,
    # and an unprobed one must say "not checked" rather than "not installed". A
    # separate heading was tried and removed — it split the install command away from
    # the companion it belonged to, leaving the agent nothing to act on in the turn
    # where it proposes.
    text = _instructions(repo)
    assert "[not checked]" in text, text[-900:]
    assert "[not installed]" not in text, "an unprobed companion was rendered as known-absent"
    # ...and the command is still there to act on.
    assert "Install: `" in text


def test_a_gate_is_not_a_gap_when_its_companion_was_never_probed(repo):
    """ "Nobody looked" must not render as "no tool behind this gate". `rules` is served
    only by companions that need a probe, so with `probe=False` it was in `gate_gaps`
    on every connection — the handshake asserting a gap on the strength of not having
    checked, inside the report whose purpose is to expose exactly that."""
    run_cli(repo, "init")
    from ddflow.surfaces.mcp import _instruction_vars

    assert "rules" not in _instruction_vars(repo)["gate_gaps"], (
        "an unprobed companion's gate was reported as having nothing behind it"
    )


def test_install_is_a_command_and_caveats_live_in_note(tmp_path):
    """`install` is printed under "ask the operator, then:" and read as something to
    paste. A sentence there renders as an instruction nobody can run — which is what
    the OptMem entry did before `note` existed, and what a trailing `# comment` on the
    sequential entry would have done to anyone pasting it into a non-shell context.

    Checked structurally rather than by taste: an install line with sentence
    punctuation in it is prose wearing a command's field.
    """
    for c in CO.load(tmp_path):
        assert c.install.strip(), f"{c.id} has no install command"
        assert "\n" not in c.install, f"{c.id}'s install spans lines"
        assert "—" not in c.install and " - " not in c.install, (
            f"{c.id}'s install reads as prose, not a command: {c.install!r}. "
            f"Caveats belong in `note`."
        )
        assert "#" not in c.install, (
            f"{c.id}'s install carries a trailing comment; put it in `note`"
        )


def test_roborev_is_a_cli_tool_because_it_has_no_mcp_mode(tmp_path):
    """The shipped registry claimed `roborev mcp`, and that subcommand does not exist.

        $ roborev mcp
        Error: unknown command "mcp" for "roborev"

    So `ddflow companions add --id roborev` wrote `{"command": "roborev", "args":
    ["mcp"]}` into the operator's `.mcp.json`, and the agent spawning it got that error
    instead of a JSON-RPC handshake — the precise failure the `kind` field was added to
    prevent, in the flagship entry, added by the same change that added the field.

    The `detect` probe is why it survived: `roborev --version` proves the BINARY exists
    and says nothing about whether the LAUNCH command works. Detection and launch were
    different code paths and only one of them was ever exercised.

    *Found by the operator asking "is roborev an MCP or a tool or both?" — a question,
    not a bug report.*
    """
    reg = {c.id: c for c in CO.load(tmp_path)}
    assert reg["roborev"].kind == "cli", (
        "roborev is registered as an MCP server; `roborev mcp` is not a command"
    )
    assert "mcp" not in reg["roborev"].args, reg["roborev"].args


def test_no_mcp_companion_launches_a_subcommand_its_probe_never_exercises(tmp_path):
    """The narrow, sound half of the class — stated as what it is rather than dressed up
    as a general check.

    When a companion's `detect` invokes the SAME binary as its `command`, the probe can
    only vouch for the launch if it exercises the same subcommand. `roborev --version`
    vouching for `roborev mcp` is the shape that shipped.

    It checks the LAST non-flag argument — the thing being launched — not the first.
    The first is wrong: `codeguide` launches `docker run <image>` and probes with
    `docker image inspect <image>`, where `run` is docker's subcommand and the IMAGE is
    what identifies the target. Inspecting that image genuinely implies running it will
    find it, so demanding the first token match would forbid a correct read-only probe.

    (The first version of this check did exactly that, and its docstring claimed the
    codeguide carve-out it had not implemented — the same
    comment-promises-what-the-code-does-not-do shape being checked for elsewhere in
    this suite. Caught by the check going red on a registry entry that is correct.)

    It stays narrow on purpose, and the general case is not mechanisable: nothing
    static can tell whether a binary speaks JSON-RPC. The registry header carries that
    instruction — LAUNCH it before marking it `mcp` — and `ddflow companions --verify`
    is filed as B114 to do it for real.
    """
    for c in CO.load(tmp_path):
        if not c.is_mcp or not c.detect or c.detect[0] != c.command:
            continue
        target = [a for a in c.args if not a.startswith("-")]
        if not target:
            continue
        assert target[-1] in c.detect, (
            f"{c.id} launches `{c.command} {' '.join(c.args)}` but probes with "
            f"`{' '.join(c.detect)}` — the probe never exercises {target[-1]!r}, so a "
            f"passing detection says nothing about whether the launch works"
        )
