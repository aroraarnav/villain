"""JSON the browser can actually parse.

Python's ``json.dumps`` writes the tokens ``NaN`` and ``Infinity`` by default.
Those are not JSON. ``Response.json()`` then dies with ``Unexpected token 'N'``,
which is how the Database and Simulate tabs went blank on the demo: a leak
severity that came out of a Beta posterior as NaN, dumped into
``top_leak_severity``.
"""

from __future__ import annotations

import json
import math
from typing import Any


def dumps(payload: Any) -> str:
    """Serialize ``payload``, replacing non-finite floats with ``null``."""
    return json.dumps(_clean(payload), allow_nan=False)


def encode(payload: Any) -> bytes:
    """Bytes for an HTTP body: pass through already-encoded assets, else JSON."""
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    return dumps(payload).encode()


def _clean(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    # numpy scalars (and anything else with a 0-d .item()) become Python floats,
    # which then take the finite check above. A real array fails .item() and
    # falls through for json.dumps to reject.
    item = getattr(obj, "item", None)
    if callable(item):
        try:
            return _clean(item())
        except (ValueError, TypeError):
            pass
    return obj
