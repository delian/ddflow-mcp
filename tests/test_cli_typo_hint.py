"""`ddflow <typo>` prints the nearest-command hint first and a --help pointer, not the
~3 KB usage line that lists all ~80 commands with the hint buried at the end (UX-typo-first).

`ddflow clam T1` used to print one 2 KB `usage:` line listing every command, then the
standard `argument cmd: invalid choice: 'clam' (choose from 'init', 'phase', ...) Did you
mean 'claim'?` — the one actionable word came after the whole command list. An agent or
person had to read 80 command names to find it.
"""

from conftest import run_cli, repo  # noqa: F401


def test_a_command_typo_puts_the_hint_first_and_drops_the_command_dump(repo):
    code, out, err = run_cli(repo, "clam")
    assert code == 2, err
    lines = err.splitlines()
    assert "Did you mean 'claim'?" in lines[0], err
    # The ~80-word command dump is gone: the nearest command is said, not listed.
    assert "choose from" not in err, err
    # One line points at the full list instead.
    assert "--help" in err, err
    assert len(lines) <= 5, err


def test_a_typo_with_no_near_command_still_points_at_help_without_a_hint(repo):
    code, out, err = run_cli(repo, "zzzzddflow")
    assert code == 2, err
    assert "Did you mean" not in err, err
    assert "--help" in err, err
    assert "choose from" not in err, err
    assert len(err.splitlines()) <= 5, err
