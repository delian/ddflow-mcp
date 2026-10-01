"""The MCP-registry publish step survives a transient registry 504.

publish #38 (0.1.6) failed at `mcp-publisher publish` with "server returned status 504:
504 Gateway Time-out (nginx)" after ~60 s -- registry-side (modelcontextprotocol/registry
#1444: publish requests starve waiting for a DB connection) -- and the one-shot step
failed the release after PyPI and both images had shipped, skipping the `tag` job.

These tests run the step's own script, extracted from publish.yml, against a fake
`mcp-publisher`, a fake `curl` (the registry GET) and a fake `sleep` (which records the
backoff instead of waiting it), so the workflow and the test cannot drift apart.
"""

from __future__ import annotations

import json
import re
import shutil
import stat
import subprocess
import textwrap
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER = json.loads((ROOT / "server.json").read_text("utf-8"))
WORKFLOW = (ROOT / ".github" / "workflows" / "publish.yml").read_text("utf-8")
STEP = "Publish server.json"


def _job(name: str) -> str:
    m = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [a-z][\w-]*:\n|\Z)", WORKFLOW, re.M | re.S)
    assert m, f"no job {name!r} in publish.yml"
    return m.group(1)


def _step(job: str, name: str) -> str:
    """One step's text: from `- name: <name>` to the next step or the end of the job."""
    m = re.search(rf"^      - name: {re.escape(name)}\n(.*?)(?=^      - |\Z)", job, re.M | re.S)
    assert m, f"no step {name!r}"
    return m.group(1)


def _block(step: str, key: str) -> str:
    """A key's indented block (`run: |` body, or an `env:` mapping), dedented."""
    m = re.search(rf"^(        ){key}:[ |]*\n((?:\1  .*\n|\s*\n)*)", step, re.M)
    return textwrap.dedent(m.group(2)) if m else ""


def _env(step: str) -> dict[str, str]:
    out = {}
    for line in _block(step, "env").splitlines():
        k, sep, v = line.partition(":")
        if sep and k.strip() and not k.lstrip().startswith("#"):
            out[k.strip()] = v.split(" #")[0].strip().strip("\"'")
    return out


JOB = _job("mcp-registry")
PUBLISH = _step(JOB, STEP)
SCRIPT = _block(PUBLISH, "run")
ENV = _env(PUBLISH)

FAKE_PUBLISHER = r"""#!/usr/bin/env bash
# Answers `publish` from the space-separated script in $FAKE_PLAN, one word per call:
#   ok        published (the version becomes listed)
#   504       gateway timeout, nothing committed
#   504-late  gateway timeout, but the commit landed behind the gateway
#   dup       400 duplicate version (listing is whatever it already was)
#   dup-race  400 duplicate version: it was committed by an earlier call whose answer
#             was lost, and the GET before this call raced it (listed from now on)
#   bad       400 validation failure -- permanent
#   hang      the request is accepted and never answered
echo "$*" >> "$FAKE_DIR/publisher.log"
[ "$1" = login ] && exit 0
n=0; [ -e "$FAKE_DIR/publishes" ] && n=$(wc -l < "$FAKE_DIR/publishes"); echo x >> "$FAKE_DIR/publishes"
set -- $FAKE_PLAN; shift "$n" 2>/dev/null || set --; word=${1:-${FAKE_LAST:-504}}
case "$word" in
  ok)       touch "$FAKE_DIR/listed"; echo "Successfully published"; exit 0 ;;
  504-late) touch "$FAKE_DIR/listed"; echo "Error: publish failed: server returned status 504: <html>504 Gateway Time-out</html>" >&2; exit 1 ;;
  504)      echo "Error: publish failed: server returned status 504: <html>504 Gateway Time-out</html>" >&2; exit 1 ;;
  dup)      echo 'Error: publish failed: server returned status 400: {"detail":"Failed to publish server","errors":[{"message":"invalid version: cannot publish duplicate version: x already exists"}]}' >&2; exit 1 ;;
  dup-race) touch "$FAKE_DIR/listed"; echo 'Error: publish failed: server returned status 400: {"detail":"Failed to publish server","errors":[{"message":"invalid version: cannot publish duplicate version: x already exists"}]}' >&2; exit 1 ;;
  hang)     exec /bin/sleep 600 ;;
  bad)      echo 'Error: publish failed: server returned status 400: {"detail":"Failed to publish server","errors":[{"message":"description too long"}]}' >&2; exit 1 ;;
esac
"""

FAKE_CURL = r"""#!/usr/bin/env bash
# The registry GET: 200 once something marked the version listed, 404 before.
for a; do case "$a" in http*) echo "$a" >> "$FAKE_DIR/curl.log" ;; esac; done
if [ -e "$FAKE_DIR/listed" ]; then printf 200; else printf 404; fi
"""

FAKE_SLEEP = r"""#!/usr/bin/env bash
echo "$1" >> "$FAKE_DIR/sleeps"
"""


