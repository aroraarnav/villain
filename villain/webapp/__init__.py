"""Local web UI.

Split by what each part is responsible for rather than kept as one file:

* :mod:`~villain.webapp.payloads` -- profiles and the roster, shaped for JSON.
* :mod:`~villain.webapp.sessions` -- uploads held in memory until committed.
* :mod:`~villain.webapp.heroview` -- the hero page and its caches.
* :mod:`~villain.webapp.server` -- routing and the request handler.
* :mod:`~villain.webapp.assets` -- the page, stylesheet and script, on disk.

Everything the old single-module ``villain.web`` exposed is re-exported here.
That module was kept for a while as an alias; nothing outside this repository
ever imported it, and the only thing it went on doing was letting the tests
exercise a compatibility shim instead of the package.
"""

from .assets import page, static
from .heroview import hero_payload
from .payloads import DISPLAY_STATS, MIN_ROSTER_HANDS, profile_payload, roster_payload
from .server import LOCAL_HOSTS, MAX_BODY_BYTES, Handler, main, serve
from .sessions import (
                       MAX_SESSIONS,
                       SESSION_TTL,
                       SESSIONS,
                       SIM_GAMES,
                       apply_answers,
                       commit_session,
                       database_merges,
                       merged_hands,
                       parse_upload,
                       question_payload,
                       session_identity_labels,
                       session_payload,
)

__all__ = [
    "DISPLAY_STATS", "Handler", "LOCAL_HOSTS", "MAX_BODY_BYTES",
    "MAX_SESSIONS", "MIN_ROSTER_HANDS", "SESSIONS", "SESSION_TTL", "SIM_GAMES",
    "apply_answers", "commit_session", "database_merges", "hero_payload",
    "main", "merged_hands", "page", "parse_upload",
    "profile_payload", "question_payload", "roster_payload", "serve",
    "session_identity_labels", "session_payload", "static",
]
