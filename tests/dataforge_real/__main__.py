"""``python -m tests.dataforge_real`` - real DataForge integration runner."""

from __future__ import annotations

import sys

from tests.dataforge_live.__main__ import main

if __name__ == "__main__":
    # If no subcommand passed, default to 'dataforge'
    argv = sys.argv[1:]
    if not argv or not argv[0].startswith("-") and argv[0] not in ("run", "dataforge"):
        argv = ["dataforge", *argv]
    elif argv and argv[0].startswith("-"):
        argv = ["dataforge", *argv]
    sys.exit(main(argv))
