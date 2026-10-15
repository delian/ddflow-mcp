"""Documentation check extractors (B-docscheck-extractors): [[docs.extract]] surface,
`<cmd> --help` walking and opt-in count markers.

ddflow's own argparse is the help fixture: its verbs and flags are real, and the
`--reason,` punctuation case must not hit.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from ddflow.config import Config
from ddflow.infra import proc as P
from ddflow.services import docscheck as D
from ddflow.services import docscheck_help as H

PYPROJECT = '[project]\nname = "x"\n[project.scripts]\nddflow = "x:main"\n'
CLI = """\
import argparse

def build():
    p = argparse.ArgumentParser()
    s = p.add_subparsers()
    s.add_parser("run").add_argument("--alpha")
    s.add_parser("run").add_argument("--beta")
    KNOBS = ("docs.alpha", "docs.beta")
    TOOLS = ["tool_one", "tool_two"]
"""


def repo(tmp_path: Path, readme: str, **extra: str) -> Path:
    files = {"README.md": readme, "pkg/cli.py": CLI, "pyproject.toml": PYPROJECT, **extra}
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def found(report):
    return sorted((f.kind, f.ref) for f in report.findings)


def ddflow_runner(root):
    """The real `ddflow` (this checkout) for `ddflow ...`, whatever is on PATH."""
    real = H.command_runner(root, 60)

    def run(argv):
        return real([sys.executable, "-m", "ddflow", *argv[1:]] if argv[0] == "ddflow" else argv)

    return run


# -- [[docs.extract]] ----------------------------------------------------------------

FLAGS = {"kind": "flags", "pattern": r"add_argument\(\"(--[a-z-]+)\"", "source_globs": ["pkg/*.py"]}
KNOBS = {"kind": "knobs", "pattern": r"\"(docs\.[a-z]+)\"", "source_globs": ["pkg/*.py"]}
TOOLS = {"kind": "tools", "pattern": r"\"(?P<name>tool_[a-z]+)\"", "source_globs": ["**/cli.py"]}


def test_an_undocumented_flag_tool_and_knob_are_found_at_their_source_line(tmp_path):
    readme = "Use `--alpha` with `[docs].alpha` and `tool_one`.\n"
    rep = D.check_docs(repo(tmp_path, readme), extract=[FLAGS, KNOBS, TOOLS])
    assert found(rep) == [("command", "tool_two"), ("flag", "--beta"), ("identifier", "docs.beta")]
    beta = next(f for f in rep.findings if f.ref == "--beta")
    assert (beta.path, beta.line) == ("pkg/cli.py", 7)
    assert "no checked document mentions it" in beta.detail
    assert rep.checked["extracted"] == 6


def test_a_documented_surface_is_clean_and_ignore_names_are_owned_elsewhere(tmp_path):
    readme = "`--alpha` `--beta` `docs.alpha` `docs.beta` `tool_one`\n"
    root = repo(tmp_path, readme)
    assert found(D.check_docs(root, extract=[FLAGS, KNOBS, TOOLS])) == [("command", "tool_two")]
    rep = D.check_docs(root, extract=[TOOLS], ignore_names=["tool_*"])
    assert rep.clean


def test_an_extractor_that_cannot_work_is_refused_not_silent(tmp_path):
    root = repo(tmp_path, "x\n")
    for bad in (
        {**FLAGS, "kind": "widgets"},
        {**FLAGS, "pattern": "no group"},
        {**FLAGS, "pattern": "("},
        {**FLAGS, "source_globs": []},
    ):
        with pytest.raises(ValueError, match=r"docs\.extract"):
            D.check_docs(root, extract=[bad])


# -- help walking -------------------------------------------------------------------

README_CLI = """\
Skip a gate with `ddflow gate skip <id> <gate> --reason "why"`, giving `--reason,` a reason.

