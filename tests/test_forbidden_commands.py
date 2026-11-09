"""The PreToolUse guard: commands an agent is told never to run are refused before they run
(DX-forbidden-commands-hook). Three subagents ran `pkill` against a brief that said never."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.config import Config
from ddflow.services import forbidden as FB


def _check(cmd: str, env: dict | None = None, **kw):
    return FB.check(cmd, Config(), env=env or {}, **kw)


@pytest.mark.parametrize(
    "cmd",
    [
        "pkill -f pytest",
        "sudo pkill python",
        "cd x && killall node",
        "git stash",
        "git -C /tmp/x stash push -u",
        "git push origin main",
        "git push --force-with-lease",
        "rm -fr .ddflow",
        "rm -rf ./.ddflow/events",
        "ddflow merge X --force",
        "FOO=1 bash -c 'pkill foo'",
        "true; git stash pop",
        "echo hi | xargs pkill",
        "sudo -u root pkill foo",
        "sudo -n pkill -f worker",
        "sudo -n bash -c 'pkill -f worker'",
        "time -p pkill worker",
        "xargs -t pkill",
        "bash -c \"bash -c 'pkill -f worker'\"",
        "cat <<EOF-2 >/dev/null\nx\nEOF\npkill foo",
        "pkill -f worker # ddflow review --force --reason",
        "nice -n 5 pkill foo",
        "env -u HOME pkill foo",
        "xargs -n 1 pkill",
        'python -c "print(1<<3)"\npkill -f myserver',
        'git commit -m "a<<b"\npkill foo',
        'grep x <<< "$y"\npkill foo',
        "ddflow review X --force",
        # a `\r` is not a shell blank: `push\r#` is one word, so the `#` is not a comment
        # and the `;` still separates a forbidden `git push` (B-...-critic #1, `\r` boundary)
        "git push\r#; git push origin main",
        # a `case` inside a `$((` arithmetic body is an arithmetic token, not a shell case, so
        # a later `esac` must not swallow the real `git push` after the arithmetic (critic)
        "echo $((case 0)); git push origin main; echo esac",
    ],
)
def test_refused(cmd):
    r = _check(cmd, cwd="/nonexistent")
    assert r is not None, cmd
    assert "Instead:" in r.message()


@pytest.mark.parametrize(
    "cmd",
    [
        "git log --grep pkill",
        'git commit -m "git stash is banned"',
        "git commit -m push",
        "git stash-ish",
        "git stash list",
        "git stash show",
        "git stash show stash@{0}",
        "grep -r pkill docs",
        "echo pkill",
        # a `#` right after an opening backtick begins a comment (the substitution body runs
        # nothing), so `git push` inside it is not a command (critic #1, backtick comment)
        "echo `# comment; git push origin main`",
        # `<(` / `>(` are NOT process substitution inside double quotes: bash prints the literal
        # and runs no command, so the body is not a command (critic #5, dquote process sub)
        'echo "<(git push origin main)"',
        'echo ">(git push origin main)"',
        # a `-c` AFTER a script operand is an argument to the script, not the shell's own -c
        # (`bash ./tool.sh -cache 'x'` runs ./tool.sh), and `-o`/`-O` swallow their optname, so
        # a `c` inside `-ocpipefail` is not a -c flag that runs the quoted word (critic #5)
        "bash ./tool.sh -cache 'git push origin main'",
        "bash -ocpipefail 'git push origin main'",
        # a `case` word whose `esac` is only literal text is not a case statement: bash closes
        # the $() at its `)` and merely prints `git push` inside the quote, never runs it
        # (critic #2 / #4: an `esac` a quote swallows must not extend the $() body to it)
        'echo "$(echo case x); git push origin main; esac"',
        'ddflow review X --force --reason "an extra round"',
        'ddflow review X --reason "an extra round" --force',
        "rm -rf build",
        "kill 1234",
        "cat <<EOF-2\npkill -f worker\nEOF-2",
        "cat <<'END.JSON'\npkill x\nEND.JSON",
        "cat <<'EOF'\npkill x\nEOF",
        "ls",
    ],
)
def test_allowed(cmd):
    assert _check(cmd) is None, cmd


@pytest.mark.parametrize(
    "cmd",
    [
        'echo a; git push origin main; python3 -c "\nprint(1)"',
        'python3 -c "\nprint(1)"; git stash -q',
        'python3 -c "\nprint(1)"; pkill x',
        'echo a; python3 -c "\nprint(1)"; git stash -q; pkill x',
    ],
)
def test_refused_multiline_quote_after_semicolon(cmd):
    # a multi-line quoted string must not stop `_split` from seeing the `;` that follows
    r = _check(cmd, cwd="/nonexistent")
    assert r is not None, cmd
    assert "Instead:" in r.message()


@pytest.mark.parametrize(
    "cmd",
    [
        'python3 -c "print(1)\npkill x"',
        'python3 -c "print(1)\ngit stash -q"',
    ],
)
def test_allowed_multiline_quote_data(cmd):
    # a forbidden word inside the quoted data of `python -c` is data, not a command
    assert _check(cmd) is None, cmd


@pytest.mark.parametrize(
    "cmd",
    [
        'echo "$(\ngit push origin main\n)"',
        'echo "`\ngit push origin main\n`"',
        'echo "$(git push origin main)"',
        'echo "$\\\n(git push origin main)"',
        'echo "`git push origin main`"',
        'echo "it\'s $\\\n(git push origin main)"',
        'echo "$(echo x\n)"; git push origin main',
        "sh -c 'echo \"$(git push origin main)\"'",
        'echo "$(echo \\); git push origin main)"',
    ],
)
def test_refused_command_substitution_multiline(cmd):
    # a $() or backtick body a double quote holds still runs as a command, so its newlines
    # stay separators and the command after them is still refused
    r = _check(cmd, cwd="/nonexistent")
    assert r is not None, cmd
    assert r.entry == "git push", cmd
    assert "Instead:" in r.message()


@pytest.mark.parametrize(
    "cmd",
    [
        "echo hi # don't\ngit push 'origin main'",
        "echo hi # comment\npkill -f myserver",
        # a `#` after a closing `)` starts a comment (its next `'` is data), so the real git push
        # on the next line is the command that runs and must still be seen
        "(true)#'\ngit push origin main\n'",
    ],
)
def test_refused_comment_quote_spans_lines(cmd):
    # a comment runs to the end of its line, so a quote inside it is not a quote and the next
    # line's command is still seen
    r = _check(cmd, cwd="/nonexistent")
    assert r is not None, cmd
    assert "Instead:" in r.message()


@pytest.mark.parametrize(
    "cmd",
    [
        # a `#` after a `$(`/`<(`/`>(` close is literal, not a comment start (bash glues it
        # to the substitution's value), so the `;` still separates the forbidden command
        # (residual finding: _COMMENT_BOUNDARY must make that `)` an exception)
        "echo $(echo hi)#; git push origin main",
        "echo $(echo hi)#; pkill -f myserver",
        "cat <(echo hi)#; git stash -q",
        # a `$((...))` arithmetic expansion's outer `)` keeps the `#` literal too
        "echo $((1+2))#; git push origin main",
        # a nested `( ... )` subshell (or nested parens) inside the substitution must not make
        # `_find_body` take the first `)` for the close, so `git push` after `)#;` is still seen
        "echo $( (echo hi) )#; git push origin main",
        "echo $(((1+1)))#; pkill -f myserver",
        # nested: the `#` after the inner <(...) is literal inside the outer $() body, so
        # `git push` runs there and must still be refused
        'echo "$(cat <(echo hi)#; git push origin main)"',
    ],
)
def test_refused_hash_literal_after_substitution_close(cmd):
    r = _check(cmd, cwd="/nonexistent")
    assert r is not None, cmd
    assert "Instead:" in r.message()


@pytest.mark.parametrize(
    "cmd",
    [
        # a $'...' ANSI-C quote escapes its quote: the `)` after it still lives inside the $()
        # body, so a forbidden command after the `;` must still be seen
        "echo $(echo $'a\\')' ; git push origin main ; echo done)",
        "echo $(echo $'a\\')' ; git stash -q ; echo done)",
        # a comment inside a $() body is not a command, and its `)` is not the close
        'echo "$(echo # )\ngit push origin main)"',
        # a case pattern's `)` is a terminator, not the close of the $() body
        "echo $(case x in x) git push origin main;; esac); echo done",
        # an esac inside a # comment in a case body is text, not the case's end
        'echo "$(case x in\nx) echo start # esac )\ngit push origin main;;\nesac)"',
        # an esac inside a nested $() (or a double-quoted word with a blank before it) in a
        # case body is not the case's end, so `git push` after the `;;` is still seen
        'echo "$(case x in x) $(echo esac); git push origin main;; esac)"',
        # a <(...) process substitution's `)` is not the outer $()'s close, so `git push` is seen
        'echo "$(cat <(echo ); git push origin main)"',
        # a bare `case` argument is a word, not a case statement: the `)` after it closes the
        # $(), so `git push` after the real newline is still seen (bare-case bypass)
        "echo $(echo case)\ngit push origin main",
        # a top-level <(...) process substitution body runs as a command too (not only nested)
        "cat <(git push origin main)",
    ],
)
def test_refused_substitution_body_edge_cases(cmd):
    r = _check(cmd, cwd="/nonexistent")
    assert r is not None, cmd
    assert "Instead:" in r.message()


@pytest.mark.parametrize(
    "cmd",
    [
        # a `)` inside a ${...} parameter expansion is part of the expansion, not the close of
        # the enclosing $() body, so the `git push` after the `;` inside the substitution still
        # runs and must be seen (critic #2, ${...} treated as the $() close)
        'echo "$(echo ${x:-)}; git push origin main)"',
        'echo "$(echo ${x:-foo)}; git push origin main)"',
        'echo "$(echo ${x:-$(echo a)}; git push origin main)"',
        # a quoted `}` inside a ${...} is part of the default value, not the brace's close, so
        # the `git push` after the `;` inside the substitution still runs (critic #2 residual)
        'echo "$(echo ${x:-"}")}; git push origin main)"',
    ],
)
def test_refused_param_expansion_body(cmd):
    r = _check(cmd, cwd="/nonexistent")
    assert r is not None, cmd
    assert "Instead:" in r.message()


@pytest.mark.parametrize(
    "cmd",
    [
        # `sh`/`bash` cluster their single-letter flags, so `-c` bundled as -xc/-exc still takes
        # the next word as the command string (critic: a clustered -c was not descended)
        "bash -xc 'git push origin main'",
        "sh -exc 'pkill -f worker'",
        "bash -ec 'git stash push'",
        # an -o/-O optname that closes a cluster takes its value as the next word, and a
        # valued long option its word too: the -c AFTER them is still the shell's (critic:
        # `-o NAME` two-word, `--rcfile`)
        "bash -xo pipefail -c 'git push origin main'",
        "bash -xO extglob -c 'git push origin main'",
        "bash --rcfile /tmp/rc -c 'git push origin main'",
        # a `+` form is an option too (`bash +x -c`), and the LAST `-c` before an operand wins
        # over an earlier `-c` whose value an `-o`/`-O` consumes (`bash -co pipefail -c`)
        "bash +x -c 'git push origin main'",
        "bash +o pipefail -c 'git push origin main'",
        "bash -co pipefail -c 'git push origin main'",
        "bash -cO extglob -c 'git push origin main'",
        # `-c` reads the FIRST non-option argument, not the next literal word: an option word
        # (`-x`, `--`, `-`) or a `-o`-consumed optname AFTER `-c` does not stop it, so these
        # all RUN `git push` too (critic #6)
        "bash -co pipefail 'git push origin main'",
        "bash -c -x 'git push origin main'",
        "bash -c -- 'git push origin main'",
        "bash -c - 'git push origin main'",
    ],
)
def test_refused_clustered_c_flag(cmd):
    r = _check(cmd, cwd="/nonexistent")
    assert r is not None, cmd
    assert "Instead:" in r.message()


def test_an_explicit_read_only_stash_entry_still_forbids_it():
    """The read-only exemption only narrows the GENERIC `git stash` entry: a config that
    names `git stash list` explicitly still forbids `git stash list`."""
    cfg = Config()
    cfg.hooks.forbidden_commands = ["git stash list"]
    assert FB.check("git stash list", cfg, env={}) is not None
    assert FB.check("git stash show", cfg, env={}) is None


def test_a_stash_write_verb_is_still_refused_even_with_a_help_looking_arg():
    """Only the `list`/`show` subcommands are read-only: `--help`/`--message` as a VALUE or
    pathspec are not a help flag, so `git stash push` with them still writes and is refused."""
    assert _check("git stash push -m --help") is not None
    assert _check("git stash push -- --help") is not None


def test_reset_hard_only_on_primary(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert _check("git reset --hard HEAD", cwd=tmp_path) is not None
    assert _check("git reset --soft HEAD~1", cwd=tmp_path) is None
    assert _check("git -C /elsewhere reset --hard", cwd=tmp_path / "nope") is not None
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=a",
            "-c",
            "user.email=a@b",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "x",
        ],
        check=True,
    )
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(tmp_path), "worktree", "add", "-q", str(wt)], check=True)
    assert _check("git reset --hard HEAD", cwd=wt) is None
    # from a worktree, a command that names another checkout is still judged as the primary
    assert _check(f"git -C {tmp_path} reset --hard", cwd=wt) is not None
    assert _check(f"cd {tmp_path} && git reset --hard", cwd=wt) is not None
    assert _check(f"git --git-dir={tmp_path}/.git reset --hard", cwd=wt) is not None
    assert _check(f"GIT_DIR={tmp_path}/.git git reset --hard", cwd=wt) is not None
    assert _check("make -C build && git reset --hard", cwd=wt) is None
    assert _check('echo GIT_DIR=x && git commit -m "GIT_DIR=y" && git reset --hard', cwd=wt) is None


def test_operator_lifts_per_session_and_config():
    assert _check("pkill x", {FB.ENV_ALLOW: "pkill"}) is None
    assert _check("pkill x", {FB.ENV_ALLOW: "git stash"}) is not None
    assert _check("git reset --hard", {FB.ENV_ALLOW: "git reset --hard @primary"}) is None
    assert _check("git push", {FB.ENV_ALLOW: "all"}) is None
    cfg = Config()
    cfg.hooks.forbidden_commands = []
    assert FB.check("pkill x", cfg, env={}) is None


def _hook(repo: Path, payload: dict) -> str:
    p = subprocess.run(
        [sys.executable, "-m", "ddflow", "hooks", "run", "pre_tool", "--harness", "claude"],
        cwd=repo,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={
            **__import__("os").environ,
            "DDFLOW_HOOKS_ALLOW": "",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        },
    )
    assert p.returncode == 0, p.stderr
    return p.stdout


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run([sys.executable, "-m", "ddflow", "init"], cwd=tmp_path, capture_output=True)
    return tmp_path


def test_hook_denies_and_records(tmp_path):
    repo = _repo(tmp_path)
    out = _hook(
        repo,
        {"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": "pkill -f pytest"}},
    )
    reply = json.loads(out)["hookSpecificOutput"]
    assert reply["permissionDecision"] == "deny"
    assert "pkill" in reply["permissionDecisionReason"]
    events = [
        json.loads(x)
        for f in (repo / ".ddflow" / "events").rglob("*.jsonl")
        for x in f.read_text().splitlines()
    ]
    assert any(
        e["kind"] == "session.note"
        and str(e["data"].get("source", "")).startswith("forbidden-command")
        for e in events
    )


def test_hook_allows_other_commands_and_tools(tmp_path):
    repo = _repo(tmp_path)
    assert _hook(repo, {"tool_name": "Bash", "tool_input": {"command": "ls"}}) == ""
    assert _hook(repo, {"tool_name": "Edit", "tool_input": {"command": "pkill"}}) == ""
    assert _hook(repo, {}) == ""
