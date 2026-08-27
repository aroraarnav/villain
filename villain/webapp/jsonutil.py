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
from dataclasses import fields, is_dataclass
from typing import Any


def dumps(payload: Any) -> str:
    """Serialize ``payload``, replacing non-finite floats with ``null``."""
    return json.dumps(_clean(payload), allow_nan=False)


def encode(payload: Any) -> bytes:
    """Bytes for an HTTP body: pass through already-encoded assets, else JSON."""
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    return dumps(payload).encode()


def as_json(obj: Any, *derived: str) -> dict:
    """A dataclass as a dict, plus any derived properties named in ``derived``.

    The payload builders used to write the field list out by hand -- eighteen
    ``"n": cell.n`` pairs for one timing cell. The cost is not the typing: a
    field added to the dataclass is simply absent from the payload, with
    nothing failing on either side, so it is found when somebody notices the
    UI has been rendering ``undefined``. Properties are named explicitly
    because most of them are prose or formatting the browser does not want."""
    if not is_dataclass(obj):
        raise TypeError(f"{type(obj).__name__} is not a dataclass")
    out = {f.name: getattr(obj, f.name) for f in fields(obj)}
    out.update({name: getattr(obj, name) for name in derived})
    return out


def _clean(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        # Trimmed here so no payload has to. Nothing the browser shows is read
        # past four decimals -- every rate is rendered as a percentage and every
        # bb figure to one or two places -- and 92.86041666666667 down a column
        # of sample sizes was noise in the wire format as well as on screen.
        # A round() left in a payload builder is therefore a display decision
        # (`opps` to one place, say), not noise-trimming.
        return round(obj, 4) if math.isfinite(obj) else None
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