```console
$ ddflow gate skip B-x research --reason "no net"
$ ddflow gate skip B-x research --nonesuch
$ ddflow review B-x --gate critic
```
"""


def test_help_walking_resolves_the_verbs_own_flags_and_not_the_punctuation(tmp_path):
    root = repo(
        tmp_path,
        README_CLI,
        **{"pkg/cli.py": "add_parser('skip') 'review' 'gate' --reason --gate\n"},
    )
    rep = D.check_docs(root, runner=ddflow_runner(root), commands=["ddflow"])
    assert [(f.ref, f.line) for f in rep.findings if f.kind == "flag"] == [
        ("ddflow gate skip --nonesuch", 5)
    ]
    assert (
        rep.checked["verb_flags"] == 3
    )  # the fenced lines; `--reason,` is a bare flag, not a verb mention


def test_a_flag_another_verb_has_is_not_this_verbs(tmp_path):
    readme = "Run `ddflow gate status --reason x`.\n"
    root = repo(tmp_path, readme, **{"pkg/cli.py": "add_parser('status') 'gate' --reason\n"})
    rep = D.check_docs(root, runner=ddflow_runner(root), commands=["ddflow"])
    assert [f.ref for f in rep.findings] == ["ddflow gate status --reason"]


def test_without_a_runner_nothing_is_spawned(tmp_path):
    root = repo(
        tmp_path,
        "`ddflow gate skip --nonesuch`\n",
        **{"pkg/cli.py": "'skip' 'gate' --nonesuch add_parser\n"},
    )
    assert D.check_docs(root, commands=["ddflow"]).clean
    asked = D.check_docs(root, commands=["ddflow"], help_commands=["ddflow"])
    assert [(n.kind, "no command runner" in n.detail) for n in asked.notes] == [("help", True)]


def test_a_tool_that_cannot_run_is_a_note_never_a_pass(tmp_path):
    root = repo(
        tmp_path, "`nosuchtool run --alpha`\n", **{"pkg/cli.py": "'run' --alpha add_parser\n"}
    )
    rep = D.check_docs(root, runner=H.command_runner(root, 5), commands=["nosuchtool"])
    assert rep.clean
    assert [(n.kind, n.ref) for n in rep.notes] == [("help", "nosuchtool")]
    assert "could not run" in rep.notes[0].detail


def test_help_flags_reads_definitions_not_prose():
    text = (
        "usage: t run [-h] [--alpha A]\n"
        "                  [--beta B]\n\n"
        "options:\n"
        "  -h, --help       show this help\n"
        "  --gamma G        like --delta but longer\n"
    )
    assert H.help_flags(text) == {"--help", "--alpha", "--beta", "--gamma"}
    assert H.flag_of("--reason,") == "--reason" and H.flag_of("--a=b") == "--a"
    assert H.flag_of("--Bad") is None and H.flag_of("word") is None


def test_each_help_chain_runs_once(tmp_path):
    calls = []

    def run(argv):
        calls.append(argv)
        ok = len(argv) <= 3
        return P.Captured(0 if ok else 2, "usage: t [--x X]\n" if ok else "", "")

    idx = H.HelpIndex(run, ["t"])
    for _ in range(3):
        assert idx.resolve(["t", "go", "--x"]) == (["t", "go"], {"--x"})
    assert calls == [["t", "--help"], ["t", "go", "--help"]]


# -- counts -------------------------------------------------------------------------


def counting(table):
    def run(argv):
        out = table.get(tuple(argv))
        return P.Captured(0, out, "") if out is not None else P.Captured(1, "", "no")

    return run


def test_a_marker_that_agrees_is_clean_and_one_that_drifted_is_a_finding(tmp_path):
    readme = (
        'There are <!-- ddflow:count cmd="ddflow list">3<!-- /ddflow:count --> things.\n'
        'And <!-- ddflow:count cmd="ddflow list">9<!-- /ddflow:count --> more.\n'
    )
    root = repo(tmp_path, readme)
    run = counting({("ddflow", "list"): "a\nb\nc\n"})
    rep = D.check_docs(root, runner=run, commands=["ddflow"])
    assert [(f.kind, f.line) for f in rep.findings] == [("count", 2)]
    assert "states 9" in rep.findings[0].detail and "says 3" in rep.findings[0].detail
    assert rep.checked["counts"] == 2


def test_marker_commands_are_not_run_unless_a_configured_tool_and_never_in_a_shell(tmp_path):
    readme = (
        '<!-- ddflow:count cmd="rm -rf x">1<!-- /ddflow:count -->\n'
        '<!-- ddflow:count cmd="ddflow list; rm x">1<!-- /ddflow:count -->\n'
    )
    ran = []

    def run(argv):
        ran.append(argv)
        return P.Captured(0, "1\n", "")

    rep = D.check_docs(repo(tmp_path, readme), runner=run, commands=["ddflow"])
    assert [n.kind for n in rep.notes] == ["count"]
    assert "not a configured tool" in rep.notes[0].detail
    assert ran == [["ddflow", "list;", "rm", "x"]]  # an argv: ';' is an argument, not a shell


def test_marker_examples_in_code_are_not_claims(tmp_path):
    readme = (
        'Write `<!-- ddflow:count cmd="ddflow list">1<!-- /ddflow:count -->`.\n\n'
        '```md\n<!-- ddflow:count cmd="ddflow list">1<!-- /ddflow:count -->\n```\n'
    )
    rep = D.check_docs(repo(tmp_path, readme), runner=counting({}), commands=["ddflow"])
    assert rep.clean and not rep.notes


def test_a_docs_count_entry_recomputes_the_number_a_pattern_finds(tmp_path):
    readme = "ddflow has 12 knobs in all.\n\nIt has 7 knobs in docs, 12 total.\n"
    entry = {"pattern": r"(\d+) knobs", "command": "ddflow knobs"}
    run = counting({("ddflow", "knobs"): "12\n"})
    rep = D.check_docs(repo(tmp_path, readme), count=[entry], runner=run, commands=["ddflow"])
    assert [(f.line, f.ref) for f in rep.findings] == [(3, "7 knobs")]
    assert "says 12" in rep.findings[0].detail


def test_counts_without_a_runner_are_noted_not_passed(tmp_path):
    readme = '<!-- ddflow:count cmd="ddflow list">1<!-- /ddflow:count -->\n'
    rep = D.check_docs(repo(tmp_path, readme), commands=["ddflow"])
    assert rep.clean and rep.notes[0].kind == "count" and "no command runner" in rep.notes[0].detail


# -- the config section -------------------------------------------------------------


def _load(tmp_path, text):
    (tmp_path / ".ddflow").mkdir(exist_ok=True)
    (tmp_path / ".ddflow" / "config.toml").write_text(text)
    return Config.load(tmp_path)


def test_docs_section_defaults_and_array_of_tables(tmp_path):
    assert Config().docs.extract == [] and Config().docs.command_timeout_s == 20
    cfg = _load(
        tmp_path,
        '[[docs.extract]]\nkind = "flags"\npattern = "(--x)"\nsource_globs = ["a/*.py"]\n'
        '[[docs.count]]\npattern = "(\\\\d+) x"\ncommand = "t n"\n[docs]\ncli_command = ["t"]\n',
    )
    assert cfg.docs.extract[0]["kind"] == "flags" and cfg.docs.cli_command == ["t"]
    assert cfg.docs.count[0]["command"] == "t n"


@pytest.mark.parametrize(
    "data",
    [
        {"extract": [{"kind": "x", "pattern": "(a)", "source_globs": ["a"]}]},
        {"extract": [{"kind": "flags", "pattern": "a", "source_globs": ["a"]}]},
        {"extract": [{"kind": "flags", "pattern": "(a)"}]},
        {"count": [{"pattern": "(a)", "command": " "}]},
        {"command_timeout_s": 0},
    ],
)
def test_a_bad_docs_value_is_refused(data):
    with pytest.raises(ValueError):
        Config.check({"docs": data})


def test_check_configured_applies_the_docs_section(tmp_path):
    from ddflow.services.docscheck_config import check_configured

    cfg = Config()
    cfg.docs.extract = [FLAGS]
    cfg.docs.cli_command = ["nosuchtool"]
    root = repo(tmp_path, "`--alpha` and `nosuchtool run --alpha`\n")
    off = check_configured(root, cfg, run_commands=False)
    assert found(off) == [("flag", "--beta")]  # nothing run: only a note that help was not walked
    assert [n.kind for n in off.notes] == ["help"]
    on = check_configured(root, cfg)  # the walked tool is missing: a note, never a pass
    assert found(on) == [("flag", "--beta")]
    assert [(n.kind, n.ref) for n in on.notes] == [("help", "nosuchtool")]


def test_blank_and_one_word_lines_in_a_fence_do_not_crash_the_help_walk(tmp_path):
    readme = "```console\n\n   \nddflow\n$ ddflow gate status\n```\n"
    root = repo(tmp_path, readme, **{"pkg/cli.py": "add_parser('status') 'gate'\n"})
    assert D.check_docs(root, runner=ddflow_runner(root), commands=["ddflow"]).clean


def test_a_multiword_tool_name_is_documented_by_its_spelling(tmp_path):
    cli = "add_parser('docs') CMDS = ['ddflow docs check']\n"
    ex = {"kind": "tools", "pattern": r"'(ddflow docs check)'", "source_globs": ["pkg/*.py"]}
    root = repo(tmp_path, "Run `ddflow   docs\ncheck`.\n", **{"pkg/cli.py": cli})
    assert D.check_docs(root, extract=[ex]).clean
    root2 = repo(tmp_path / "b", "Run `ddflow docs`.\n", **{"pkg/cli.py": cli})
    assert found(D.check_docs(root2, extract=[ex])) == [("command", "ddflow docs check")]


def test_the_walked_help_is_the_authority_on_a_script_s_verbs(tmp_path):
    readme = "Run `ddflow gate skip --reason x`.\n"
    root = repo(tmp_path, readme, **{"pkg/cli.py": "# no verbs registered here\n"})
    plain = D.check_docs(root, commands=["ddflow"])
    assert found(plain) == [("command", "ddflow gate")]  # the corpus alone cannot tell
    rep = D.check_docs(root, runner=ddflow_runner(root), commands=["ddflow"])
    assert rep.clean, found(rep)
