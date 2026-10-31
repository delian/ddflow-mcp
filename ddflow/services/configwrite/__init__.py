"""Writing `.ddflow/config.toml`: validate first, then replace in place.

Lifted out of `surfaces/commands/config.py`, which is where it was written and the wrong
layer for it. Nothing here presents anything — it parses TOML, decides whether a change
is *allowed*, and writes it atomically. That is domain policy, and keeping it in a
surface module meant the `api` layer could not perform a config change without reaching
up through a presentation layer, which is the exact shape B37 exists to remove.

Two of the rules are load-bearing and neither is obvious:

* **A pipeline may not name an undefined gate.** An unknown id is permanent silent
  damage: every item entering that pipeline blocks forever and `gate record` refuses the
  id. `workflow_problems` checks it BEFORE the write.
* **`gate.<id>.human` may not be set through here at all.** A human gate exists to make
  a person sign something; a tool that can flip it to `false` and then record the gate
  itself makes the signature worthless. `_guarded_human_gates` refuses the key rather
  than trusting the caller — the claim that this was "impossible through the MCP
  surface" was checked and turned out to be false.

* **A reviewer is guarded too** (decision D-reviewer-trust). Every write here goes
  through `_write_reviewed`: an agent's `kind = "command"` reviewer is undone and
  refused, and a reviewer whose identity a tool wrote is recorded so its reviews count
  only after a person approves it (`services/reviewer_trust.py`).

The edit goes through tomlkit (`tomlcfg.upsert`), not through `tomllib` and a serialiser:
the file is written by hand and carries comments explaining every knob, `tomllib` cannot
write, and a plain dump of its data would silently delete the documentation that makes the
file editable at all. tomlkit parses the text with its comments, blank lines and key order,
changes the one value, and writes the rest back as it found it.

This package is the former ``ddflow/services/configwrite.py``, split into submodules that
stay under 30k chars each (rule r-small-files). The public API is unchanged: every name
the old module exported is re-exported here through a LAZY ``__getattr__``, so monkeypatch
(``monkeypatch.setattr(CW, name, ...)``) and the layering ratchet behave exactly as they
did for the one-file module.
"""

from __future__ import annotations

from ddflow.core import _reexport

#: Public (and patch-covered private) name -> the submodule that defines it.
_SUBMODULE_OF = {
    # layers
    "LOCAL_DIR": "layers",
    "MACHINE_LOCAL_KEYS": "layers",
    "machine_local_layer": "layers",
    "machine_local_tables": "layers",
    "config_file": "layers",
    "ensure_local_dir": "layers",
    # _keys
    "_GATE_KEY_PARTS": "_keys",
    "_key_parts": "_keys",
    "KeyRefused": "_keys",
    "_BARE": "_keys",
    "plain_key_problem": "_keys",
    "_bare_path": "_keys",
    "_plain_spelling": "_keys",
    "_gate_key_problem": "_keys",
    "gate_id_problem": "_keys",
    "bare_id_problem": "_keys",
    "_TOKEN": "_keys",
    "_with_header_spelling": "_keys",
    "appended_key_problem": "_keys",
    # _problems
    "_human_cleared": "_problems",
    "_effective": "_problems",
    "_range_problems": "_problems",
    "_new_range_problems": "_problems",
    "_range_refusal": "_problems",
    "_workflow_problems": "_problems",
    "_parsed": "_problems",
    "_gate_table_problems": "_problems",
    "_render_gate_problem": "_problems",
    "_guarded_human_gates": "_problems",
    "_written_keys": "_problems",
    "_appended_keys": "_problems",
    "format_problem": "_problems",
    # _edits
    "Spelled": "_edits",
    "_literal": "_edits",
    "SetPairs": "_edits",
    "AppendText": "_edits",
    "Block": "_edits",
    "Edit": "_edits",
    "Guards": "_edits",
    "DEFAULT_GUARDS": "_edits",
    "EditResult": "_edits",
    "ReviewerRefusal": "_edits",
    "_apply_edits": "_edits",
    "_check_reviewer_fields": "_edits",
    "_compose": "_edits",
    # _write
    "_key_refusal": "_write",
    "_human_refusal": "_write",
    "_text_refusal": "_write",
    "_schema_problem": "_write",
    "_human_removed": "_write",
    "_gate_problem": "_write",
    "_workflow_refusal": "_write",
    "_result_problem": "_write",
    "apply_edit": "_write",
}

_reexport.reexport(globals(), _SUBMODULE_OF, __name__)
