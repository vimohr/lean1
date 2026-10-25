"""Codex research supervision with Lean verification and email notifications."""

__version__ = "0.3.0"


def main() -> int:
    from .cli import main as cli_main

    return cli_main()
