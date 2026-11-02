"""Adapter ports: the seams ddflow owns, declared in ddflow/adapters.

A **port** is a `Protocol` (structural, no base class to inherit, no import the other way);
its **default adapter** is the one implementation in its home under `ddflow/adapters/`, and
its **fake** is the shipped stand-in in `ddflow/adapters/fakes/`. The pattern every family
follows is documented in `docs/ARCHITECTURE.md` under "Adapter model"; this file is the code
half of that one document.

Where things live (D-adapters-answers, D-adapters-answers-2):

* **Ports** -- here, in `ddflow.adapters`, the BOTTOM of the layer contract: every layer
  may import `ddflow.adapters`, it imports no other ddflow layer. Pure: only `typing` and
  the standard library. As a home moves under `ddflow/adapters/<family>/` (ADP-migrate-homes)
  its Protocol moves next to the adapter and this module re-exports it.
* **Default adapters** -- `ddflow/adapters/<family>/`. The homes being moved in (`proc`,
  `git`, `fsio`, `tomlcfg`, `digest`) keep a thin re-export at their old import path until
  their import-linter baseline is empty, then the shim is deleted.
* **Fakes** -- `ddflow/adapters/fakes/`, one `Fake<Port>` per port, registered in `FAKES`
  under the port's name. A ratchet (tests/test_ports_contract.py) requires every port to
  have one; the list of ports still without one only shrinks.
* **The registry** -- `ddflow.adapters.registry.Registry[T]`, the one implementation
  (`ddflow.core.registry` re-exports it). A family with several adapters selects one
  through it.

Naming:

* a port is the family's noun, no suffix: `Proc`, `Git`, `Fsio`, `Toml`, `Digest`, `Forge`
  (so it reads as a type in a signature);
* a single-adapter family has exactly ONE implementation in its home module -- a second one
  is a bug, not a registerable alternative;
* a family with SEVERAL adapters registers them in a `Registry[T]` keyed by the adapter's
  `name` -- `forge` (GitHub, GitLab) is the worked example; notify, lane transport, search,
  optimizer and scheduler follow it.

A port's methods mirror the module the default adapter is extracted from, so a fake can be
dropped in where the module is imported (`from ddflow.infra import proc as P`, then a fake
`P`). Result types the adapter owns (`Captured`, `GitResult`, `PRInfo`, ...) are typed `Any`
here rather than re-imported: the port stays pure, and the type stays in the adapter's own
module where it is not a second definition.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Proc(Protocol):
    """Run subprocesses with stdin detached from the MCP stdio session.

    Home: `ddflow.adapters.proc` (compat re-export `ddflow.infra.proc`). Single adapter; a
    second process runner is a bug.
    """

    def run(self, *args: Any, **kwargs: Any) -> Any:
        """`subprocess.run` with `stdin=DEVNULL` by default; the stdlib result."""

    def capture(self, argv: list[str], *, timeout: float | None, **kwargs: Any) -> Any:
        """Run `argv` captured as text, never raising for the command's own trouble; the
        `Captured` value's `error` carries it."""

    def spawn_shell(self, line: str, **kwargs: Any) -> Any:
        """One shell line; a `ShellResult` whose `ok`/`finished` never read a missing
        binary as a failed command."""


@runtime_checkable
class Git(Protocol):
    """Every git call, as a value rather than an exception where the exit code is the answer.

    Home: `ddflow.adapters.git` (compat re-export `ddflow.infra.git`). Single adapter.
    """

    def run(self, repo: Path | str, *args: str, **kwargs: Any) -> Any:
        """`git -C repo *args`; a `GitResult` (`ok`, `unavailable`, `timed_out`)."""

    def paths(self, repo: Path | str, *args: str, **kwargs: Any) -> list[str] | None:
        """NUL-separated names as the filesystem spells them; None when git could not tell."""

    def unmerged(self, repo: Path | str, timeout: float | None = None) -> list[str] | None:
        """The unmerged paths of an in-progress merge; None when git could not answer."""


@runtime_checkable
class Fsio(Protocol):
    """Atomic writes, temporary files and locks in one home.

    Home: `ddflow.adapters.fsio` (compat re-export `ddflow.infra.fsio`). Single adapter.
    """

    def atomic_write(self, path: Path | str, text: str, *, fsync: bool = True) -> None:
        """Write `text` all-or-nothing, beside the target then renamed over it."""

    def replace_text(self, path: Path | str, text: str, *, fsync: bool = True) -> None:
        """Rewrite `path` in place only when its content actually changed."""

    def file_lock(self, path: Path | str, *, timeout: float | None = None) -> Iterator[Any]:
        """An exclusive lock for `path`, released on exit; raises `LockTimeout`."""

    def scratch_dir(self) -> Any:
        """A temporary directory that removes itself on exit."""

    def temp_text(self, text: str, suffix: str = "") -> Iterator[Path]:
        """`text` in a temporary file, removed on exit."""


