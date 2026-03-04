"""Allow ``python -m factory_simulator`` invocation.

Equivalent to running the ``factory-simulator`` console script.
"""

from __future__ import annotations

import sys

from factory_simulator.cli import main

sys.exit(main())
