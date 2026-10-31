"""Companion tools — detect what is missing, say what it costs, register what can be.

ddflow imposes an order and demands evidence. It does not *perform* the judgement
inside most of its gates: `standards` wants an automated standards review, `research`
wants documentation it can check a claim against. (`rules` wants memory of the last
time somebody hit this, and ddflow serves that one itself -- see `BUILTIN_COVERAGE`.) A
project that installs ddflow and stops has the others wired to nothing — and because an agent gate passes on an assertion, that gap is invisible
exactly the way the whole design is meant to prevent.

So the gap is named. `ddflow companions` maps each gate to the servers that serve it,
probes which are actually on this machine, and reports the three states separately:
**installed and registered**, **installed but not registered** (one command away), and
**not installed** (with the command and the URL, never an automatic download).

This package is the former ``ddflow/services/companions.py``, split into submodules
that stay under 30k chars each (rule r-small-files): ``model`` (what a companion IS),
``probe`` (launch and speak MCP), ``match`` (a stored entry IS this companion) and
``registry`` (registrations, cache, ``scan``, ``register`` and the coverage views).
The public API is unchanged: every name the old module exported is re-exported here,
so ``from ddflow.services.companions import load`` and ``from ddflow.services import
companions as CO`` bind exactly what they bound before.

The re-export of the split names is LAZY (``__getattr__``): the package imports no
submodule until one name is asked for. That keeps the invariant a one-file module had
and a package must keep — monkeypatch (``monkeypatch.setattr(CO, name, ...)`` re-binds
the name on THIS module exactly as before, and the call sites that must see it bind it
late) and the layering ratchet (``test_no_mutually_importing_pair`` would read an eager
``from .model import ...`` here as a latent parent<->child cycle).
"""

from __future__ import annotations

import importlib
import shutil  # noqa: F401 -- re-exported; tests patch companions.shutil
import time  # noqa: F401 -- re-exported; tests patch companions.time
from typing import Any

from ...infra import proc as P  # noqa: F401 -- re-exported; tests patch companions.P
from ..mcpconfig import (  # noqa: F401 -- re-exported: their home was here
    SHAPE_CORDIS_PATCH,
    _declared,
    _launch_of,
    _load_toml,
    _serves,
    _toml_without,
    cordis_rows,
    cordis_servers,
)

#: Public (and patch-covered private) name -> the submodule that defines it.
_SUBMODULE_OF = {
    # model
    "ADVICE_WORDS": "model",
    "Companion": "model",
    "DETECT_TIMEOUT_S": "model",
    "KINDS": "model",
    "Status": "model",
    "_ANSI": "model",
    "_NPM_NOISE": "model",
    "_said": "model",
    "actionable": "model",
    "is_installed": "model",
    "load": "model",
    # probe
    "Verification": "probe",
    "VERIFY_TIMEOUT_S": "probe",
    "_ECHO_MARK": "probe",
    "_EXIT_GRACE_S": "probe",
    "_MAX_BUF": "probe",
    "_VERIFY_PROTOCOL": "probe",
    "_await_exit": "probe",
    "_describe_answer": "probe",
    "_exit_how": "probe",
    "_exit_info": "probe",
    "_keep": "probe",
    "_reply_to": "probe",
    "_stop": "probe",
    "verify": "probe",
    "verify_one": "probe",
    # match
    "NPM_LAUNCHERS": "match",
    "VALUE_FLAGS": "match",
    "_DOCKER_VALUE_FLAGS": "match",
    "_NPM_TAG": "match",
    "_in_order_with_flags": "match",
    "_norm_args": "match",
    "launches_as": "match",
    # registry
    "BUILTIN_COVERAGE": "registry",
    "BUILTIN_MEMORY": "registry",
    "_cache_path": "registry",
    "_launched_elsewhere": "registry",
    "_launched_name": "registry",
    "_read_cache": "registry",
    "_registered_name": "registry",
    "_servers_in": "registry",
    "_write_cache": "registry",
    "coverage_gates": "registry",
    "gate_coverage": "registry",
    "gate_gaps": "registry",
    "register": "registry",
    "registered_in": "registry",
    "registrations": "registry",
    "scan": "registry",
}

_MODULES: dict[str, Any] = {}


def __getattr__(name: str) -> Any:
    sub = _SUBMODULE_OF.get(name)
    if sub is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod = _MODULES.get(sub)
    if mod is None:
        mod = importlib.import_module(f"{__name__}.{sub}")
        _MODULES[sub] = mod
    return getattr(mod, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_SUBMODULE_OF))
