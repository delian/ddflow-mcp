"""The refusal of a bare pass names the flags that supply evidence (gate-record-evidence-message-omits-flags).

`gate record <id> <an evidence gate> --outcome passed` was refused with "Attach the command,
its exit code and its output", but the flags that do (`--evidence`, `--command`, `--exit-code`,
`--output-file`) appeared only in `--help`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

FAIL = 1


def test_a_bare_pass_of_an_evidence_gate_names_the_flags_that_give_evidence(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    assert run_cli(repo, "claim", "T1")[0] == 0
    code, out, err = run_cli(repo, "gate", "record", "T1", "unit_tests", "--outcome", "passed")
    text = out + err
    assert code == FAIL and "requires evidence" in text, text
    for flag in ("--evidence", "--command", "--exit-code", "--output-file"):
        assert flag in text, f"{flag} not named in: {text}"
    assert "--outcome unavailable --reason" in text
