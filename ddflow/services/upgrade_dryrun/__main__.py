"""`python -m ddflow.services.upgrade_dryrun` — the dry run's command line."""

from ._cli import main

if __name__ == "__main__":
    raise SystemExit(main(doc=__doc__ or ""))
