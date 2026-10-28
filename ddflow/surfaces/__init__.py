"""The process surfaces: CLI, MCP and the reviewers they talk to (B-layering).

The docs check answers ddflow's own `--help` from this process's parser when it can
(Bc621160a82). The parser is a surface object, so `services.docscheck_help` -- a layer
below -- receives a FACTORY for it here, and the deferred import inside the factory keeps
the heavy CLI tree out of a plain `import ddflow.surfaces`.
"""

from ddflow.services.docscheck_help import bind_parser_factory


def _build_parser():
    from ddflow.surfaces.cli import build_parser  # deferred: the CLI pulls the whole tree

    return build_parser()


bind_parser_factory(_build_parser)  # every ddflow process and test has one registered
