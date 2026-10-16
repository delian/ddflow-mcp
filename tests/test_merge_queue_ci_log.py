"""A parked merge-queue entry says which check failed and keeps the ci log (B539ec3de5a).

`queue run` used to park an item with only 'ci failed: <command> exited 1': the failing hook
and the output were gone, so the cause was found by rerunning the stage by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import run_cli
from test_merge_queue import _finish, _git, _queue, _status, proj  # noqa: F401

NOISY = """\
import sys
from pathlib import Path

print("trailing whitespace.....................................Passed")
print("ruff check..............................................Failed")
print("- hook id: ruff")
for i in range(40):
    print(f"noise line {i}")
print("E501 the very last line of the output")
sys.exit(1 if any("BAD" in p.read_text() for p in Path("knobs").glob("*.txt")) else 0)
"""


def test_a_parked_entry_names_the_failing_hook_and_keeps_the_ci_log(proj):  # noqa: F811
    (proj / "check.py").write_text(NOISY)
    _git(proj, "add", "-A")
    _git(proj, "commit", "-qm", "noisy check")
    _finish(proj, "T1", "BAD\n")
    q = _queue(proj, "T1")
    assert q[0] == 0, q
    code, out, err = run_cli(proj, "--json", "queue", "run", agent="integrator")
    assert code == 1, out + err
    row = json.loads(out)["processed"][0]
    assert row["failed"] == ["ruff check"]
    assert "very last line" in row["tail"] and "noise line 0" not in row["tail"]
    parked = _status(proj)["T1"]
    assert parked["failed"] == ["ruff check"] and "very last line" in parked["tail"]
    log = proj / parked["log"]
    assert log.is_file() and ".ddflow/local/mergequeue/" in str(log.relative_to(proj))
    assert "noise line 0" in log.read_text()  # the whole output, not just the tail
    code, text, err = run_cli(proj, "queue", "status")
    assert code == 0, err
    assert "failed: ruff check" in text and "very last line" in text and parked["log"] in text
    assert isinstance(log, Path)
