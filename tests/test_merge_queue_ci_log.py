"""A parked merge-queue entry says which check failed and keeps the ci log (B539ec3de5a).

`queue run` used to park an item with only 'ci failed: <command> exited 1': the failing hook
and the output were gone, so the cause was found by rerunning the stage by hand.
"""

from __future__ import annotations

import json

from conftest import run_cli
from test_merge_queue import _finish, _git, _queue, _status, proj  # noqa: F401

from ddflow.services import mergequeue as MQ

NOISY = """\
import sys

print("trailing whitespace.....................................Passed")
print("ruff check..............................................Failed")
print("- hook id: ruff")
for i in range(40):
    print(f"noise line {i}")
print("FAILED tests/test_x.py::test_y - AssertionError: boom")
print("E501 the very last line of the output")
sys.exit(1 if any("BAD" in p.read_text() for p in Path("knobs").glob("*.txt")) else 0)
"""

SILENT = (
    "import sys\nfrom pathlib import Path\n"
    "bad = any('BAD' in p.read_text() for p in Path('knobs').glob('*.txt'))\nsys.exit(1 if bad else 0)\n"
)


def test_a_passing_run_keeps_no_log_and_a_silent_failure_keeps_an_empty_one(proj):  # noqa: F811
    (proj / "check.py").write_text(SILENT)
    _git(proj, "add", "-A")
    _git(proj, "commit", "-qm", "silent check")
    _finish(proj, "T1", "BAD\n")
    _finish(proj, "T2")
    for item in ("T1", "T2"):
        assert _queue(proj, item)[0] == 0
    run_cli(proj, "--json", "queue", "run", agent="integrator")
    rows = _status(proj)
    assert rows["T2"]["state"] == "merged" and rows["T2"]["log"] == ""
    assert rows["T1"]["log"] and (proj / rows["T1"]["log"]).read_text() == ""
    assert rows["T1"]["failed"] == ["command"]
    logs = list((proj / MQ.QUEUE_DIR / "logs").glob("*"))
    assert [p.name.split("-", 1)[1] for p in logs] == ["T1.log"]


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
    assert row["failed"] == ["ruff check", "tests/test_x.py::test_y"]
    assert "very last line" in row["tail"] and "noise line 0" not in row["tail"]
    parked = _status(proj)["T1"]
    assert (
        parked["failed"] == ["ruff check", "tests/test_x.py::test_y"]
        and "very last line" in parked["tail"]
    )
    log = proj / parked["log"]
    assert log.is_file() and ".ddflow/local/mergequeue/" in str(log.relative_to(proj))
    assert "noise line 0" in log.read_text()  # the whole output, not just the tail
    code, text, err = run_cli(proj, "queue", "status")
    assert code == 0, err
    assert "failed: ruff check" in text and "very last line" in text and parked["log"] in text
