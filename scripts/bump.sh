#!/usr/bin/env sh
# Move the version in every place that declares it, in one step.
#
#   scripts/bump.sh patch     # 0.1.0 -> 0.1.1
#   scripts/bump.sh minor     # 0.1.0 -> 0.2.0
#   scripts/bump.sh major     # 0.1.0 -> 1.0.0
#   scripts/bump.sh 0.4.2     # ...or say it exactly
#
# WHY A SCRIPT. The version lives in FIVE places that must agree: `pyproject.toml`,
# `server.json`'s `version`, its per-package `version`, the TAG inside every `oci`
# identifier, and `SERVER_INFO` in `surfaces/mcp.py` — which is what the server tells
# every client it is. Most are easy to forget, and the one that hurts is the OCI tag: a
# `:0.1.0` left behind while everything else moved publishes a registry manifest pointing
# at the PREVIOUS image — discoverable in an IDE marketplace, installable, and the wrong
# build. `tests/test_packaging.py` catches the drift, but only after you have made it.
#
# The bump is also the RELEASE DECISION. `.github/workflows/publish.yml` publishes on a
# push to main exactly when this number changes, so this is the one deliberate step in an
# otherwise automatic pipeline. That is on purpose: PyPI refuses to re-upload a version,
# so a release per commit is a release you cannot take back for a README typo.
set -eu

cd "$(git rev-parse --show-toplevel)"
CUR=$(grep -m1 '^version = ' pyproject.toml | cut -d'"' -f2)
[ -n "$CUR" ] || { echo "no version in pyproject.toml" >&2; exit 1; }

WHAT="${1:-}"
case "$WHAT" in
  patch|minor|major)
    NEW=$(python3 - "$CUR" "$WHAT" <<'PY'
import sys
cur, part = sys.argv[1], sys.argv[2]
bits = cur.split(".")
if len(bits) != 3 or not all(b.isdigit() for b in bits):
    sys.exit(f"cannot bump {cur!r}: not three numeric parts. Pass an exact version.")
major, minor, patch = (int(b) for b in bits)
if part == "major":
    major, minor, patch = major + 1, 0, 0
elif part == "minor":
    minor, patch = minor + 1, 0
else:
    patch += 1
print(f"{major}.{minor}.{patch}")
PY
    ) ;;
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
import json, pathlib, re, sys

cur, new = sys.argv[1], sys.argv[2]

proj = pathlib.Path("pyproject.toml")
text = proj.read_text()
patched, n = re.subn(rf'^version = "{re.escape(cur)}"$', f'version = "{new}"', text, count=1, flags=re.M)
if n != 1:
    sys.exit(f"pyproject.toml: expected one `version = \"{cur}\"`, replaced {n}")
proj.write_text(patched)

srv = pathlib.Path("server.json")
d = json.loads(srv.read_text())
d["version"] = new
for pkg in d["packages"]:
    if "version" in pkg:
        pkg["version"] = new
    # The OCI tag. This is the one that gets left behind.
    if pkg.get("registryType") == "oci":
        ident = pkg["identifier"]
        pkg["identifier"] = f"{ident.rsplit(':', 1)[0]}:{new}"
srv.write_text(json.dumps(d, indent=2) + "\n")

# The FIFTH place: what the server reports at handshake. A client that installed 0.2.0
# and is told 0.1.0 has no way to tell which is wrong, and `test_the_declared_versions_
# agree` — which caught this script missing it on its first run — treats the three as one
# invariant for exactly that reason.
mcp = pathlib.Path("ddflow/surfaces/mcp.py")
mtext = mcp.read_text()
mpatched, mn = re.subn(rf'("version": ")({re.escape(cur)})(")', rf'\g<1>{new}\g<3>', mtext, count=1)
if mn != 1:
    sys.exit(f"surfaces/mcp.py: expected one SERVER_INFO version {cur!r}, replaced {mn}")
mcp.write_text(mpatched)

# Re-read and assert, rather than trusting the writes above. Five places is exactly the
# number where "I updated them all" stops being checkable by eye.
import tomllib

got_proj = tomllib.loads(proj.read_text())["project"]["version"]
got = json.loads(srv.read_text())
stale = [p["identifier"] for p in got["packages"]
         if p.get("registryType") == "oci" and not p["identifier"].endswith(f":{new}")]
problems = []
sys.path.insert(0, ".")
import importlib

import ddflow.surfaces.mcp as _mcp

importlib.reload(_mcp)
if _mcp.SERVER_INFO["version"] != new:
    problems.append(f"SERVER_INFO is {_mcp.SERVER_INFO['version']}")
if got_proj != new:
    problems.append(f"pyproject is {got_proj}")
if got["version"] != new:
    problems.append(f"server.json is {got['version']}")
if stale:
    problems.append(f"stale OCI tags: {stale}")
if problems:
    sys.exit("bump did not take: " + "; ".join(problems))
print(f"{cur} -> {new}  (pyproject.toml, server.json + {len(got['packages'])} packages, SERVER_INFO)")
PY

cat <<EOF

Next:

  scripts/release.sh              # build and verify everything locally, publish nothing
  git commit -am 'release $NEW'
  git push origin main            # CI publishes PyPI + Docker Hub + ghcr + MCP registry,
                                  # then tags v$NEW once all of them succeeded

Nothing is published until that push, and nothing is tagged until it worked.
EOF
