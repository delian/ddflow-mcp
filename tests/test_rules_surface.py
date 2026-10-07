"""The rules file is CHECKED, not just written once.

`adopt` writes a managed block into `AGENTS.md` and nothing ever looked again. A deleted
file, a stripped block, or a block written by an older ddflow all left the agent reading
project rules that were absent, incomplete or wrong — while every surface reported the
project as adopted, because adoption is judged by `.ddflow/config.toml` existing. **Adoption
is a config file; the INSTRUCTIONS are a separate fact**, and nothing checked the second one.

That matters more than it sounds: `AGENTS.md` is what tells an agent it must CLAIM an item
before editing, and every coordination guarantee in this package rests on that. An agent
without it does not know to claim, and a parallel agent then destroys its work.

The two surfaces behave differently on purpose:

* **CLI** — the operator is right there typing. `adopt` writes it; `init` REPORTS it, because
  writing prose into someone's `AGENTS.md` is not what `init` was asked to do.
* **MCP** — the handshake tells the agent to ASK THE OPERATOR and then call `ddflow_setup`.
  It is a file in their repository, usually with their own prose around the block, and
  rewriting it is not a decision a tool makes on their behalf.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.services.adopt import (
    BEGIN,
    CURRENT,
    MISSING,
    NO_BLOCK,
    STALE,
    project_section,
    rules_status,
)


def _adopted(repo: Path) -> None:
    code, _out, err = run_cli(repo, "adopt", "--agents", "claude")
    assert code == 0, err


def _state(repo: Path, name: str = "AGENTS.md") -> str:
    return next(r.state for r in rules_status(repo) if r.path == name)


# -- detection -----------------------------------------------------------------------------


def test_every_way_the_rules_file_can_go_wrong_is_detected(repo):
    """Four states, and the three that are not `current` each read differently to whoever
    has to fix them."""
    _adopted(repo)
    assert _state(repo) == CURRENT

    agents = repo / "AGENTS.md"
    original = agents.read_text()

    agents.write_text("# proj\n\njust my own notes\n")
    assert _state(repo) == NO_BLOCK, "a stripped block was reported as fine"

    agents.unlink()
    assert _state(repo) == MISSING

    # STALE: the block is there and does not say what this version says.
    agents.write_text(original.replace("Claim before you edit", "Claim before you edit SOMEDAY"))
    assert _state(repo) == STALE, "a drifted block was reported as fine"

    agents.write_text(original)
    assert _state(repo) == CURRENT


def test_the_checker_compares_against_the_SAME_text_adopt_writes(repo):
    """Two generators would be two answers to "what should be in there", and the drift
    between them would be invisible in exactly the file that tells an agent how to behave."""
    _adopted(repo)
    block = (repo / "AGENTS.md").read_text()
    want = project_section().strip()
    assert want in block, "adopt and rules_status disagree about the managed block"
    assert BEGIN in want


def test_a_missing_CLAUDE_md_is_not_a_defect(repo):
    """Creating it in a project that does not use Claude Code would be noise."""
    _adopted(repo)
    (repo / "CLAUDE.md").unlink()
    reported = [r.path for r in rules_status(repo)]
    assert "CLAUDE.md" not in reported, reported
    assert "AGENTS.md" in reported


# -- the surfaces ---------------------------------------------------------------------------


def test_doctor_calls_a_MISSING_rules_file_a_problem_not_a_note(repo):
    """An agent with no project rules does not know it must claim before editing. That is a
    problem, and `doctor` must exit 1 on it — a note would let the operator read past it."""
    from ddflow import api

    _adopted(repo)
    assert api.doctor(repo).exit == 0, "a freshly adopted project is not healthy"

    (repo / "AGENTS.md").unlink()
    out = api.doctor(repo)
    assert out.exit == 1, "a missing rules file was not a problem"
    assert any("AGENTS.md" in p for p in out.data["problems"]), out.data["problems"]
    assert any("ddflow adopt" in p for p in out.data["problems"]), "no remedy was named"


def test_doctor_calls_a_DRIFTED_block_a_note_not_a_problem(repo):
    """The agent has rules; they are not current. Failing the health check on that would
    make `doctor` red after every ddflow upgrade, which trains people to ignore it."""
    from ddflow import api

    _adopted(repo)
    agents = repo / "AGENTS.md"
    agents.write_text(agents.read_text().replace("Claim before you edit", "Claim eventually"))

    out = api.doctor(repo)
    assert out.exit == 0, "a drifted block failed the health check"
    assert any("AGENTS.md" in n for n in out.data["notes"]), out.data["notes"]


def test_the_handshake_tells_the_agent_to_ASK_not_to_fix(repo):
    """MCP is the surface where ddflow does NOT write the operator's repo unasked.

    The whole block is about consent: it names the file, says what is wrong, says why it
    matters, and then says ask first. A tool that silently rewrote `AGENTS.md` on connect
    would be editing a file it was never given permission to touch.
    """
    from ddflow.surfaces.mcp import _instructions

    _adopted(repo)
    clean = _instructions(repo)
    assert "rules file needs the operator" not in clean, "it speaks when nothing is wrong"

    (repo / "AGENTS.md").unlink()
    text = _instructions(repo)
    assert "rules file needs the operator" in text
    assert "Ask the operator before fixing it" in text
    assert "ddflow_setup" in text, "the remedy is not named"
    assert "leaves everything else untouched" in text, "it does not say the write is scoped"


def test_the_footer_reports_it_mid_session_too(repo):
    """The handshake fires once. An agent whose AGENTS.md is deleted while it works — or
    which connected before the deletion — hears about it from the footer."""
    from ddflow.config import Config
    from ddflow.core.model import fold
    from ddflow.infra.log import EventLog
    from ddflow.services import obligations as OB

    _adopted(repo)
    state = fold(EventLog(repo).read_all(), strict=False)
    cfg = Config.load(repo)
    assert OB.footer(state, cfg, repo=repo) == "", "it spoke about a healthy project"

    (repo / "AGENTS.md").unlink()
    text = OB.footer(state, cfg, repo=repo)
    assert "AGENTS.md" in text, text
    assert "ddflow_setup" in text or "ddflow adopt" in text, text


def test_init_REPORTS_a_broken_rules_file_and_does_not_write_it(repo):
    """`init` creates `.ddflow/`. Writing prose into someone's AGENTS.md is `adopt`'s job.

    Two halves, and the first version got the second one wrong:

    * On a FRESH repo it says nothing about AGENTS.md — the project has not been adopted, so
      there is no rules file to be missing, and reporting one would be nagging an operator
      who never asked for it.
    * On a repo that WAS adopted and whose rules file has since gone, it reports — because
      that is a real fault and `init` is a moment someone is looking at the output.
    """
    code, out, err = run_cli(repo, "init")
    assert code == 0, err
    assert not (repo / "AGENTS.md").exists(), "`init` wrote the operator's rules file"
    assert "AGENTS.md" not in out, f"an un-adopted project was nagged: {out}"

    _adopted(repo)
    (repo / "AGENTS.md").unlink()
    code, out, err = run_cli(repo, "init")
    assert code == 0, err
    assert not (repo / "AGENTS.md").exists(), "`init` wrote it after all"
    assert "AGENTS.md does not exist" in out, out
    assert "ddflow adopt" in out, "the remedy is not named"


def test_adopt_repairs_it_and_keeps_the_operators_own_prose(repo):
    """The CLI half: the operator is right there, so it fixes it. And it replaces ONLY the
    block between the markers — the file is theirs."""
    _adopted(repo)
    agents = repo / "AGENTS.md"
    agents.write_text("# my project\n\nMY OWN NOTES, do not eat.\n")
    assert _state(repo) == NO_BLOCK

    _adopted(repo)
    assert _state(repo) == CURRENT
    assert "MY OWN NOTES, do not eat." in agents.read_text(), "adopt ate the operator's prose"


def test_a_broken_rules_check_cannot_break_the_handshake(repo, monkeypatch):
    """B153's lesson: a partial failure in `_instruction_vars` must not take the whole
    handshake down. The check is two `read_text` calls and it is still wrapped."""
    import ddflow.services.adopt as A
    from ddflow.surfaces.mcp import _instructions

    _adopted(repo)

    def boom(*_a, **_k):
        raise OSError("the rules check is broken")

    monkeypatch.setattr(A, "rules_status", boom)
    text = _instructions(repo)
    assert "could not be loaded" not in text, text[:200]
    assert "Work queue" in text or "ddflow_brief" in text, text[:200]


def test_re_adopting_leaves_exactly_ONE_managed_block(repo):
    """Idempotence, asserted by counting.

    `_upsert_block` REPLACES the block when the markers are present. Disabling that branch
    does not lose the operator's prose — it appends a second copy — so the prose test above
    passes while every re-adopt grows the file by another block. Two blocks is worse than a
    stale one: the agent reads both, and nothing says which is current.
    """
    from ddflow.services.adopt import END

    _adopted(repo)
    once = (repo / "AGENTS.md").read_text()
    assert once.count(BEGIN) == 1 and once.count(END) == 1

    for _ in range(3):
        _adopted(repo)
    twice = (repo / "AGENTS.md").read_text()
    assert twice.count(BEGIN) == 1, f"{twice.count(BEGIN)} managed blocks after re-adopting"
    assert twice.count(END) == 1
    assert twice == once, "a no-op re-adopt changed the file"


def test_a_project_that_only_ran_init_is_not_nagged(repo):
    """An unadopted project has no `AGENTS.md`, CORRECTLY.

    `.ddflow/config.toml` cannot make this distinction because `init` writes it too — so the
    first version reported "AGENTS.md does not exist" for every repo that had only been
    initialised, on every tool call. That is noise put in front of an operator who never
    asked for AGENTS.md, and the handshake's adoption offer already covers them.

    The drivers are the marker, because writing them is the first thing `adopt` does.
    """
    from ddflow.services.adopt import has_been_adopted

    run_cli(repo, "init")
    assert not has_been_adopted(repo)
    assert rules_status(repo) == [], "an un-adopted project was told its rules file is missing"

    _adopted(repo)
    assert has_been_adopted(repo)
    assert [r.state for r in rules_status(repo)] == [CURRENT, CURRENT]


# -- the NATIVE surfaces, which for some agents outrank AGENTS.md ---------------------------


def _adopted_for(repo: Path, agents: str) -> None:
    code, _out, err = run_cli(repo, "adopt", "--agents", agents)
    assert code == 0, err


def test_cursors_own_rule_file_is_checked_because_it_is_what_binds(repo):
    """Cursor does not follow `AGENTS.md` in any meaningful sense.

    Its precedence is Team Rules > Project Rules > User Rules > `.cursorrules` >
    `AGENTS.md`, so `.cursor/rules/ddflow.mdc` is what actually binds. `adopt` has always
    written it — and `rules_status` checked only AGENTS.md and CLAUDE.md, so a project
    adopted for Cursor with a deleted or drifted `.mdc` had an agent that does not follow
    the rules while every check reported the project as fine. The one file whose whole
    purpose was to bind was the one nobody verified.
    """
    from ddflow.services.adopt import NATIVE_RULES, adopted_agents

    _adopted_for(repo, "cursor")
    assert adopted_agents(repo) == ["cursor"]
    mdc = repo / NATIVE_RULES["cursor"].path
    assert mdc.is_file(), "adopt did not write the native rule"
    assert _state(repo, NATIVE_RULES["cursor"].path) == CURRENT

    original = mdc.read_text()
    mdc.unlink()
    assert _state(repo, ".cursor/rules/ddflow.mdc") == MISSING

    mdc.write_text(original.replace("Claim before you edit", "Claim eventually"))
    assert _state(repo, ".cursor/rules/ddflow.mdc") == STALE


def test_a_rule_that_does_not_BIND_is_as_serious_as_a_missing_one(repo):
    """`alwaysApply: false` is the mechanism switched off, not a milder drift.

    The file is there, its text may be perfect, and Cursor may never load it — which for
    claim-before-you-edit is the same as not having it. Reporting that as a note would put
    it below the threshold an operator reads, so it fails the health check like MISSING.
    Drifted TEXT still gets read, and stays a note.
    """
    from ddflow import api
    from ddflow.services.adopt import NOT_BINDING

    _adopted_for(repo, "cursor")
    mdc = repo / ".cursor" / "rules" / "ddflow.mdc"

    mdc.write_text(mdc.read_text().replace("alwaysApply: true", "alwaysApply: false"))
    assert _state(repo, ".cursor/rules/ddflow.mdc") == NOT_BINDING
    out = api.doctor(repo)
    assert out.exit == 1, "a rule that does not bind passed the health check"
    assert any("does not bind" in p for p in out.data["problems"]), out.data["problems"]

    # Text drift alone: still read, still a note, health check green.
    mdc.write_text(
        mdc.read_text()
        .replace("alwaysApply: false", "alwaysApply: true")
        .replace("Claim before you edit", "Claim eventually")
    )
    assert _state(repo, ".cursor/rules/ddflow.mdc") == STALE
    assert api.doctor(repo).exit == 0


def test_the_native_rule_is_not_expected_for_agents_that_were_not_adopted(repo):
    """`.cursor/rules/ddflow.mdc` in a Claude-only project would be noise, and demanding it
    would make `doctor` permanently red for everyone who does not use Cursor."""
    _adopted_for(repo, "claude")
    paths = [r.path for r in rules_status(repo)]
    assert not any("cursor" in p for p in paths), paths
    assert "AGENTS.md" in paths


def test_the_native_rule_and_AGENTS_md_carry_the_SAME_text(repo):
    """One source, so the two copies cannot say different things. The only difference is the
    frontmatter, which is what makes the Cursor rule bind."""
    from ddflow.services.adopt import END, native_rule_text

    _adopted_for(repo, "cursor")
    body = project_section().replace(BEGIN, "").replace(END, "").strip()
    native = native_rule_text()
    assert body in native, "the native rule is not the same block"
    assert native.startswith("---\n"), "frontmatter must be first or the rule does not bind"
    assert "alwaysApply: true" in native
    assert (repo / ".cursor" / "rules" / "ddflow.mdc").read_text() == native


def test_adopt_repairs_the_native_rule_too(repo):
    """The CLI half, for the surface that actually binds."""
    _adopted_for(repo, "cursor")
    mdc = repo / ".cursor" / "rules" / "ddflow.mdc"
    mdc.write_text("---\nalwaysApply: false\n---\n\nnonsense\n")
    assert _state(repo, ".cursor/rules/ddflow.mdc") != CURRENT

    _adopted_for(repo, "cursor")
    assert _state(repo, ".cursor/rules/ddflow.mdc") == CURRENT


# -- D-compat: an argument a release accepted keeps working (Bf84a50bce3) ---------------------


def _mcp(repo: Path, name: str, args: dict) -> dict:
    from ddflow.surfaces.mcp import Server

    return Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
    )["result"]


def _tools_list(repo: Path) -> dict:
    from ddflow.surfaces.mcp import Server

    reply = Server(repo).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    return {t["name"]: t for t in reply["result"]["tools"]}


def test_arguments_0_1_15_accepted_are_still_accepted_as_deprecated_no_ops(repo: Path) -> None:
    """`ddflow_rule_list` took `json` and `limit`, `ddflow_rule_remove` took `reason`; 0.1.18
    rejected them as unknown arguments. D-compat keeps an old caller working: accepted, hidden
    from tools/list, ignored, and said once to be deprecated."""
    run_cli(repo, "init")
    assert run_cli(repo, "rule", "add", "--id", "r-one", "--title", "One", "--content", "x")[0] == 0

    plain = _mcp(repo, "ddflow_rule_list", {})
    old = _mcp(repo, "ddflow_rule_list", {"json": True, "limit": 5})

    assert not old["isError"], old
    assert old["content"][0] == plain["content"][0], "the answer is the same without them"
    note = [c["text"] for c in old["content"][1:] if "deprecated" in c["text"]]
    assert len(note) == 1 and "json" in note[0] and "limit" in note[0]
    assert not any("deprecated" in c["text"] for c in plain["content"])

    gone = _mcp(repo, "ddflow_rule_remove", {"id": "r-one", "reason": "obsolete"})
    assert not gone["isError"], gone
    assert any("reason" in c["text"] and "deprecated" in c["text"] for c in gone["content"][1:])


def test_a_deprecated_argument_is_not_advertised_and_an_unknown_one_is_still_refused(
    repo: Path,
) -> None:
    run_cli(repo, "init")
    tools = _tools_list(repo)

    assert set(tools["ddflow_rule_list"]["inputSchema"]["properties"]) == {
        "tag",
        "scope",
        "as_agent",
    }
    assert "reason" not in tools["ddflow_rule_remove"]["inputSchema"]["properties"]
    refused = _mcp(repo, "ddflow_rule_list", {"nonsense": 1})
    assert refused["isError"] and "unknown argument" in refused["content"][0]["text"]
