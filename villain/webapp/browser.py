"""Run the web API in the browser, with no socket underneath it.

The demo ships the whole tool as WebAssembly (Pyodide): the same Python that
answers ``/api/*`` over a loopback socket in :mod:`~villain.webapp.server`
answers it here, called directly from JavaScript instead of read off a wire.
Reusing :class:`~villain.webapp.server.Handler` rather than re-implementing the
routing is the point -- the demo cannot drift from the real server, because it
*is* the real server with its transport removed.

Pyodide is single-threaded, so there is no request concurrency to guard: the
call runs to completion before JavaScript regains control. The origin checks in
:meth:`Handler.do_POST` still run, and pass, because every request is minted
here with a loopback ``Host`` -- there is no cross-origin surface when the
"server" lives inside the page it serves.
"""

from __future__ import annotations

import io
from pathlib import Path

from .server import Handler, writes_to_disk


class _Headers:
    """The slice of the header interface the handler actually reads.

    ``BaseHTTPRequestHandler`` hands the routing an ``email.message.Message``;
    the handler only ever calls ``.get``, so a case-insensitive dict is the
    whole contract and pulling in the email machinery would be theatre.
    """

    def __init__(self, values: dict[str, str]):
        self._values = {k.lower(): v for k, v in (values or {}).items()}

    def get(self, name: str, default=None):
        return self._values.get(name.lower(), default)


class _BridgeHandler(Handler):
    """A :class:`Handler` with the socket lifecycle skipped.

    ``BaseHTTPRequestHandler.__init__`` connects to a client and starts
    serving; there is neither here, so it is bypassed and the three attributes
    the routing reads -- ``path``, ``headers``, ``rfile`` -- are set by hand.
    ``_send`` is captured into a value instead of written to a socket.
    """

    def __init__(self, method: str, path: str, headers: dict, body: bytes):
        self.command = method
        self.path = path
        self.headers = _Headers(headers)
        self.rfile = io.BytesIO(body or b"")
        self.result: tuple[int, bytes, str] | None = None

    def _send(self, code: int, payload, content_type: str = "application/json"):
        from .jsonutil import encode
        body = encode(payload)
        self.result = (code, bytes(body), content_type)


def set_db(path: str) -> None:
    """Point every subsequent request at ``path`` inside the Pyodide FS."""
    Handler.db_path = Path(path)


def dispatch(method: str, path: str, headers: dict | None = None, body: bytes = b"") -> tuple[int, bytes, str]:
    """Route one request and return ``(status, body, content_type)``."""
    handler = _BridgeHandler(method, path, headers or {}, body)
    if method == "GET":
        handler.do_GET()
    elif method == "POST":
        handler.do_POST()
    else:
        handler._send(405, {"error": "method not allowed"})
    return handler.result


def dispatch_json(method: str, path: str, body: str = "") -> dict:
    """The shape the JavaScript ``fetch`` shim wants: a plain dict.

    Writes need a same-origin ``Host``/``Origin`` to clear the CSRF guard in
    :meth:`Handler.do_POST`; in the browser the origin is the page itself, so
    the loopback values are both honest and the only ones that make sense.
    """
    raw = body.encode() if isinstance(body, str) else bytes(body or b"")
    headers = {
        "Host": "127.0.0.1",
        "Origin": "http://127.0.0.1",
        "Content-Type": "application/json",
        "Content-Length": str(len(raw)),
    }
    code, out, content_type = dispatch(method, path, headers, raw)
    # The API is JSON throughout; decode as text so the JS side can hand it
    # straight to a Response without a copy through the pyodide buffer proxy.
    #
    # `wrote` is how the hosted page knows to upload the database to the
    # account. It is answered here, by the module that owns the routes, because
    # the page used to answer it with a regex of its own -- and a regex that
    # does not know about a route added later says "nothing changed" for it,
    # which is a silent failure to save somebody's import.
    #
    # A definitions rebuild is a write that no route asked for, so the path
    # alone cannot see it. Without this, a GET that migrated reported false,
    # the stamp never left the worker, and the next visit rebuilt every hand.
    from .. import db
    from .heroview import consume_hero_dirty
    return {"status": code, "body": out.decode("utf-8"), "content_type": content_type,
            "wrote": db.consume_cache_dirty() or consume_hero_dirty() or (
                method == "POST" and code < 400 and writes_to_disk(
                    path.split("?")[0]))}


def set_progress(hook=None) -> None:
    """Route rebuild progress to the host, or stop routing it.

    The rebuild that needs a bar is the migration inside ``Store()``, which no
    caller asks for and every request can trigger. The host arms this around a
    call it is willing to show a bar for; :data:`villain.db.PROGRESS_HOOK`
    stays ``None`` the rest of the time, so nothing pays for a reporter nobody
    is watching.
    """
    from .. import db
    if hook is None:
        db.PROGRESS_HOOK = None
        return

    def report(done, total, phase):
        hook(int(done), int(total), str(phase))

    db.PROGRESS_HOOK = report


def build_hero(progress=None) -> dict:
    """The Hero payload, reporting progress while it is built.

    Separate from :func:`dispatch_json` because progress has to escape a call
    that takes minutes, and an HTTP-shaped interface has nowhere to put it. The
    worker hands in a JavaScript function; Python calls it as the walk goes.

    ``progress(done, total, phase)``. A total of zero means the phase cannot be
    counted -- fitting the trees, where the only true thing to report is that
    it is still going.
    """
    from ..db import Store, consume_cache_dirty
    from .heroview import consume_hero_dirty, hero_payload
    from .jsonutil import dumps

    def report(done, total, phase):
        if progress is not None:
            progress(int(done), int(total), str(phase))

    # Before anything at all, including the row counts and cache checks, so the
    # interface has something to show on the first frame rather than after the
    # first phase gets going.
    report(0, 0, "starting")
    with Store(Handler.db_path) as store:
        payload = hero_payload(store, progress=report)
    # A cold build writes ``.hero-cache.json`` beside the db. That file is
    # what the next visit has to find -- memory dies with the worker -- and
    # the page only flushes it when ``wrote`` is true. ``consume_cache_dirty``
    # is the definitions stamp, not this sidecar; without the hero flag the
    # cache never left MEMFS and every reload rebuilt.
    return {"status": 200 if payload is not None else 404,
            "body": dumps(payload if payload is not None else
                          {"error": "Could not identify hero automatically -- "
                                    "no player has cards known on enough of their "
                                    "own hands."}),
            "content_type": "application/json",
            "wrote": consume_cache_dirty() or consume_hero_dirty()}
