"""The five behavioural defects roborev's duplication analysis found, 2026-09-24.

Every one is the same shape — **two implementations of one rule, drifted** — which is
the third time that class has produced a real bug in this package, and the reason the
house rule is *eliminate* a duplicate rather than guard it twice. Worth naming what
made these invisible: in each case both copies were individually correct-looking, and
the defect only exists in the difference between them, so reading either one alone
finds nothing.

- D1  three TOML overlay loaders, one of which silently drops unknown keys
- D2  four HTTP call sites, one of which forgets the container rewrite
- D3  two "is this executable on PATH" checks, the weaker one facing a reviewer
- D4  two `family_of` wrappers, one of which ignores the project's own config
- D7  a config reader and a config writer that disagree about where the field is
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


# -- D1: a typo in companions.toml must be an error, like everywhere else -------------


def test_an_unknown_companion_field_is_refused_not_dropped(repo):
    """`gates.load_gates` and `load_reviewers` raise on a typo; companions dropped it.

    A misspelt `commmand` produced a companion with an EMPTY command, which
    `companions add` would then write into an agent's MCP config as a launch line that
    fails mid-task — at the moment a gate told the agent to reach for the tool. This is
    the silent-knob-drop class in a package whose config loader raises on a typo'd
    *section* specifically to prevent it.
    """
    run_cli(repo, "init")
    (repo / ".orchard" / "companions.toml").write_text(
        '[[companion]]\nid = "mine"\ntitle = "x"\ncommmand = "typo"\n'
    )
    code, out, err = run_cli(repo, "companions", "list", "--no-probe")
    assert code == FAIL, f"a typo must be reported, not absorbed:\n{out}"
    assert "commmand" in (out + err), out + err


def test_companions_may_be_configured_in_the_main_config_file(repo):
    """One place to configure, like gates and reviewers.

    `load_gates`' own docstring records this exact bug being fixed for gates — "a
    config write that silently does nothing is worse than one that errors" — and
    `companions.load` was written afterwards with the old shape, reading only its own
    dedicated file.
    """
    run_cli(repo, "init")
    cfg = repo / ".orchard" / "config.toml"
    cfg.write_text(
        cfg.read_text() + '\n[[companion]]\nid = "mine"\ntitle = "in the main file"\n'
        'detect = ["definitely-not-a-real-binary-xyzzy"]\n'
    )
    # `--no-probe`: this is about which FILES are read, and probing the four shipped
    # companions spends tens of seconds on npx cold-fetches that prove nothing here.
    _code, out, _ = run_cli(repo, "companions", "list", "--no-probe")
    assert "in the main file" in out, f"the main config file must be read too:\n{out}"


# -- D2: the container rewrite must reach every backend -------------------------------


def test_the_container_host_rewrite_applies_to_every_reviewer_backend(monkeypatch):
    """Three of four HTTP call sites rewrote `127.0.0.1`; `_post_json` did not.

    `_post_json` is the ONLY path used by `kind="anthropic"` and `kind="gemini"`. So
    inside a container an openai-kind reviewer on localhost worked and an anthropic- or
    gemini-kind reviewer pointed at a local gateway (litellm, LM Studio, an in-house
    proxy — all normal) reported UNAVAILABLE. Container support silently covered two
    thirds of the backends.

    Asserted on the URL actually requested, not on the source text: the first version
    of this test grepped for `rewrite_localhost` in the function body and **survived
    the mutation**, because removing the call left the import and the comment behind.
    A test that reads the code rather than running it proves the code says something,
    which is not what was in doubt.
    """
    from orchard.infra import container
    from orchard.services import review as R

    monkeypatch.setattr(container, "in_container", lambda: True)
    seen: list[str] = []

    class _Boom(OSError):
        pass

    def _fake_urlopen(req, timeout=0):
        seen.append(req.full_url)
        raise _Boom("not really opening a socket")

    monkeypatch.setattr(R.urllib.request, "urlopen", _fake_urlopen)
    R._post_json("http://127.0.0.1:11434/v1/messages", {}, {}, 5)
    assert seen and "127.0.0.1" not in seen[0], (
        f"inside a container the reviewer's loopback endpoint is the CONTAINER's "
        f"loopback, so this request goes nowhere: {seen}"
    )
    assert container.HOST_ALIAS in seen[0], seen


# -- D3: one PATH check, and it must not raise ----------------------------------------


def test_a_command_reviewer_with_an_env_prefix_is_not_reported_missing():
    """`FOO=bar claude -p` is a normal shell command, and it was called UNAVAILABLE.

    The reviewer copy of the PATH check split on the whole string and took token 0, so
    an environment prefix became the "executable". `gates._missing_executable` already
    handled this — and builtins, and shell metacharacters — which is exactly why there
    should not have been a second copy.
    """
    from orchard.services.review import Reviewer, _chat_command

    _out, err = _chat_command(
        Reviewer(name="x", kind="command", command="ORCHARD_TEST=1 true", model="m"),
        "sys",
        "user",
        30,
    )
    assert "not on PATH" not in err, f"an env prefix is not a missing binary: {err!r}"


def test_a_command_reviewer_with_an_unbalanced_quote_does_not_raise():
    """`_chat_command`'s contract is "never raises"; `shlex.split` raises on bad quoting.

    A reviewer is invoked inside the gate runner, so an exception here does not
    degrade to UNAVAILABLE — it propagates out of a function whose whole job is to
    convert every way of not-reviewing into a reported one.
    """
    from orchard.services.review import Reviewer, _chat_command

    out, err = _chat_command(
        Reviewer(name="x", kind="command", command='claude -p "unbalanced', model="m"),
        "sys",
        "user",
        30,
    )
    assert err, "it must report a reason rather than raising"
    assert not out


# -- D4: one family map, consulted by both surfaces -----------------------------------


def test_a_project_taught_a_model_name_has_it_honoured_by_BOTH_surfaces(repo):
    """`resolved_family` used the shipped map; `reviewer_independence` used the config.

    So a project that adds its in-house model to `[agent].families` got it honoured by
    the check that decides whether a review counted, and ignored by
    `orchard reviewers list` and by the family recorded on the result. The two surfaces
    answering the independence question disagreed — which is worse than either answer,
    because the operator sees one and the gate applies the other.

    Half of this was fixed by unifying `family_of`; the wrapper pair re-opened the seam
    one level up, which is what makes it worth its own test.
    """
    run_cli(repo, "init")
    cfg = repo / ".orchard" / "config.toml"
    cfg.write_text(
        cfg.read_text() + '\n[agent.families]\nacme = "acme-labs"\n'
        '\n[[reviewer]]\nname = "house"\nkind = "openai"\n'
        'base_url = "http://127.0.0.1:9/v1"\nmodel = "acme-7b"\ngates = ["critic"]\n'
    )
    code, out, err = run_cli(repo, "reviewers", "list")
    assert code == OK, err
    assert "acme-labs" in out, (
        f"the project taught [agent].families this name; `reviewers list` must use it "
        f"rather than the shipped map:\n{out}"
    )
    assert "no known family" not in out, out


# -- D7: the reader and the writer must agree where the field is ----------------------


def test_the_companion_reader_and_writer_agree_on_the_config_field(repo):
    """`registered_in` checked both `mcpServers` and `servers`; `register` wrote
    `servers` only for copilot, keyed on a hardcoded agent name.

    Add a seventh agent that uses `servers` and the reader finds its entry while the
    writer creates a second, dead `mcpServers` block beside it — and the symptom is a
    companion that reports as registered and never launches. Rather than a test per
    agent, this asserts the property: whatever `register` writes, `registered_in`
    finds, for every agent the package knows.
    """
    from orchard.services import companions as CO
    from orchard.services.adopt import AGENT_TARGETS

    run_cli(repo, "init")
    comp = next(c for c in CO.load(repo) if c.id == "context7")
    for agent in AGENT_TARGETS:
        CO.register(repo, comp, agent)
        assert agent in CO.registered_in(repo, comp.id), (
            f"register() wrote {comp.id} for {agent} somewhere registered_in() does not look"
        )


def test_copilot_gets_the_field_name_vs_code_actually_reads(repo):
    """Agreement between our reader and our writer is necessary, not sufficient.

    The property test above passes if BOTH sides are wrong in the same way, which is
    exactly what the obvious mutation does. This pins the external contract: VS Code /
    Copilot keys MCP servers under `servers`, everyone else under `mcpServers`, and
    that is a fact about their config format rather than a choice of ours.
    """
    import json as _json

    from orchard.services import companions as CO

    run_cli(repo, "init")
    comp = next(c for c in CO.load(repo) if c.id == "context7")
    CO.register(repo, comp, "copilot")
    CO.register(repo, comp, "claude")

    copilot = _json.loads((repo / ".vscode" / "mcp.json").read_text())
    assert comp.id in copilot.get("servers", {}), copilot
    assert "mcpServers" not in copilot, f"a second, dead block: {copilot}"

    claude = _json.loads((repo / ".mcp.json").read_text())
    assert comp.id in claude.get("mcpServers", {}), claude
    assert "servers" not in claude, f"a second, dead block: {claude}"


# -- the architecture pass: two concrete defects --------------------------------------


def test_a_read_only_command_does_not_create_orchard_in_an_unadopted_repo(repo):
    """`Ctx` builds a Store for EVERY command, and `Store.__init__` ran `mkdir`.

    So `orchard status` in a repository that had never run `orchard init` created
    `.orchard/` and exited 0, as though the project had adopted the tool. A read-only
    question must not leave a mark — and the rule already existed: the MCP server's
    handshake was fixed for exactly this, which makes the Store the second door onto
    the same mistake.
    """
    code, out, err = run_cli(repo, "status")
    assert not (repo / ".orchard").exists(), (
        f"asking a question adopted the tool: {sorted(p.name for p in repo.iterdir())}"
    )
    assert code in (OK, NOTHING, FAIL), f"{code}: {out}{err}"


def test_every_mcp_resource_is_served_through_the_cli(repo):
    """`resources/read` had a second data path, and a dead entry hiding beside it.

    Two of the four URIs folded the log directly, re-wiring `EventLog` and `fold`
    without `Ctx`'s config and agent resolution, in a module whose stated premise is
    "one implementation, two doors". The lookup table also carried an entry for
    `orchard://lessons` that the branch above it shadowed — which is how a second path
    stays hidden: nothing reads the line, so nothing contradicts it.
    """
    import inspect

    from orchard.surfaces import mcp as M

    src = inspect.getsource(M.Server.handle)
    read = src[src.index('if method == "resources/read"') :]
    read = read[: read.index('if method == "prompts/list"')]
    assert "fold(" not in read, f"resources/read still folds the log itself:\n{read}"
    assert read.count("_run_cli") == 1, f"one path, one call:\n{read}"


def test_the_render_views_are_reachable_from_the_command_line(repo):
    """`render --show` is what makes the single path possible, so it is pinned here."""
    run_cli(repo, "init")
    run_cli(repo, "lesson", "add", "--id", "L1", "--title", "empty collections pass checks")
    code, out, err = run_cli(repo, "render", "--show", "lessons")
    assert code == OK, err
    assert "empty collections pass checks" in out, out

    code, _, err = run_cli(repo, "render", "--show", "nonsense")
    assert code == FAIL and "known:" in err.lower(), err


# -- the cross-family critic's findings, 2026-09-24 -----------------------------------


def test_a_companion_with_env_writes_TOML_a_parser_accepts(repo):
    """`json.dumps` for a TOML value is *nearly* right, which is worse than wrong.

    JSON and TOML agree on strings, numbers and arrays and disagree on exactly one
    thing: an object. `{"API_KEY": "x"}` is valid JSON and invalid TOML, which needs
    `{API_KEY = "x"}`. So a companion carrying `env` — the normal shape for anything
    that needs an API key — wrote a config the agent's parser rejects outright, taking
    down EVERY MCP server in that file rather than just this one. And `registered_in`
    still reported it as registered, because it looks for the section header with a
    substring test: the tool told the operator the gate was served while the agent
    could not read the file at all.
    """
    import tomllib

    from orchard.services import companions as CO

    run_cli(repo, "init")
    (repo / ".orchard" / "companions.toml").write_text(
        '[[companion]]\nid = "keyed"\ntitle = "needs a key"\n'
        'command = "npx"\nargs = ["-y", "pkg"]\nenv = {API_KEY = "s3cret"}\n'
    )
    comp = next(c for c in CO.load(repo) if c.id == "keyed")
    CO.register(repo, comp, "codex")  # the .toml target

    written = (repo / ".codex" / "config.toml").read_text()
    parsed = tomllib.loads(written)  # must not raise
    assert parsed["mcp_servers"]["keyed"]["env"] == {"API_KEY": "s3cret"}, written


def test_a_quote_in_a_command_does_not_corrupt_the_toml(repo):
    """Same class, different value: unescaped interpolation into a TOML string."""
    import tomllib

    from orchard.services import companions as CO

    run_cli(repo, "init")
    (repo / ".orchard" / "companions.toml").write_text(
        '[[companion]]\nid = "quoted"\ntitle = "t"\ncommand = \'say "hi"\'\nargs = [\'a"b\']\n'
    )
    comp = next(c for c in CO.load(repo) if c.id == "quoted")
    CO.register(repo, comp, "codex")
    parsed = tomllib.loads((repo / ".codex" / "config.toml").read_text())
    assert parsed["mcp_servers"]["quoted"]["command"] == 'say "hi"'
    assert parsed["mcp_servers"]["quoted"]["args"] == ['a"b']


def test_proc_run_guards_an_explicit_input_of_None():
    """`proc.run(cmd, input=None)` bypassed the guard entirely.

    `"input" not in kwargs` was False, so the shim declined to set `stdin`; then
    `subprocess.run`'s own `if input is not None` was also False, so IT declined too,
    and the child inherited fd 0 — the JSON-RPC stream — through the very shim written
    to make that impossible. `input=None` is the natural shape of "no diff to send",
    so the hole sat exactly where a caller falls into it.
    """
    import subprocess as sp
    import sys as _sys

    parent = (
        f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r});"
        "from orchard.infra import proc as P;"
        "r = P.run([sys.executable, '-c', 'import sys; sys.stdout.write(sys.stdin.read())'],"
        "          input=None, capture_output=True, text=True, timeout=30);"
        "print('CHILD_SAW=' + repr(r.stdout))"
    )
    p = sp.run(
        [_sys.executable, "-c", parent],
        input="PROTOCOL-BYTES\n",
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert p.returncode == 0, p.stderr[-600:]
    assert "CHILD_SAW=''" in p.stdout, (
        f"input=None let the child read the parent's stdin: {p.stdout!r}"
    )


# -- companions: unknown is not the same as absent -------------------------------------


def test_not_having_looked_is_reported_as_unknown_not_as_missing(repo):
    """`scan(probe=False)` must not assert a companion is absent.

    The MCP handshake skips detection deliberately — making an agent wait on `npx`
    before it can do anything is the wrong trade — and the state then collapsed to
    "missing", so the instruction block told the agent three tools were absent on the
    strength of never having checked. That is the same mistake as recording an
    unavailable reviewer as a pass, one layer out: a value nobody measured, rendered
    as a measurement.
    """
    from orchard.services import companions as CO

    run_cli(repo, "init")
    unprobed = {st.companion.id: st for st in CO.scan(repo, probe=False)}
    assert unprobed, "the shipped registry must load"
    for st in unprobed.values():
        assert st.installed is None, f"{st.companion.id}: nobody probed, so nobody knows"
        assert st.state == "unknown", st.state

    # And a registered one is still known to be registered — that reads config files,
    # which is free, and is the half the handshake CAN answer.
    comp = next(c for c in CO.load(repo) if c.id == "context7")
    CO.register(repo, comp, "claude")
    again = {st.companion.id: st for st in CO.scan(repo, probe=False)}
    assert again["context7"].state == "registered"


def test_the_cli_shows_the_unknown_state_distinctly(repo):
    run_cli(repo, "init")
    _code, out, _ = run_cli(repo, "companions", "list", "--no-probe")
    assert "[?]" in out, f"an unchecked companion needs its own mark:\n{out}"
    assert "not checked" in out, out


def test_merge_refuses_an_item_that_was_removed_from_the_queue(repo):
    """`orchard merge` was the one mutating command not routed through `_require_item`.

    Removal is a FLAG on an item that still folds, so `st.items.get()` finds it and
    only the flag says it is gone. Every other mutating command — `split`, `update`,
    `complete`, `abandon`, `remove`, `block`, `show` — goes through the one place that
    checks the flag; `merge` did not, so the most consequential action in the package
    was taken on the item least likely to be wanted, and it printed "merged".

    Found by `roborev analyze duplication` as a consequence of the D-class duplication
    it was actually looking for, and probed before it was fixed.
    """
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    assert run_cli(repo, "claim", "T1")[0] == OK
    assert run_cli(repo, "remove", "T1", "--reason", "dropped")[0] == OK

    code, out, err = run_cli(repo, "merge", "T1")
    assert code == FAIL, f"it merged work the operator dropped:\n{out}"
    assert "removed from the queue" in err, err


# -- roborev 772, on the import commit -------------------------------------------------


def test_re_importing_after_a_new_journal_entry_keeps_the_OLD_ones_recallable(repo):
    """The advertised incremental path, losing data in the index.

    Journal and memory entries land as notes in two fixed sessions, numbered by a fresh
    `enumerate` on every run. The session handler MERGES, so a second import appends
    notes numbered 0,1,2… beside the first run's 0,1,2… — and the index primary key is
    `(session, 10_000 + seq)`, so each new note silently overwrites an earlier one.
    Worse for search: `prompts_fts` then holds two rows under one doc id, so a query
    matching the OLD text resolves to the surviving row and returns text that does not
    contain the query terms.
    """
    (repo / "docs" / "log").mkdir(parents=True)
    log_md = repo / "docs" / "log" / "2026-04.md"
    log_md.write_text("# 2026-04\n\n## Zebra alpha, the first entry (2026-04-01)\n\nfirst\n")
    run_cli(repo, "init")
    assert run_cli(repo, "import", "--apply")[0] == OK

    log_md.write_text(log_md.read_text() + "\n## Quagga beta, added later (2026-04-09)\n\nsecond\n")
    assert run_cli(repo, "import", "--apply")[0] == OK

    _code, out, _ = run_cli(repo, "recall", "zebra alpha first entry", "--max-chars", "20000")
    assert "Zebra alpha" in out, f"the first entry was overwritten by the second:\n{out}"
    _code, out2, _ = run_cli(repo, "recall", "quagga beta added later", "--max-chars", "20000")
    assert "Quagga beta" in out2, f"the new entry never arrived:\n{out2}"


def test_a_declared_id_that_was_already_taken_does_not_vote_as_if_it_had_been_used(repo):
    """`id_from_source` recorded "this id came from the source" even when `_unique`
    REJECTED it and handed back a derived slug.

    The phase branch guards exactly this (`declared == phase_ident`); the task branch
    did not. The result is the circular vote `_adopt_child_prefix` exists to avoid: a
    derived id disagrees in its first component, the common prefix comes out empty, and
    the phase silently keeps its prose slug.
    """
    (repo / "docs" / "todo" / "open").mkdir(parents=True)
    (repo / "docs" / "todo" / "open" / "a.md").write_text(
        "## Session ALPHA\n\n- [ ] **142.1** — original\n- [ ] **142.2** — original two\n"
    )
    (repo / "docs" / "todo" / "open" / "b.md").write_text(
        "## Session BETA\n\n- [ ] **142.1** — a duplicate id in another file\n"
    )
    from orchard.services import importer as IM

    found, _empty = IM.scan_todos(repo)
    dup = next(f for f in found if f.kind == "task" and f.source.startswith("docs/todo/open/b.md"))
    assert dup.ident != "142.1", "the id was taken; this one must have been renamed"
    assert dup.extra["id_from_source"] is False, (
        f"{dup.ident!r} was DERIVED, but it is recorded as having come from the source, "
        f"so it votes in the phase-prefix inference it must not influence"
    )


def test_the_critical_path_cycle_guard_covers_the_graph_it_actually_walks(repo):
    """The memo in `longest()` is unsound on a cyclic graph, and the guard knows it.

    But the guard built its graph from direct `needs` while `longest()` now walks
    INHERITED ones — so a cycle that exists only in the inherited graph slips past, the
    node-keyed memo caches a truncated path, and the function returns a confidently
    wrong number instead of the refusal its docstring promises.
    """
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--globs", "p1/**", "--needs", "P2.T1")
    run_cli(repo, "phase", "add", "P2", "--globs", "p2/**", "--needs", "P1.T1")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "p1/a.py")
    run_cli(repo, "task", "add", "P2.T1", "--phase", "P2", "--globs", "p2/a.py")

    from orchard.core.model import fold
    from orchard.core.schedule import critical_path
    from orchard.infra.log import EventLog

    st = fold(EventLog(repo, "agent-test").read_all(), strict=False)
    assert critical_path(st) == [], (
        "P1.T1 inherits a dependency on P2.T1 and P2.T1 inherits one on P1.T1 — that "
        "is a cycle in the graph this walks, and a memoised longest-path over it is "
        "wrong rather than merely late"
    )
