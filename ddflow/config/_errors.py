"""Configuration error types."""

from __future__ import annotations


class InvalidValue(ValueError):
    """A KNOWN knob given a value it cannot take. The write paths refuse it (exit 3,
    naming the key); an unknown key or section stays a plain ValueError (exit 1)."""
