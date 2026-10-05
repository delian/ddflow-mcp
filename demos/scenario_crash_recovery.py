"""Scenario 2 — an agent is killed mid-task, and the work is recovered, not lost.

This is the scenario the whole lease design exists for. The invented project is a
feed parser; agent DELTA claims a task, commits one change, leaves a second change
uncommitted, and then dies without releasing anything.

What must happen next, in order:

1. Nothing is lost or touched. The worktree still holds both the commit and the
   uncommitted file.
2. While the lease is live, the item stays claimed — a dead agent is indistinguishable
   from a slow one until the lease expires, and guessing is how work gets destroyed.
3. Once expired, `ddflow recover` finds it, measures the tree, and says what is in it.
4. A new agent may NOT silently take it. It must acknowledge the situation.
5. After salvage, the item returns to the pool and the work is still on disk.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from harness import Scenario

#: The lease TTL the scenario runs under: short, so expiry is observable inside a test.
TTL_S = 8
#: How many times the "still claimed" checks are re-observed when the machine was too
#: slow to finish them inside one lease (B11e64b1e8c).
LIVE_ATTEMPTS = 6

SCAFFOLD = {
    "README.md": "# feedparse\n",
    ".gitignore": "__pycache__/\n*.pyc\n.pytest_cache/\n",
    "feedparse/__init__.py": "",
    "tests/__init__.py": "",
}


def _while_live(sc: Scenario, check):
    """Run ``check`` inside DELTA's live lease and return what it returned.

    Judged against the LEASE'S OWN CLOCK, not a margin: the check counts only when it
    FINISHED before the lease that DELTA's last heartbeat started could expire -- an
    upper bound on when it looked. At load average 100-190 one process could outlive an
    8 s lease, and the scenario failed on a correct answer (B11e64b1e8c, after
    Bdc7fe4dbbb widened the margin once). So a check that ran too late is re-observed
    after a fresh heartbeat; a wrong answer inside the window still fails. One check per
    window: two in a row would put the second one's start beyond the bound.
    """
    for attempt in range(1, LIVE_ATTEMPTS + 1):
        sc.ddflow("heartbeat", "P1.T1", agent="delta")  # DELTA's last one
        result = check()
        done = time.time()
        renewed = sc.jddflow("show", "P1.T1")["lease"]["renewed_at"]
        if done - renewed < TTL_S:
            return result
        sc.note(
            f"attempt {attempt}: the check finished {done - renewed:.1f}s after the "
            f"heartbeat, past the {TTL_S}s lease, so it did not observe the live window; "
            "heartbeat again and re-observe"
        )
    sc.check(
        f"the live window was observed within {LIVE_ATTEMPTS} attempts",
        False,
        f"every attempt outlived the {TTL_S}s lease (load average {os.getloadavg()})",
    )
    raise AssertionError("unreachable: a failed check raises")


def run(sc: Scenario) -> None:
    sc.head("SCENARIO 2 — crash mid-task, then recovery without losing work")
    sc.make_repo("feedparse", SCAFFOLD)

    sc.step("Set up a short lease TTL so a crash is observable inside a test")
    sc.ddflow("init")
    # Commit the setup, as a real operator does: `init` touches tracked files
    # (.gitignore, .gitattributes) and an uncommitted change there leaves the primary
    # checkout dirty, which `ddflow merge` refuses.
    sc.git("add", "-A")
    sc.git("-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "ddflow: adopt")
    sc.write(
        ".ddflow/config.toml",
        f"""
        [lease]
        ttl_s = {TTL_S}
        grace_s = 0
        heartbeat_s = 1

        [gates]
        # keep the demo focused on recovery
        required = ["implement", "merge"]
    """,
    )
    sc.ddflow("phase", "add", "P1", "--title", "Parsing")
    sc.ddflow(
        "task",
        "add",
        "P1.T1",
        "--phase",
        "P1",
        "--title",
        "RSS reader",
        "--globs",
        "feedparse/rss.py",
    )
    sc.ddflow(
        "task",
        "add",
        "P1.T2",
        "--phase",
        "P1",
        "--title",
        "Atom reader",
        "--globs",
        "feedparse/atom.py",
    )

    sc.step("Agent DELTA claims the task and starts working")
    sc.ddflow("claim", "P1.T1", agent="delta")
    wt = Path(sc.jddflow("show", "P1.T1")["worktree"])
    sc.write(
        "feedparse/rss.py",
        '''
        """RSS 2.0 reader (committed before the crash)."""
        import xml.etree.ElementTree as ET

        def parse(text):
            root = ET.fromstring(text)
            return [{"title": i.findtext("title"), "link": i.findtext("link")}
                    for i in root.iterfind(".//item")]
    ''',
        repo=wt,
    )
    committed = sc.commit_in(wt, "P1.T1: rss reader")
    sc.write(
        "feedparse/rss_dates.py",
        '''
        """Date normalisation — FINISHED but never committed. This file is the whole
        point of the scenario: it exists nowhere except this worktree."""
        from email.utils import parsedate_to_datetime

        def normalise(raw):
            return parsedate_to_datetime(raw).isoformat()
    ''',
        repo=wt,
    )
    sc.check(
        "the worktree holds one commit and one uncommitted file",
        sc.git("rev-list", "--count", "main..HEAD", repo=wt) == "1"
        and (wt / "feedparse/rss_dates.py").exists(),
    )

    sc.step("DELTA heartbeats one last time, then is killed — no release, nothing")
    sc.note(
        "Simulated exactly as a real kill would leave things: the process simply "
        "stops. No cleanup code runs, because in a real crash none does. Each check "
        "below starts from that last heartbeat, re-taken only when the machine was too "
        "slow to finish the check inside the lease it started."
    )

    sc.step("Immediately after the crash, the item is still CLAIMED")
    code, _, err = _while_live(
        sc, lambda: sc.ddflow("claim", "P1.T1", agent="epsilon", expect=None)
    )
    sc.check("another agent is refused while the lease is still live", code == 3, err)
    code = _while_live(sc, lambda: sc.ddflow("recover", expect=None))[0]
    sc.check("recover reports nothing yet — a dead agent looks like a slow one", code == 2)
    sc.note(
        "This is deliberate. Treating a momentarily-quiet agent as dead is how two "
        "agents end up in one worktree."
    )

    sc.step("Wait for the lease to expire, then run recovery")
    # Poll rather than sleep a fixed time: expiry is wall-clock, and how long the steps
    # above took varies with the machine's load.
    deadline = time.monotonic() + TTL_S + 120
    while sc.ddflow("recover", expect=None)[0] != 0 and time.monotonic() < deadline:
        time.sleep(0.5)
    found = sc.jddflow("recover")
    sc.check("recovery finds exactly one situation", len(found) == 1, json.dumps(found))
    rec = found[0]
    sc.check("it is identified as an expired lease", rec["kind"] == "expired_lease")
    sc.check("it names the agent that died", rec["holder"] == "delta")
    sc.check(
        "it MEASURED the tree: 1 uncommitted file, 1 unmerged commit",
        rec["dirty_files"] == 1 and rec["unmerged_commits"] == 1,
        json.dumps(rec),
    )
    sc.check("it is flagged as containing salvageable work", rec["salvageable"] is True)
    sc.check(
        "the advice tells the operator to inspect FIRST",
        "INSPECT FIRST" in rec["advice"],
        rec["advice"],
    )

    sc.step("The uncommitted work is STILL THERE — nothing was cleaned up")
    sc.check(
        "the finished-but-uncommitted file survived the crash",
        (wt / "feedparse/rss_dates.py").exists(),
    )
    sc.check(
        "the pre-crash commit survived too",
        sc.git("cat-file", "-e", committed + "^{commit}", repo=wt) == "",
    )

    sc.step("A new agent still cannot silently steal an expired lease")
    code, _, err = sc.ddflow("claim", "P1.T1", agent="epsilon", expect=3)
    sc.check("the claim is refused even though the lease expired", code == 3)
    sc.check(
        "the refusal explains WHY it will not auto-reclaim",
        "NOT stolen automatically" in err,
        err[:220],
    )
    sc.note(
        "An automatic reclaim here would be indistinguishable from correct "
        "behaviour right up until the first time it deleted a day's work."
    )

    sc.step("An automatic sweep also leaves salvageable work alone")
    sc.ddflow("recover", "--apply")
    st = sc.jddflow("show", "P1.T1")
    sc.check(
        "the claim is still in place after --apply, because work was found",
        st["lease"] is not None,
        json.dumps(st.get("lease")),
    )

    sc.step("The operator salvages, then releases — now the item returns to the pool")
    sc.commit_in(wt, "P1.T1: salvaged date normalisation")
    sc.ddflow("release", "P1.T1", "--note", "salvaged 1 file, 2 commits kept")
    sc.ddflow("claim", "P1.T1", agent="epsilon", expect=0)
    sc.check(
        "EPSILON adopted the EXISTING worktree rather than making a second one",
        Path(sc.jddflow("show", "P1.T1")["worktree"]) == wt,
    )
    sc.check(
        "and the salvaged work is in that worktree's history",
        "rss_dates" in sc.git("show", "--stat", "HEAD", repo=wt),
    )

    sc.step("Meanwhile an unrelated task was never affected")
    ready = [r["id"] for r in sc.jddflow("next", "--phase", "P1")["ready"]]
    sc.check("T2 stayed available throughout the whole incident", "P1.T2" in ready, str(ready))

    sc.step("The event log tells the full story of the incident")
    _, out, _ = sc.ddflow("doctor")
    sc.check(
        "doctor reports a healthy log after recovery",
        "Healthy" in out or "problem" not in out.lower(),
        out,
    )
