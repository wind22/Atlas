"""Package entry point: ``python -m atlas`` runs the daily pipeline CLI."""
from __future__ import annotations

from .runner import main

if __name__ == "__main__":
    raise SystemExit(main())
