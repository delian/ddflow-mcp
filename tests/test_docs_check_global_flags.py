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
    assert not any(
        ref.endswith((" --json", " --agent", " --repo")) for ref in refs
    ), f"global flag reported missing: {sorted(refs)}"
    assert rep.checked["verb_flags"] == 3  # each mention's flag still judged, not skipped
