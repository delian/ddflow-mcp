"""Adapters: ports, default adapters, fakes and the one registry -- the BOTTOM layer.

Every layer may import `ddflow.adapters`; it imports no other ddflow layer (D-adapters-answers).
One module per family; the pattern and how to add a port are in
`docs/ddflow/adapters/README.md`. Nothing is imported here, so importing the package costs
nothing.
"""
