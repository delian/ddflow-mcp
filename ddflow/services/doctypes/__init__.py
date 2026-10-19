"""Document types as data: `spec` parses and resolves the TOML descriptors; `sources` fills sections;
`validate` and the `rule_*` files check a document against its type."""

from . import sources  # noqa: F401 -- installs the source check into spec
