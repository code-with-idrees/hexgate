"""Allow ``python -m hexgate`` to behave like the console script."""

from hexgate.cli import main

raise SystemExit(main())
