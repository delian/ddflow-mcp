"""Adapters: ports, default adapters, fakes and the one registry -- the BOTTOM layer.

Every layer may import `ddflow.adapters`; it imports no other ddflow layer (D-adapters-answers).
The pattern, the layout and how to add a port are in `docs/ARCHITECTURE.md`, "Adapter model".
Nothing is imported here, so importing the package costs nothing.
"""
