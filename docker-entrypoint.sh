#!/bin/sh
# Reconcile the container with the host repository before handing over.
#
# Four things go wrong when a containerised tool touches a bind-mounted git repo, and
# all four are silent until they are expensive. Each is handled here, once.
set -e

REPO="${ORCHARD_REPO:-/repo}"

# (1) The repo must actually be mounted. Without this check the server starts, reports
#     an empty queue, and the operator concludes Orchard lost their work.
if [ ! -d "$REPO" ]; then
  echo "orchard: $REPO is not mounted. Run with: -v \"\$PWD:$REPO\"" >&2
  exit 2
fi

# (2) File ownership. On a Linux bind mount, files a root container creates are
#     root-owned on the host — the operator then cannot edit their own repository
#     without sudo. Drop to whoever owns the mount. On macOS and Windows the Desktop
#     VM already maps ownership to the user, so this is a no-op there.
OWNER_UID=$(stat -c '%u' "$REPO" 2>/dev/null || echo 0)
OWNER_GID=$(stat -c '%g' "$REPO" 2>/dev/null || echo 0)

# (3) git refuses to operate on a repository owned by a different uid ("detected
#     dubious ownership"). The container is ephemeral and the mount is explicitly
#     provided by the operator, so trusting it is correct here — and without it every
#     git call fails with an error that reads like a permissions bug in Orchard.
git config --global --add safe.directory "$REPO" 2>/dev/null || true
git config --global --add safe.directory '*' 2>/dev/null || true

# (4) git identity. A container has no ~/.gitconfig, so `git commit` fails with
#     "Please tell me who you are" — surfacing to the agent as an unexplained merge
#     failure. Prefer values the operator passed in; otherwise fall back to the repo's
#     own config; otherwise a clearly-marked placeholder that is obvious in a log.
if [ -z "$(git config --global user.email 2>/dev/null)" ]; then
  EMAIL="${GIT_AUTHOR_EMAIL:-$(git -C "$REPO" config user.email 2>/dev/null || true)}"
  NAME="${GIT_AUTHOR_NAME:-$(git -C "$REPO" config user.name 2>/dev/null || true)}"
  git config --global user.email "${EMAIL:-orchard@container.invalid}"
  git config --global user.name  "${NAME:-Orchard (container)}"
fi

if [ "$OWNER_UID" != "0" ] && [ "$(id -u)" = "0" ]; then
  # Make the global git config we just wrote readable by the user we drop to.
  cp -f /root/.gitconfig "/tmp/.gitconfig-$OWNER_UID" 2>/dev/null || true
  chown "$OWNER_UID:$OWNER_GID" "/tmp/.gitconfig-$OWNER_UID" 2>/dev/null || true
  export HOME=/tmp
  mv -f "/tmp/.gitconfig-$OWNER_UID" /tmp/.gitconfig 2>/dev/null || true
  exec su-exec "$OWNER_UID:$OWNER_GID" "$@"
fi
exec "$@"
