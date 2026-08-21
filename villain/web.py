"""Backwards-compatible alias for :mod:`villain.webapp`.

The UI outgrew one module -- 4,600 lines, three quarters of it a single string
of HTML, CSS and JavaScript. It is a package now. This module stays so that
``from villain.web import ...`` and ``python -m villain.web`` keep working;
the supported way in is ``villain test``.
"""

from __future__ import annotations

from .webapp import *  # noqa: F401,F403
from .webapp import (
    __all__,  # noqa: F401
    main,
)

if __name__ == "__main__":
    raise SystemExit(main())
