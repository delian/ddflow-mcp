#!/usr/bin/env sh
# Build and verify a release locally. DRY BY DEFAULT — it publishes nothing unless you
# pass `--publish`, and even then it refuses anything it has not first proved works.
#
#   scripts/release.sh                 # build wheel + image, verify both, push nothing
#   scripts/release.sh --publish       # ...then push to PyPI, Docker Hub, ghcr.io, MCP
#
# WHY THIS EXISTS ALONGSIDE .github/workflows/publish.yml. The workflow is the thing that
# actually releases, on a `v*` tag. But a tag is not reversible: PyPI refuses to re-upload
# a version, `:latest` is already on someone's disk by the time you notice, and a manifest
# in the MCP registry is what an IDE marketplace offers people. So the same checks have to
# be runnable BEFORE the tag exists, from a laptop, with no credentials — which is what
# this is. If it fails here, the tag was going to fail too, an hour later, in public.
#
# It deliberately duplicates the workflow's verification rather than sharing it. A shell
# script and a GitHub Actions YAML cannot share logic without a third file that neither
# obviously runs; two readable copies of six checks beat one clever indirection, and the
# version-agreement check below is the one that catches the drift between them.
set -eu

PUBLISH=0
for arg in "$@"; do
  case "$arg" in
    --publish) PUBLISH=1 ;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $arg (try --help)" >&2; exit 2 ;;
  esac
done

cd "$(git rev-parse --show-toplevel)"
say() { printf '\n== %s\n' "$1"; }
die() { printf '\nFAILED: %s\n' "$1" >&2; exit 1; }

IMAGE_DH="docker.io/delian/ddflow-mcp"
IMAGE_GH="ghcr.io/delian/ddflow-mcp"

# ---------------------------------------------------------------- 1. versions agree
say "versions"
VERSION=$(grep -m1 '^version = ' pyproject.toml | cut -d'"' -f2)
SRV=$(python3 -c 'import json;print(json.load(open("server.json"))["version"])')
[ -n "$VERSION" ] || die "no version in pyproject.toml"
[ "$VERSION" = "$SRV" ] || die "pyproject $VERSION != server.json $SRV"
# Every OCI identifier's TAG must be the version. One left at an old tag publishes a
# manifest that points at the previous image: discoverable, installable, wrong build.
python3 - "$VERSION" <<'PY' || exit 1
import json, sys
want = sys.argv[1]
bad = [p["identifier"] for p in json.load(open("server.json"))["packages"]
       if p.get("registryType") == "oci" and not p["identifier"].endswith(f":{want}")]
if bad:
    sys.exit(f"OCI identifiers not tagged {want}: {bad}")
PY
echo "  $VERSION — pyproject, server.json and every OCI tag agree"

# ---------------------------------------------------------------- 2. the suite
say "tests"
uv run --with pytest --with pytest-timeout python -m pytest tests/ -q --timeout=420 \
  || die "the suite is red; a release is not the time to find out"

# ---------------------------------------------------------------- 3. the wheel
say "wheel"
rm -rf dist
uv build >/dev/null || die "uv build failed"
ls dist/*.whl >/dev/null 2>&1 || die "no wheel produced"
# INSTALL it and run it. `uv build` succeeding proves the metadata parses, not that the
# console script resolves or that the package data (templates, prompts) actually shipped
# — and a wheel missing its templates fails at the first `ddflow help`, on the user's
# machine, not here.
say "wheel actually works"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
uv venv --quiet "$TMP/venv"
VENV_PY="$TMP/venv/bin/python"
uv pip install --quiet --python "$VENV_PY" dist/*.whl || die "the wheel will not install"
"$VENV_PY" -m ddflow --version >/dev/null || die "the installed package will not run"
"$VENV_PY" -c 'from ddflow.infra.paths import templates_dir
import sys
d = templates_dir()
missing = [n for n in ("companions.toml", "prompts") if not (d / n).exists()]
sys.exit(f"package data missing from the wheel: {missing}" if missing else 0)' \
  || die "the wheel is missing its shipped templates"
echo "  installs, runs, and carries its templates"

# ---------------------------------------------------------------- 4. the image
say "image"
command -v docker >/dev/null || die "docker not on PATH"
docker build --quiet -t "$IMAGE_GH:$VERSION" . >/dev/null || die "docker build failed"
SIZE=$(docker image inspect "$IMAGE_GH:$VERSION" --format '{{.Size}}')
echo "  built $IMAGE_GH:$VERSION ($((SIZE / 1024 / 1024)) MB)"

# A built image that cannot answer `initialize` is a broken release every marketplace
# will happily offer. This is the one check that separates "it compiled" from "it works".
say "image speaks MCP"
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' \
  | docker run -i --rm -v "$PWD:/repo" "$IMAGE_GH:$VERSION" 2>/dev/null \
  | grep -q '"serverInfo"' || die "the image does not answer initialize"
echo "  answers initialize over stdio"

if [ "$PUBLISH" -eq 0 ]; then
  cat <<EOF

Everything verified, nothing published.

  version   $VERSION
  wheel     $(ls dist/*.whl)
  image     $IMAGE_GH:$VERSION

To release, prefer the TAG — it runs these same checks in CI with OIDC credentials
and no stored tokens:

  git tag v$VERSION && git push origin v$VERSION

\`--publish\` from here is the escape hatch for when CI is unavailable. It needs you
already logged in to PyPI, Docker Hub and ghcr.io, and it pushes \`:latest\`, which
cannot be taken back.
EOF
  exit 0
fi

# ---------------------------------------------------------------- 5. publish
printf '\nThis PUBLISHES %s to PyPI, Docker Hub, ghcr.io and the MCP registry.\n' "$VERSION"
printf 'PyPI will not let you re-upload it and :latest moves for everyone. Type the version to confirm: '
read -r CONFIRM
[ "$CONFIRM" = "$VERSION" ] || die "not confirmed"

say "PyPI"
uv publish dist/* || die "PyPI upload failed"

say "registries"
docker tag "$IMAGE_GH:$VERSION" "$IMAGE_GH:latest"
docker tag "$IMAGE_GH:$VERSION" "$IMAGE_DH:$VERSION"
docker tag "$IMAGE_GH:$VERSION" "$IMAGE_DH:latest"
for ref in "$IMAGE_GH:$VERSION" "$IMAGE_GH:latest" "$IMAGE_DH:$VERSION" "$IMAGE_DH:latest"; do
  docker push "$ref" || die "push failed: $ref"
done

# LAST, because `mcp-publisher` validates that every package named in server.json
# exists. Publishing the manifest first advertises a version nobody can fetch.
say "MCP registry"
command -v mcp-publisher >/dev/null || die "mcp-publisher not on PATH — see .github/workflows/publish.yml"
mcp-publisher login github || die "mcp-publisher login failed"
mcp-publisher publish || die "mcp-publisher publish failed"

printf '\nPublished %s.\n' "$VERSION"
