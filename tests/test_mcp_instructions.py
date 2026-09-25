"""The instruction block the MCP server hands the agent on connect.

This is the only place the server speaks unprompted, so it is also the only lever that
makes a project's workflow arrive in the model's context without anyone remembering to
paste it. Three things have to hold:

1. it **bootstraps** — an unadopted repository is told how to adopt, an adopted one is
   told the loop, the pipeline and what to record;
2. it **names the companion tools** the gates depend on, and says which are missing
   here, because a gate with nothing behind it passes on one model's unaided assertion;
3. it is a **template**, not a string literal — a developer changes the workflow by
   editing text, not by forking the package.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

ROOT = Path(__file__).resolve().parents[1]
OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _instructions(repo: Path) -> str:
    from ddflow.surfaces.mcp import _instructions as fn

    return fn(repo)


# -- bootstrap ------------------------------------------------------------------------


def test_an_unadopted_repository_is_told_how_to_adopt_and_nothing_else(repo):
    text = _instructions(repo)
    assert "does not use ddflow yet" in text
    assert "ddflow_setup" in text
    assert "say nothing about it and carry on" in text, (
        "an agent connected to a project that did not ask for a work queue must not "
        "start talking about one"
    )


def test_an_adopted_repository_is_told_the_loop_and_the_pipeline(repo):
    run_cli(repo, "init")
    text = _instructions(repo)
    for must in ("ddflow_brief", "ddflow_claim", "ddflow_next", "ddflow_complete"):
        assert must in text, must
    # The pipeline is rendered from the project's OWN configuration, not retyped — the
    # mistake `render.board` made once, which showed ten columns to a project that had
    # trimmed its pipeline to three.
    for gate in ("research", "rubber_duck", "critic", "standards", "bug_hunt", "dedupe"):
        assert gate in text, gate


def test_the_pipeline_shown_is_the_project_s_own(repo):
    run_cli(repo, "init")
    run_cli(repo, "config", "--set", "gates.task_pipeline", "implement,unit_tests,merge")
    text = _instructions(repo)
    line = next((ln for ln in text.splitlines() if " implement ·" in ln), "")
    assert line, text[:600]
    assert "rubber_duck" not in line, f"a trimmed pipeline must not show the default: {line}"


# -- reporting ------------------------------------------------------------------------


def test_the_agent_is_told_what_to_record_and_when(repo):
    """Each recording duty, with the tool that performs it."""
    run_cli(repo, "init")
    text = _instructions(repo)
    for tool in (
        "ddflow_session_prompt",
        "ddflow_decision_add",
        "ddflow_bug_found",
        "ddflow_lesson_add",
        "ddflow_research_add",
    ):
        assert tool in text, f"{tool} is not mentioned, so nobody will call it"
    assert "verbatim" in text, "a summarised prompt reconstructs the summary"
    assert "unavailable" in text and "never" in text.lower()


# -- the companion tools --------------------------------------------------------------


def test_every_companion_is_named_with_the_gates_it_serves(repo):
    run_cli(repo, "init")
    text = _instructions(repo)
    for name in ("roborev", "codeguide", "context7", "OptMem"):
        assert name in text, f"{name} is not named, so it will not be used"
    assert "ddflow_companions" in text


def test_the_ones_missing_HERE_are_called_out_with_what_to_do(repo):
    """Naming all four is not enough, and neither is reporting which are absent.

    The operator's instruction was explicit: the tool must not install anything itself,
    and the agent must be told to PROPOSE installing them so the operator can decide.
    A report with no action attached is a report nobody acts on — which is how a gate
    ends up with nothing behind it while everyone believes it is covered.
    """
    run_cli(repo, "init")
    text = _instructions(repo)
    assert "Not wired up here" in text, text[-800:]

    block = text.split("Not wired up here")[1]
    assert "propose" in block.lower(), "the agent must be told to raise it, not just know it"
    assert "If they agree" in block, "and what to do when the operator says yes"
    assert "without asking" in block, "and that it must not install unilaterally"
    assert "unavailable" in block, "and what to do when the operator says no"


def test_each_missing_companion_carries_its_install_command(repo):
    """Inline, so proposing it does not cost a second tool call.

    An instruction that says "find out how to install it and then ask" is one the agent
    defers; one that already has the command is one it can act on in the same turn.
    """
    run_cli(repo, "init")
    block = _instructions(repo).split("Not wired up here")[1]
    lines = [ln for ln in block.splitlines() if ln.startswith("- **")]
    assert len(lines) >= 3, block[:600]
    for ln in lines:
        assert "Serves:" in ln and "Install:" in ln, ln
    # One per line: `trim_blocks` once collapsed all three onto one unreadable line.
    assert any("roborev" in ln for ln in lines), lines
    assert not any(ln.count("**") > 2 for ln in lines), (
        f"two companions rendered onto one line: {lines}"
    )


def test_a_registered_companion_is_not_listed_as_missing(repo):
    from ddflow.services import companions as CO

    run_cli(repo, "init")
    comp = next(c for c in CO.load(repo) if c.id == "context7")
    CO.register(repo, comp, "claude")
    text = _instructions(repo)
    missing = (
        text.split("Not wired up here")[1].split("\n")[0] if "Not wired up here" in text else ""
    )
    assert "context7" not in missing, f"it IS wired up: {missing!r}"


# -- it is a template -----------------------------------------------------------------


def test_the_whole_instruction_is_an_editable_file(repo):
    """The point of the exercise: change the workflow by editing text."""
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "prompts", "eject", "mcp_instructions")
    assert code == OK, err
    ejected = repo / ".ddflow" / "prompts" / "mcp_instructions.md"
    assert ejected.is_file(), out

    ejected.write_text("Our house rules: {% if adopted %}ADOPTED{% endif %}. Ask Priya.\n")
    text = _instructions(repo)
    assert text == "Our house rules: ADOPTED. Ask Priya.", text


def test_a_config_path_can_point_the_template_anywhere(repo, tmp_path):
    run_cli(repo, "init")
    shared = tmp_path / "house-prompts" / "mcp.md"
    shared.parent.mkdir(parents=True)
    shared.write_text("From the shared prompts repository.\n")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text() + f'\n[prompts]\nmcp_instructions = "{shared}"\n')
    assert _instructions(repo) == "From the shared prompts repository."


def test_a_broken_override_says_so_instead_of_going_quiet(repo):
    """A silent fallback would give the operator the default while they believed their
    edit was live — and this is the one surface where nobody would ever check."""
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text() + '\n[prompts]\nmcp_instructions = "nope.md"\n')
    text = _instructions(repo)
    assert "could not be loaded" in text and "nope.md" in text, text
    assert "ddflow_brief" in text, "and it still has to hand over the essentials"


def test_the_template_is_listed_and_shipped(repo):
    run_cli(repo, "init")
    code, out, _ = run_cli(repo, "prompts", "list")
    assert code == OK and "mcp_instructions" in out, out
    assert (ROOT / "ddflow" / "templates" / "prompts" / "mcp_instructions.md").is_file()


# -- it reaches the client, over the real protocol ------------------------------------


def test_the_instructions_arrive_in_the_initialize_response(repo):
    run_cli(repo, "init")
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), "mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )
    try:
        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"},
                    },
                }
            )
            + "\n"
        )
        proc.stdin.flush()
        res = json.loads(proc.stdout.readline())["result"]
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
    body = res.get("instructions", "")
    assert "ddflow_brief" in body and "roborev" in body, body[:400]


def test_the_handshake_does_not_write_to_the_repository(repo):
    """Gathering the state must not adopt the project, create an index, or touch a file.

    Already fixed twice — once for the server's own `EventLog`, once for `Store` — so
    it is pinned here as a property of the handshake rather than of either component.
    """
    before = sorted(p.name for p in repo.iterdir())
    _instructions(repo)
    assert sorted(p.name for p in repo.iterdir()) == before, "the handshake left a mark"
