# ddflow as a container: for operators who have Docker and would rather not install a
# Python toolchain. Works identically on Linux, macOS and Windows.
#
#   docker run -i --rm -v "$PWD:/repo" ghcr.io/delian/ddflow-mcp
#
# Alpine because the result is an order of magnitude smaller than a Debian base, which
# matters when an MCP client pulls it on first use.
#
# ddflow has two runtime dependencies, Jinja2 and tomlkit (pure Python). Jinja2 pulls
# MarkupSafe, and MarkupSafe carries a C
# extension, so musl is no longer irrelevant here the way it was when this was pure
# standard library. It still needs no compiler: MarkupSafe publishes `musllinux_1_2`
# wheels for x86_64 and aarch64, which are exactly the two platforms the `docker` job
# builds. If that ever stops being true the build fails loudly at `pip install` rather
# than producing a broken image, and the fix is `apk add --virtual .build gcc musl-dev`
# around the install.
FROM python:3.13-alpine

# git is not optional: it IS half of what ddflow does. `su-exec` is a 10 KB setuid
# helper used by the entrypoint to drop to the mounted repository's owner; `tini` reaps
# the git subprocesses so a long-lived server does not accumulate zombies.
RUN apk add --no-cache git su-exec tini

# Installed as a package rather than copied as a source tree, so the container and the
# `uvx` path run byte-identical code and cannot drift.
COPY . /src
RUN pip install --no-cache-dir /src && rm -rf /src

COPY docker-entrypoint.sh /usr/local/bin/ddflow-entrypoint
RUN chmod +x /usr/local/bin/ddflow-entrypoint

WORKDIR /repo
ENV DDFLOW_REPO=/repo \
    DDFLOW_IN_CONTAINER=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Upgrade at start (decisions D-self-upgrade 5, D-upgrade-on-mcp-connect). When the image is
# NEWER than the project it serves (the project's stamp, log, index and instruction files), the
# server compares them before answering: it rebuilds the derived stores, applies the
# non-destructive categories (hooks, instructions) with a backup first, and proposes the rest
# to the operator in the handshake and the first brief. It never refuses to start, gives up
# after `[upgrade].start_timeout_s` seconds, and only reports on a read-only mount. An OLDER
# image than the project writes nothing (the skew guard). The switch for one run:
#   docker run -e DDFLOW_UPGRADE_ON_START=check|safe|off ...   (default: [upgrade].on_start = safe)
# `check` only proposes; `off` skips the whole thing.

# tini as PID 1; the entrypoint fixes identity and then execs the server.
ENTRYPOINT ["/sbin/tini", "--", "/usr/local/bin/ddflow-entrypoint"]
CMD ["ddflow-mcp"]

# `io.modelcontextprotocol.server.name` is how the MCP registry verifies that the server
# in server.json owns this image; without it `mcp-publisher publish` refuses the OCI entries.
LABEL io.modelcontextprotocol.server.name="io.github.delian/ddflow-mcp" \
      org.opencontainers.image.title="ddflow" \
      org.opencontainers.image.description="Work-queue kernel for AI coding agents (MCP server + CLI)" \
      org.opencontainers.image.source="https://github.com/delian/ddflow-mcp" \
      org.opencontainers.image.licenses="MIT"