@runtime_checkable
class Toml(Protocol):
    """The one TOML overlay loader and the one write-back that keeps comments and layout.

    Home: `ddflow.adapters.tomlcfg` (compat re-export `ddflow.infra.tomlcfg`). Single
    adapter; the `tomlkit` extra is imported here alone.
    """

    def overlay_table(self, *args: Any, **kwargs: Any) -> Any:
        """A TOML table overlaid from config path, `.ddflow/`, then shipped defaults."""

    def upsert(self, text: str, dotted: str, literal_text: str) -> str:
        """Set `dotted` to `literal_text` in `text`, preserving comments and layout."""

    def remove(self, text: str, dotted: str) -> Any:
        """Drop `dotted` from `text`; ``(new_text, changed)``."""

    def move_keys(self, *args: Any, **kwargs: Any) -> Any:
        """Move keys within `text`; ``(new_text, changed)``."""


@runtime_checkable
class Digest(Protocol):
    """The one hashing and content-digest family, stdlib `hashlib` only.

    Home: `ddflow.adapters.digest` (compat re-export `ddflow.core.digest`). Single adapter;
    `hashlib` is imported here alone, and the architecture guards only let the count go down.
    """

    def content_digest(
        self,
        data: str | bytes | memoryview,
        algo: str = "sha256",
        *,
        length: int | None = None,
        errors: str = "strict",
        size: int | None = None,
    ) -> str:
        """The hex digest of `data`, cut to `length` characters when given."""

    def canonical(self, obj: Any) -> str:
        """Stable JSON (sorted keys, no spaces): the input to every value digest."""

    def of_obj(
        self, obj: Any, algo: str = "blake2b", *, size: int | None = 12, length: int | None = None
    ) -> str:
        """The digest of `obj`'s `canonical` form."""


class Forge(Protocol):
    """A forge reached through its CLI, holding no token and speaking no HTTP.

    The worked example of a multi-adapter family: `GitHub` and `GitLab` are its two
    adapters, each with a `name`, registered in `ddflow.adapters.forge`'s
    ``Registry[Forge]`` (keyed by `name`) — the pattern `ci`, `notify` and `supervisors`
    follow. `name` is data, so this port is not `runtime_checkable`: a runtime `isinstance`
    check refuses a data member; the registry reads it.

    Home: `ddflow.adapters.forge` (compat re-export `ddflow.infra.forge`).
    """

    name: str

    def find(self, head: str) -> Any:
        """The request for `head`, or None; a `PRInfo`."""

    def create(
        self,
        *,
        head: str,
        base: str,
        title: str,
        body: str,
        draft: bool,
        labels: list[str],
        reviewers: list[str],
    ) -> Any:
        """Open the request; a `PRInfo`."""

    def view(self, number: int) -> Any:
        """The request's current state; a `PRInfo`."""

    def merge(self, number: int, *, strategy: str, head_sha: str, auto: bool) -> str:
        """Merge (or enqueue) the request; the forge's answer."""

    def set_base(self, number: int, base: str) -> None:
        """Change the request's base branch."""

    def threads(self, number: int) -> list[Any]:
        """The review threads; each a `Thread`."""

    def reply(self, number: int, thread_id: str, body: str) -> None:
        """Reply to a review thread."""

    def resolve(self, number: int, thread_id: str) -> None:
        """Mark a review thread resolved."""


@runtime_checkable
class HttpClient(Protocol):
    """One HTTP exchange, the way every caller makes it.

    Home: `ddflow.adapters.http` (urllib.request / http.client; the one module allowed to
    import them). Single adapter; the fake is `FakeHttpClient`. The adapter never decides
    consent or reads configuration: the caller checks both and passes its own timeout.
    """

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float,
        max_bytes: int | None = None,
        raise_for_status: bool = True,
        on_connect: Callable[[Any], None] | None = None,
    ) -> Any:
        """Send the request; an `HttpResponse` (status, headers, body bytes). Raises only
        `HttpError` subclasses: `HttpTimeout`, `HttpConnectionError` (`HttpTlsError`,
        `HttpSchemeError`), `HttpProtocolError` and, unless `raise_for_status` is false,
        `HttpStatusError`. `on_connect` receives an object with `abort()` once connected."""