def _run(tmp_path: Path, plan: str, *, listed: bool = False, last: str = "504"):
    fake = tmp_path / "fake"
    fake.mkdir(parents=True)
    for name, body in (
        ("mcp-publisher", FAKE_PUBLISHER),
        ("curl", FAKE_CURL),
        ("sleep", FAKE_SLEEP),
    ):
        p = fake / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    if listed:
        (fake / "listed").touch()
    work = tmp_path / "work"
    work.mkdir(parents=True)
    shutil.copy(ROOT / "server.json", work / "server.json")
    script = work / "step.sh"
    script.write_text(SCRIPT)
    env = {
        "PATH": f"{fake}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "FAKE_DIR": str(fake),
        "FAKE_PLAN": plan,
        "FAKE_LAST": last,
        **ENV,
        "MCP_CALL_TIMEOUT": "1",  # the hang case; every other call returns at once
    }
    # GitHub runs `shell: bash` as `bash --noprofile --norc -eo pipefail {0}`.
    proc = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    def lines(name: str) -> list[str]:
        f = fake / name
        return f.read_text().split() if f.exists() else []

    log = (
        (fake / "publisher.log").read_text().splitlines()
        if (fake / "publisher.log").exists()
        else []
    )
    return proc, {
        "publishes": len(lines("publishes")),
        "logins": sum(1 for x in log if x.startswith("login")),
        "sleeps": [int(s) for s in lines("sleeps")],
        "gets": (fake / "curl.log").read_text().splitlines()
        if (fake / "curl.log").exists()
        else [],
        "out": proc.stdout + proc.stderr,
    }


def _delays() -> list[int]:
    raw = ENV.get("MCP_PUBLISH_DELAYS", "")
    assert raw, "the publish step declares its backoff in env MCP_PUBLISH_DELAYS"
    return [int(x) for x in raw.split()]


def test_the_step_runs_under_bash_with_a_bounded_job():
    assert re.search(r"^        shell: bash\s*$", PUBLISH, re.M), "the step pins `shell: bash`"
    m = re.search(r"^    timeout-minutes: (\d+)", JOB, re.M)
    assert m, "mcp-registry declares timeout-minutes"
    delays = _delays()
    assert len(delays) >= 3, "several attempts, not one"
    # Even the worst case -- every login and publish hanging to MCP_CALL_TIMEOUT -- ends
    # inside the job timeout, so the step's own ::error:: is what the operator sees.
    call = int(ENV.get("MCP_CALL_TIMEOUT", "0"))
    assert call > 0, "each mcp-publisher call is bounded"
    assert sum(delays) + 2 * call * (len(delays) + 1) + 120 <= int(m.group(1)) * 60


def test_a_504_then_success_publishes(tmp_path):
    proc, r = _run(tmp_path, "504 ok")
    assert proc.returncode == 0, r["out"]
    assert r["publishes"] == 2
    assert r["sleeps"] == _delays()[:1]


def test_every_attempt_logs_in_again(tmp_path):
    # Registry tokens last five minutes; the window is longer than that.
    proc, r = _run(tmp_path, "504 504 ok")
    assert proc.returncode == 0, r["out"]
    assert r["logins"] == r["publishes"] == 3


def test_a_504_that_committed_anyway_is_success_without_republishing(tmp_path):
    proc, r = _run(tmp_path, "504-late")
    assert proc.returncode == 0, r["out"]
    assert r["publishes"] == 1
    name = urllib.parse.quote(SERVER["name"], safe="")
    path = f"/v0.1/servers/{name}/versions/{SERVER['version']}"
    assert any(path in g for g in r["gets"]), r["gets"]


def test_an_already_listed_version_is_not_published_again(tmp_path):
    # A re-run after a 504 that hid a commit.
    proc, r = _run(tmp_path, "bad", listed=True)
    assert proc.returncode == 0, r["out"]
    assert r["publishes"] == 0


def test_duplicate_is_success_only_once_the_registry_confirms_it(tmp_path):
    # Not a permanent 400: retried, and the next GET finds it listed.
    proc, r = _run(tmp_path, "dup-race", listed=False)
    assert proc.returncode == 0, r["out"]
    assert r["publishes"] == 1 and r["sleeps"] == _delays()[:1]
    # A duplicate the GET never confirms is not success.
    proc, r = _run(tmp_path / "unconfirmed", "", last="dup")
    assert proc.returncode != 0, r["out"]
    assert r["publishes"] == len(_delays()) + 1


def test_a_call_that_hangs_is_cut_off_and_retried(tmp_path):
    # mcp-publisher's HTTP client has no timeout; a starved request can be held open.
    proc, r = _run(tmp_path, "hang ok")
    assert proc.returncode == 0, r["out"]
    assert r["publishes"] == 2


def test_a_permanent_rejection_fails_at_once(tmp_path):
    proc, r = _run(tmp_path, "bad")
    assert proc.returncode != 0
    assert r["publishes"] == 1 and r["sleeps"] == []
    assert "description too long" in r["out"]


def test_a_registry_that_keeps_504ing_fails_after_the_bounded_budget(tmp_path):
    proc, r = _run(tmp_path, "", last="504")
    assert proc.returncode != 0
    delays = _delays()
    assert r["publishes"] == len(delays) + 1
    assert r["sleeps"] == delays
    err = [ln for ln in r["out"].splitlines() if ln.startswith("::error::")]
    assert err, r["out"]
    assert "registry/issues/1444" in err[-1] and "Re-run failed jobs" in err[-1]


@pytest.mark.parametrize("plan", ["504 ok", "504-late"])
def test_success_never_prints_an_error_annotation(tmp_path, plan):
    proc, r = _run(tmp_path, plan)
    assert proc.returncode == 0
    assert "::error::" not in r["out"]
