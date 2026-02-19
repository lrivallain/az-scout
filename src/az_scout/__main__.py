"""Allow running the package directly: python -m az_scout."""

from az_scout.cli import cli

cli(standalone_mode=True)
