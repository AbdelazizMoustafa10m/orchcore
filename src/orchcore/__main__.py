"""Entry point for python -m orchcore."""

from __future__ import annotations

import sys


def main() -> None:
    """Placeholder CLI entry point."""
    print(f"orchcore {__import__('orchcore').__version__}")
    sys.exit(0)


if __name__ == "__main__":
    main()
