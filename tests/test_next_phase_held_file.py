"""`next --phase` reports a conflict, never "ready", for an item another lane's live
lease already covers.

B-next-ready-over-held-files: `ddflow next --phase P-config-engine` printed "Ready
(1 ready, 0 running)" for CE-defaults-file while its `.ddflow/pins.toml` write was
under the live lease sw-quotameters held on B-quota-meters; the claim straight after
was refused. `ddflow_next` promises the reason for everything that may not start, so an
item whose files are under another lane's live lease must be in `blocked` (reason
"conflict", the held file named), never in `ready`.
"""

import json
import time

from _cli_helpers import run_cli


def _pinned_scenario(log):
    log.append("phase.added", "P-config-engine", {"title": "phase"})
    log.append("phase.added", "B-quota-meters", {"title": "phase"})
    log.append(
        "task.added",
        "CE-defaults-file",
        {"parent": "P-config-engine", "priority": 1, "globs": [".ddflow/pins.toml"]},
    )
    log.append(
        "task.added",
        "BQ",
        {"parent": "B-quota-meters", "priority": 1, "globs": [".ddflow/pins.toml"]},
    )
    log.append(
        "lease.acquired",
        "BQ",
        {
            "holder": "sw-quotameters",
            "at": time.time(),
            "ttl_s": 1800,
            "globs": [".ddflow/pins.toml"],
        },
    )
    log.append("item.started", "BQ", {})


def test_next_phase_blocks_an_item_whose_file_is_under_a_live_lease(log, repo):
    _pinned_scenario(log)
    code, out, _err = run_cli(repo, "next", "--phase", "P-config-engine", "--json", agent="sw-cedefaults")
    assert code == 2, out
    data = json.loads(out)
    assert data["ready"] == [], out
    ce = {b["item"]: b for b in data["blocked"]}["CE-defaults-file"]
    assert ce["reason"] == "conflict"
    assert "held by sw-quotameters" in ce["detail"]
    assert ce["waiting_on"] == ["BQ"]


def test_next_phase_still_offers_an_independent_item_beside_the_held_file(log, repo, cfg):
    _pinned_scenario(log)
    log.append(
        "task.added",
        "CE-independent",
        {"parent": "P-config-engine", "priority": 2, "globs": ["ddflow/config/_core.py"]},
    )
    code, out, _err = run_cli(repo, "next", "--phase", "P-config-engine", "--json", agent="sw-cedefaults")
    assert code == 0, out
    data = json.loads(out)
    assert [r["id"] for r in data["ready"]] == ["CE-independent"], out
    # The held item is reported, with its reason, beside the independent one it does not shadow.
    assert {b["item"] for b in data["blocked"]} == {"CE-defaults-file"}, out
