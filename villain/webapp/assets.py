"""The page and its static files, read from disk once.

The markup, styles and script used to live in one 3,300-line string literal
inside the server module, which meant no syntax highlighting, no linting, and a
diff on a CSS tweak that read as a change to Python. They are plain files now,
served from ``/static``; the server module is back to being about HTTP.

``app.js`` is assembled here rather than stored. The hosted shell injects the
script as one inline ``<script>`` element -- it fetches the asset out of the
installed wheel through a shim, so an ``import`` inside it has nothing to
resolve against -- which is the only reason the UI was a single 3,800-line
file. Concatenating ``assets/app/*.js`` in name order gives the same one
script, in the same order, out of files that are each about one thing.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

ASSETS = Path(__file__).parent / "assets"

#: Extension -> content type for everything the UI serves. The fonts are
#: shipped rather than fetched: this tool reads real hand histories, and a
#: webfont request would tell a CDN every time somebody opened it.
TYPES = {".html": "text/html; charset=utf-8",
         ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8",
         ".svg": "image/svg+xml",
         ".woff2": "font/woff2"}


#: Source of ``/static/app.js``. Ordered by filename, and the numeric prefixes
#: are load-bearing: this is one script, so a name declared with ``const`` has
#: to appear before the line that reads it, exactly as it did when they were
#: one file.
APP_PARTS = ASSETS / "app"


@cache
def _read(name: str) -> bytes:
    if name == "app.js":
        return b"\n\n".join(part.read_bytes().strip(b"\n")
                          for part in sorted(APP_PARTS.glob("*.js"))) + b"\n"
    return (ASSETS / name).read_bytes()


def page() -> bytes:
    """The shell document."""
    return _read("index.html")


def static(name: str) -> tuple[bytes, str] | None:
    """``(body, content_type)`` for a /static request, or None if unknown.

    Rejects anything with a path separator in it: this is a fixed set of files
    shipped beside the module, never a directory to walk.
    """
    if "/" in name or "\\" in name or name.startswith("."):
        return None
    suffix = Path(name).suffix
    if suffix not in TYPES:
        return None
    if name != "app.js" and not (ASSETS / name).is_file():
        return None
    return _read(name), TYPES[suffix]
