"""ddflow's runtime dependencies are an explicit ALLOWLIST; refuse any other one.

It used to assert there were none at all; Jinja2 was then declared on purpose (48aae97:
the handshake rendered differently depending on whose interpreter happened to have it),
and the assertion was never updated -- unseen, because the build step before it failed on
every run. tomlkit followed (D-unify 1: comment-preserving config write-back). A new dependency is a supply-chain decision: add it to ALLOWED, deliberately.

Run by CI (quality job) and by the pre-push hook, so the two cannot drift.
"""

import re
import sys
import tomllib
from pathlib import Path

ALLOWED = {"jinja2", "tomlkit"}

pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
deps = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"].get("dependencies", [])
names = {re.split(r"[<>=!~;\[ ]", d, maxsplit=1)[0].strip().lower() for d in deps}
extra = sorted(names - ALLOWED)
sys.exit(f"runtime dependencies outside the allowlist: {extra}" if extra else 0)
