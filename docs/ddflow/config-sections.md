# Config sections

Every `[section]` of `ddflow config --explain` is a dataclass in `ddflow/config_sections/`. A
new section is **one new file** and edits no shared file: the registry discovers it by a sorted
scan of public module names (`config_sections/__init__.py` → `ddflow.adapters.ports.module_names`,
the one scanner), `@declare("<name>")` registers its dataclass (a duplicate section name raises),
and `ddflow.config` wires the `Config` field, the class re-export and the knob manifest from that
registry (`_docs.SECTION_CLASSES` → `_core._SectionFields` / `__getattr__` / `_docs.declared_tables`).

## New-section recipe

1. Add `ddflow/config_sections/<name>.py` with a dataclass:

   ```python
   from dataclasses import dataclass

   from ddflow.config_sections._docs import declare, knob

   @declare("<section-name>")
   @dataclass
   class <Name>Config:
       <knob>: int = knob(3, doc="what it controls")
   ```

   The module name carries the section; the class name (`<Name>Config`) is what `ddflow.config`
   re-exports. Nothing else to edit — `Config`'s field, the class re-export and
   `config --explain` pick the section up from the discovery.

2. The section name is unique: `@declare` raises if it is already registered. Knob keys
   (`<section>.<knob>`) are likewise unique across the whole config.

The discovery contract is pinned by `tests/test_config_section_discovery.py`: adding a section to
a package on the search path is discovered, registers, and leaves `config/__init__.py` and
`config/_core.py` byte-identical.
