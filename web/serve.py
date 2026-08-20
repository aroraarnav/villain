#!/usr/bin/env python3
"""Serve web/dist/ locally and open it -- the one-click local demo.

Pyodide needs HTTP (it will not load a wheel over file://), so this is the
thinnest possible static server over the built directory. Build first with
``python web/build.py``.
"""

from __future__ import annotations

import http.server
import threading
import webbrowser
from functools import partial
from pathlib import Path

DIST = Path(__file__).resolve().parent / "dist"
PORT = 8000


def main() -> int:
    if not (DIST / "index.html").exists():
        raise SystemExit("Nothing built yet -- run: python web/build.py")
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(DIST))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    url = f"http://127.0.0.1:{PORT}/"
    print(f"villain demo on {url}  (ctrl-c to stop)")
    threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
