# Orchard as a container: for operators who have Docker and would rather not install a
# Python toolchain. Works identically on Linux, macOS and Windows.
#
#   docker run -i --rm -v "$PWD:/repo" ghcr.io/OWNER/orchard
#
# Alpine because Orchard is pure standard library — there is no compiled dependency to
# worry musl about — and the result is an order of magnitude smaller than a Debian base,
# which matters when an MCP client pulls it on first use.
FROM python:3.13-alpine

# git is not optional: it IS half of what Orchard does. `su-exec` is a 10 KB setuid
# helper used by the entrypoint to drop to the mounted repository's owner; `tini` reaps
# the git subprocesses so a long-lived server does not accumulate zombies.
RUN apk add --no-cache git su-exec tini

# Installed as a package rather than copied as a source tree, so the container and the
# `uvx` path run byte-identical code and cannot drift.
COPY . /src
RUN pip install --no-cache-dir /src && rm -rf /src

COPY docker-entrypoint.sh /usr/local/bin/orchard-entrypoint
RUN chmod +x /usr/local/bin/orchard-entrypoint

WORKDIR /repo
ENV ORCHARD_REPO=/repo \
    ORCHARD_IN_CONTAINER=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# tini as PID 1; the entrypoint fixes identity and then execs the server.
ENTRYPOINT ["/sbin/tini", "--", "/usr/local/bin/orchard-entrypoint"]
CMD ["orchard-mcp"]

LABEL org.opencontainers.image.title="Orchard" \
      org.opencontainers.image.description="Work-queue kernel for AI coding agents (MCP server + CLI)" \
      org.opencontainers.image.source="https://github.com/OWNER/orchard" \
      org.opencontainers.image.licenses="MIT"
