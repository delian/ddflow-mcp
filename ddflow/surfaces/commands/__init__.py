"""Command families, split out of `cli.py`.

Each module owns one family and imports the shared kernel from `..context` — never
from `..cli`, which would recreate the module-level cycle `test_layering` forbids.
"""
