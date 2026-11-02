"""One Redactor, named profiles (D-unify 6, B-uni-textkit.4).

This package is the former ``ddflow/core/redact.py``, split into submodules that stay
under 30k chars each (rule r-small-files). The public API is unchanged: every name the
old module exported is re-exported here, so ``from ddflow.core.redact import Redactor``
and ``from ddflow.core import redact as R`` bind exactly what they bound before.

The re-export is LAZY (``__getattr__``): the package imports no submodule until one name
is asked for. That keeps two invariants a one-file module had and a package must keep —
monkeypatch (re-binds the name on THIS module exactly as before) and the layering ratchet
(``test_no_mutually_importing_pair`` would read an eager submodule import here as a
parent<->child cycle).
"""

from __future__ import annotations

from ddflow.core import _reexport

#: Public (and patch-covered private) name -> the submodule that defines it.
#: `pattern` owns the compiled patterns and address/host detectors; `profiles` owns the
#: profiles, `Redactor` and `redact_argv`; `log` owns the committed-log field tables and
#: the per-event redaction.
_SUBMODULE_OF = {
    # pattern
    "PUBLIC_NAMES": "pattern",
    "SUPERSEDED_MARKER": "pattern",
    "_EMAIL": "pattern",
    "_GENERIC_HOSTS": "pattern",
    "_HOME_PATH": "pattern",
    "_HOST": "pattern",
    "_IPV4": "pattern",
    "_IPV6": "pattern",
    "_MARKER": "pattern",
    "_MIN_TERM": "pattern",
    "_TILDE_PATH": "pattern",
    "_V4": "pattern",
    "_is_private": "pattern",
    "_loop": "pattern",
    "_parse": "pattern",
    "_this_network": "pattern",
    "private_addresses": "pattern",
    # engine
    "_KEY": "engine",
    "_Pass": "engine",
    "_SCHEME_AND_VALUE": "engine",
    "_SCHEMES": "engine",
    "_coerce": "engine",
    "_host_forms": "engine",
    "_marker": "engine",
    "_mask": "engine",
    "_secrets": "engine",
    "_term": "engine",
    "Redacted": "engine",
    "mask_secrets": "engine",
    "redact_text": "engine",
    # profiles
    "_PROGRAMS": "profiles",
    "_WORD": "profiles",
    "PROFILES": "profiles",
    "Profile": "profiles",
    "Redactor": "profiles",
    "redact_argv": "profiles",
    # log
    "LOG_NO_NETWORK_FIELDS": "log",
    "LOG_TEXT_FIELDS": "log",
    "LOG_TEXT_NAMES": "log",
    "LOG_VERBATIM_FIELDS": "log",
    "LOG_VERBATIM_NAMES": "log",
    "_map_leaves": "log",
    "network_leaves": "log",
    "redact_event_data": "log",
    "redact_leaves": "log",
    "redact_network": "log",
}

_reexport.reexport(globals(), _SUBMODULE_OF, __name__)
del _reexport
