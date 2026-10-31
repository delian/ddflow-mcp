"""The fake agent: replays a stream fixture's lines on stdout and exits with its exit code.

Run as ``python fake_agent.py <fixture.json>``; no network, no keys, no real agent."""

import json
import sys

fx = json.load(open(sys.argv[1]))
for line in fx["lines"]:
    print(line if isinstance(line, str) else json.dumps(line), flush=True)
sys.exit(int(fx.get("exit_code", 0)))
