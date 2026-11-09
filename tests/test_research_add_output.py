"""The output a `ddflow research add` prints, beside the record it files.

The duplicate check (`api/_dedupe.py`, decision D-no-duplicates) lists the records the
new text reads like; for a filed-anyway add those candidates (`shares: ...`) used to bury
the confirmation, so the filed id was easy to miss. The last line of a successful add's
stdout is the confirmation, however many candidates the check also reported (bug
Ba3d7e2ecfe).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def test_a_successful_add_ends_on_the_confirmation_line(repo, monkeypatch):
    run_cli(repo, "init")
    # `warn` files the add whatever it reads like, so the candidate list is printed and
    # the add still succeeds -- exactly the race the confirmation must not be lost in.
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "warn")
    run_cli(
        repo,
        "research",
        "add",
        "--question",
        "does it parse?",
        "--verdict",
        "CONFIRMED",
        "--probe",
        "x",
    )
    code, out, err = run_cli(
        repo,
        "research",
        "add",
        "--question",
        "does it parse this new query",
        "--verdict",
        "CONFIRMED",
        "--probe",
        "x",
    )
    assert code == 0, err
    # The confirmation is the last line of stdout, whatever tile the check printed beside it.
    assert re.search(r"research \w+ recorded \(CONFIRMED\)\s*$", out), (out, err)
