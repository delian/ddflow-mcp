"""A fake coding agent: replays a recording of stdout lines, then exits with a recorded code.

usage: fake_agent.py RECORDING [--exit N] [--stderr TEXT] [--hang] [--child PIDFILE]
         [--echo-env NAME] [--read-stdin] [--pwd-home]
Lines of RECORDING go to stdout as they are; ``--hang`` then sleeps until killed;
``--child`` first starts a grandchild that records its pid (to test group cleanup).
"""

import json
import os
import subprocess
import sys
import time

a = sys.argv[1:]
rec = a.pop(0)


def opt(name, default=None):
    if name in a:
        i = a.index(name)
        v = a[i + 1]
        del a[i : i + 2]
        return v
    return default


code = int(opt("--exit", "0"))
err = opt("--stderr", "")
child = opt("--child")
echo = opt("--echo-env")
if child:
    c = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(300)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    open(child + ".tmp", "w").write(str(c.pid))
    os.rename(child + ".tmp", child)  # atomic: a reader never sees half a pid
if "--read-stdin" in a:
    print(json.dumps({"type": "stdin", "text": sys.stdin.read().strip()}), flush=True)
if echo:
    print(json.dumps({"type": "env", "value": os.environ.get(echo, "<unset>")}), flush=True)
if "--pwd-home" in a:
    print(json.dumps({"type": "home", "home": os.environ.get("HOME")}), flush=True)
for line in open(rec):
    sys.stdout.write(line)
    sys.stdout.flush()
if err:
    sys.stderr.write(err)
    sys.stderr.flush()
if "--hang" in a:
    time.sleep(300)
sys.exit(code)
