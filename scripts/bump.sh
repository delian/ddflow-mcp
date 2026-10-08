#!/usr/bin/env sh
# Move the version in every place that declares it, in one step.
#
#   scripts/bump.sh patch     # 0.1.0 -> 0.1.1
#   scripts/bump.sh minor     # 0.1.0 -> 0.2.0
#   scripts/bump.sh major     # 0.1.0 -> 1.0.0
#   scripts/bump.sh 0.4.2     # ...or say it exactly
#
# WHY A SCRIPT. The version is DECLARED in one place, `__version__` in `ddflow/__init__.py`;
# everything else follows it: `pyproject.toml` reads it (hatch dynamic version, so uv.lock
# records no version and never goes stale on a bump), `SERVER_INFO` in `surfaces/mcp.py` is
# built from it, and `server.json` -- the `version`, the `pypi` package's `version` and the
# TAG inside every `oci` identifier (an `oci` package has NO `version` field; the registry
# rejects one, publish #40) -- is a GENERATED file, rendered from `server.template.json` by
# `scripts/render_server_json.py`. This script edits that one literal, renders server.json
# and re-reads the result. The OCI tag was the one that used to get left behind: a `:0.1.0`
# while everything else moved publishes a manifest pointing at the PREVIOUS image.
#
# CI BUMPS FOR YOU, BY IMPACT. `.github/workflows/publish.yml` bumps the version on every push
# to main that changes shipped code -- a patch, or a minor when the upgrade manifest declares a
# breaking change while ddflow is 0.x (`scripts/release_impact.py`) -- commits 'release X.Y.Z'
# to main, and publishes. MAJOR, AND ANY EXACT VERSION, ARE YOURS: run `major` or an exact
# version by hand, commit and push. publish.yml releases a declared version PyPI does not
# have yet AS IS, instead of bumping it again, and the automatic bumps continue from there.
#
# No re-lock: uv.lock records no version for this project.
set -eu

cd "$(git rev-parse --show-toplevel)"
CUR=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' ddflow/__init__.py | head -n 1)
[ -n "$CUR" ] || { echo "no __version__ in ddflow/__init__.py" >&2; exit 1; }

WHAT="${1:-}"
case "$WHAT" in
  patch|minor|major)
    # The arithmetic lives in scripts/release_impact.py, which CI uses for the same steps:
    # one definition, so a manual bump and an automatic one cannot number differently.
    NEW=$(python3 scripts/release_impact.py next "$CUR" "$WHAT") \
      || { echo "cannot bump $CUR: not three numeric parts. Pass an exact version." >&2; exit 1; }
    ;;
  '' | -h | --help)
    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
    printf '\ncurrent version: %s\n' "$CUR"
    exit 0 ;;
  *)
    # An exact version. Validated rather than trusted: a typo here becomes a git tag, a
    # PyPI release and an image tag, none of which can be withdrawn.
    echo "$WHAT" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.]+)?$' \
      || { echo "not a version: $WHAT" >&2; exit 2; }
    NEW="$WHAT" ;;
esac

[ "$NEW" != "$CUR" ] || { echo "already $CUR" >&2; exit 2; }

python3 - "$CUR" "$NEW" <<'PY'
import pathlib, re, subprocess, sys

cur, new = sys.argv[1], sys.argv[2]

# The ONE edit.
init = pathlib.Path("ddflow/__init__.py")
itext = init.read_text()
ipatched, n_init = re.subn(
    rf'^(__version__ = ")({re.escape(cur)})(")$', rf'\g<1>{new}\g<3>', itext, count=1, flags=re.M
)
if n_init != 1:
    sys.exit(f"ddflow/__init__.py: expected one __version__ {cur!r}, replaced {n_init}")
init.write_text(ipatched)

# Everything else is derived: render server.json from its template.
r = subprocess.run([sys.executable, "scripts/render_server_json.py"], capture_output=True, text=True)
if r.returncode != 0:
    sys.exit("server.json did not render: " + (r.stderr or r.stdout).strip())

# Re-read and assert, rather than trusting the writes above.
import json
import tomllib

got = json.loads(pathlib.Path("server.json").read_text())
stale = [p["identifier"] for p in got["packages"]
         if p.get("registryType") == "oci" and not p["identifier"].endswith(f":{new}")]
stale += [f"{p['identifier']} has a version field" for p in got["packages"]
          if p.get("registryType") == "oci" and "version" in p]
stale += [f"{p['identifier']} is at {p['version']}" for p in got["packages"]
          if p.get("registryType") != "oci" and "version" in p and p["version"] != new]
problems = []
sys.path.insert(0, ".")
# Bytecode from an EMPTY cache, i.e. compiled from the files just written. The tree's own
# __pycache__ is trusted whenever source size and whole-second mtime match, and a patch
# bump keeps the size: two bumps in one second read the previous version's bytecode and
# reported every correct write as "did not take" (Bf898a7f3b8).
import atexit
import shutil
import tempfile

sys.pycache_prefix = tempfile.mkdtemp(prefix="bump-pycache-")
atexit.register(shutil.rmtree, sys.pycache_prefix, True)
import importlib

import ddflow as _pkg

importlib.reload(_pkg)
import ddflow.surfaces.mcp as _mcp

importlib.reload(_mcp)
if _pkg.__version__ != new:
    problems.append(f"ddflow.__version__ is {_pkg.__version__}")
if _mcp.SERVER_INFO["version"] != new:
    problems.append(f"SERVER_INFO is {_mcp.SERVER_INFO['version']}")
if "version" in tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]:
    problems.append("pyproject.toml declares a version of its own")
if got["version"] != new:
    problems.append(f"server.json is {got['version']}")
if stale:
    problems.append(f"stale package versions: {stale}")
if problems:
    sys.exit("bump did not take: " + "; ".join(problems))
print(f"{cur} -> {new}  (ddflow/__init__.py, server.json + {len(got['packages'])} packages rendered)")
PY

cat <<EOF

Next:

  scripts/release.sh              # build and verify everything locally, publish nothing
  git commit -am 'release $NEW'
  git push origin main            # CI publishes $NEW as declared (it bumps only a
                                  # version PyPI already has), then tags v$NEW

Nothing is published until that push, and nothing is tagged until it worked.
EOF
