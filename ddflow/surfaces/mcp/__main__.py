"""`python -m ddflow.surfaces.mcp` — the same entry point as the `ddflow-mcp` console script.

The worker relaunch (`python -m ddflow.surfaces.mcp --repo ...`) and `ddflow-mcp =
"ddflow.surfaces.mcp:main"` both resolve through `ddflow.surfaces.mcp.main`; this file is
what keeps `-m` working now that the module is a package.
"""

from ._serve import main

if __name__ == "__main__":
    raise SystemExit(main())
