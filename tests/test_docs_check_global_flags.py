"""`docs check` must not flag a GLOBAL option mentioned after a subcommand.

`--json`, `--agent` and `--repo` are root flags mirrored onto every subparser with
``help=SUPPRESS`` (`_accept_global_options_anywhere`), so ``ddflow next --json`` parses fine
yet ``ddflow next --help`` never lists ``--json``. The help walk descends to the deepest verb
and judges a mention against that verb's own ``--help``, so it reported ``ddflow next --json``
as missing. A global flag accepted after the subcommand is not a missing flag (B1da555743c).
"""

from __future__ import annotations

import subprocess

from ddflow.services import docscheck as D
from ddflow.services import docscheck_help as H


def _repo_with_readme(tmp_path, readme: str):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[project.scripts]\nddflow = "x:main"\n', encoding="utf-8"
    )
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def test_global_flags_after_a_subcommand_are_not_flagged_missing(tmp_path):
    readme = (
        "Run `ddflow next --json` to list, `ddflow claim T1 --agent x` to claim, "
        "and `ddflow show NOP --repo .` to inspect.\n"
    )
    root = _repo_with_readme(tmp_path, readme)
    rep = D.check_docs(root, runner=H.command_runner(root, 60), commands=["ddflow"])
    refs = {f.ref for f in rep.findings if f.kind == "flag"}
    assert not any(ref.endswith((" --json", " --agent", " --repo")) for ref in refs), (
        f"global flag reported missing: {sorted(refs)}"
    )
    assert rep.checked["verb_flags"] == 3  # each mention's flag still judged, not skipped


def test_a_flag_the_verbs_help_does_not_list_is_still_flagged(tmp_path):
    readme = "Run `ddflow next --bogus --json` to list.\n"
    root = _repo_with_readme(tmp_path, readme)
    rep = D.check_docs(root, runner=H.command_runner(root, 60), commands=["ddflow"])
    refs = {f.ref for f in rep.findings if f.kind == "flag"}
    assert "ddflow next --bogus" in refs  # a genuine missing flag still fails the check
    assert not any(ref.endswith(" --json") for ref in refs)  # the global flag stays exempt


def test_the_exempt_set_has_not_drifted_from_the_mirrored_root_options():
    """A root option mirrored onto every subparser but left out of `_GLOBAL_FLAGS` is a
    silent false finding the moment a doc spells it after a subcommand. `_accept_global_
    options_anywhere` mirrors every root store/store-true option, so only `--allow-older-
    version` and `--reason` (deliberately not exempt: every checked doc spells them after a
    verb that defines its own copy) may sit outside `_GLOBAL_FLAGS`; anything else is a new
    root option that drifted in and must be added here (B1da555743c)."""
    import argparse

    from ddflow.surfaces.cli import build_parser

    root = build_parser()
    # Mirror the CLI's own filter (`_accept_global_options_anywhere`): a `_StoreTrueAction`
    # (by isinstance) and a bare `_StoreAction` (by exact type) are mirrored; every other
    # action (`--version`'s `_PrintVersion`, help, subparsers) is not.
    mirrored = {
        option
        for action in root._actions
        if not isinstance(action, (argparse._HelpAction, argparse._SubParsersAction))
        and (isinstance(action, argparse._StoreTrueAction) or type(action) is argparse._StoreAction)
        for option in action.option_strings
    }
    free = mirrored - set(D._GLOBAL_FLAGS)
    assert free <= {"--allow-older-version", "--reason"}, sorted(free)
