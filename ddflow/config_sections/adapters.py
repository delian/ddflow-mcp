"""The `[adapters]` section: which adapter serves each multi-adapter port, and the consent
a caller needs before it may reach outside this clone or write a shared file.

The port SELECTORS with a dedicated section keep their own knob -- `[flow].forge`,
`[schedule].optimizer`, `[notify].channel` -- so a port's choice is next to its reader, not
aggregated here (r-adapters: one implementation per function, one knob per choice). This
section holds the ports whose choice has no such home (`platform`, `lane_transport`) and the
consent knobs of D-adapters-answers-2 / D-adapters-answers-3: network reach, once per kind,
and the shared managed files ddflow merges, once per file kind. The bottom `ddflow.adapters`
layer cannot read config (r-core-pure), so the code that reads these knobs -- and does the
selection / consent check -- is `ddflow.services.adapters`, one layer up.

Re-exported from `ddflow.config`, which assembles `Config` from every section.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ._docs import declare, knob

#: The network reaches a `[adapters]` adapter may make, consent once per kind
#: (D-adapters-answers-3): opening a pull request (`forge`), a chat/webhook message, an
#: email. A kind with no recorded consent must read as declined, never as granted.
NETWORK_KINDS = ("forge", "chat", "webhook", "email")


@declare("adapters")
@dataclass
class AdaptersConfig:
    """`[adapters]`: per-port adapter selection and the consent of `D-adapters-answers-2/3`."""

    platform: str = knob(
        "auto",
        doc="Which `Platform` adapter serves the OS-dependent verbs (file locks, process groups, 0600 permissions): 'auto' (default) asks this host; 'posix' forces the Posix adapter. Windows is documented unsupported and its adapter reports unavailable; it is not a choice here (D-adapters-answers-3).",
        choices=("auto", "posix"),
        strictest=("posix", "only the POSIX code path, never a guessed platform"),
    )
    lane_transport: str = knob(
        "local",
        doc="Which `Transport` adapter runs a lane's process and brings its files back: 'local' spawns the process on this host. A remote executor registers into the same `TRANSPORTS` registry; until one does, 'local' is the only choice.",
        choices=("local",),
        strictest=("local", "stays on this host, never a remote executor"),
    )
    network_consent: Mapping[str, bool] = knob(
        factory=dict,
        doc="Consent for an adapter to reach the network, once per kind (D-adapters-answers-3): a table `[adapters.network_consent]` whose keys are kinds (`forge`, `chat`, `webhook`, `email`) and whose true value records that this clone may make that reach. A kind with no recorded consent is declined, never granted (r-consent-outward); an org bundle layer can pre-approve or forbid a kind and lock it.",
    )
    managed_files_consent: Mapping[str, bool] = knob(
        factory=dict,
        doc="Consent for the `ManagedFile` adapter to merge one shared file kind (`settings.json`, `.mcp.json`, `opencode.json`, `hooks.toml`, the AGENTS.md/README regions), once per kind (D-adapters-answers-2): a table `[adapters.managed_files]`-style map keyed by file kind whose true value records consent. A kind with no recorded consent is refused, never silently written.",
    )
    managed_files_backup: bool = knob(
        True,
        doc="Whether the `ManagedFile` adapter writes a one-time `.ddflow-bak` beside a shared file before its first write (D-adapters-answers-2). True by default; it is an operator decision to turn off, so an org layer locking it cannot be loosened by a project layer (a value set higher is never loosened lower).",
    )
