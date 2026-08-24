"""The HTTP server: routing, static assets, and the request handler.

Local-only by construction -- it binds the loopback interface and rejects any
request whose Origin the local UI could not have sent.
"""

from __future__ import annotations

import gzip
import json
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..db import DEFAULT_PATH, Store, split_key
from ..evidence import find as find_evidence
from ..glossary import payload as glossary_payload
from ..glossary import stat_help
from ..identity import auto_answers, session_questions
from ..model import hand_from_dict
from ..parsers import UnknownFormat
from ..replay import replay
from ..stats import VS_HERO
from .assets import page, static
from .heroview import _cached_hero_id, forget_hero, hero_begin, hero_payload, hero_peek, hero_status
from .jsonutil import encode as json_encode
from .payloads import MIN_ROSTER_HANDS, profile_payload, roster_payload, tab_availability
from .sessions import SESSIONS, SIM_GAMES, _reap_sessions, apply_answers, commit_session, parse_upload, question_payload, session_brief, session_payload

#: Hostnames the UI may be reached on. Anything else is a rebinding attempt.
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", ""})

#: Cap on a request body. The upload route holds what it reads in memory.
#: The UI uploads multiple hand-history JSON files in a single JSON payload,
#: so large batches can exceed the default 64MB limit.
MAX_BODY_BYTES = 256 * 1024 * 1024

#: Which POST routes leave the stored database different afterwards, and which
#: only touch memory. Every route ``do_POST`` answers has to appear in one of
#: them -- ``tests/test_web.py`` reads this handler's source and fails on a
#: route that is in neither, because "I forgot to classify it" is exactly the
#: mistake this exists to make impossible.
#:
#: The hosted app is why this is data rather than a comment. It runs the same
#: handler in a Pyodide worker over a database it has to upload after a change,
#: and it decides whether to upload by asking this list. It used to carry its
#: own hand-copied copy of it, so a new writing route would have worked
#: perfectly on a laptop and silently never been saved to the account -- the
#: user's import surviving until they opened the app on another device.
#:
#: ``<token>`` stands for one path segment; see :func:`writes_to_disk`.
WRITING_POST_ROUTES = frozenset({
    "/api/reset",
    "/api/unlink",
    "/api/player/delete",
    "/api/session/<token>/commit",
})

#: The rest: parsing into memory, running the simulator, asking a model for
#: prose, answering identity questions on a session that has not been saved.
READING_POST_ROUTES = frozenset({
    "/api/upload",
    "/api/sim/new",
    "/api/sim/act",
    "/api/sim/step",
    "/api/sim/next",
    "/api/sim/analysis",
    "/api/session/<token>/identity",
    "/api/session/<token>/plan",
})


def writes_to_disk(path: str) -> bool:
    """Does a POST to ``path`` change what is stored on disk?

    The hosted shell asks this through :data:`/api/meta` rather than pattern
    matching on its own, so there is one answer and it is this module's.
    """
    if path in WRITING_POST_ROUTES:
        return True
    parts = path.split("/")
    if len(parts) == 5 and parts[:3] == ["", "api", "session"]:
        return f"/api/session/<token>/{parts[4]}" in WRITING_POST_ROUTES
    return False


class Handler(BaseHTTPRequestHandler):
    db_path = DEFAULT_PATH

    def log_message(self, *args):
        pass                      # the terminal belongs to the user, not the server

    def _send(self, code: int, payload, content_type="application/json"):
        body = json_encode(payload)
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urlparse(self.path)
        path = route.path
        # Reads carry the data this tool exists to keep local: the roster names
        # every player in a real home game, and hand replay returns their cards.
        # Only POST was checked, so any page open in the same browser could
        # `fetch("http://127.0.0.1:8766/api/roster")` -- or point an <img> at it
        # -- and read all of it. The shell and its assets are deliberately not
        # guarded: they hold nothing about anybody, and leaving the page itself
        # loadable keeps this from being able to break opening the app.
        if path.startswith("/api/") and not self._same_origin():
            return self._send(403, {"error": "cross-origin request refused"})
        try:
            if path in ("/", "/index.html"):
                return self._send(200, page(), "text/html; charset=utf-8")
            if path.startswith("/static/"):
                asset = static(path[len("/static/"):])
                if asset is None:
                    return self._send(404, {"error": "no such asset"})
                body, content_type = asset
                return self._send(200, body, content_type)
            if path == "/api/sessions":
                with Store(self.db_path) as store:
                    out = []
                    for sess in store.sessions():
                        out.append({k: v for k, v in sess.items() if k != "hand_ids"})
                    return self._send(200, out)
            if path.startswith("/api/session-detail"):
                sid = int(parse_qs(urlparse(self.path).query).get("id", ["0"])[0])
                with Store(self.db_path) as store:
                    match = next((x for x in store.sessions() if x["id"] == sid), None)
                    if match is None:
                        return self._send(404, {"error": "no such session"})
                    hero_id = _cached_hero_id(store)
                    players = store.session_detail(match)
                    for pl in players:
                        pl["is_hero"] = pl.get("player_id") == hero_id
                    return self._send(200, {
                        "id": match["id"], "started_at": match["started_at"],
                        "ended_at": match["ended_at"], "hands": match["hands"],
                        "hero_id": hero_id, "players": players})
            if path == "/api/roster":
                with Store(self.db_path) as store:
                    n_players = store.conn.execute(
                        "SELECT COUNT(*) c FROM players").fetchone()["c"]
                    n_fitted = store.conn.execute(
                        "SELECT COUNT(*) c FROM fitted_priors").fetchone()["c"]
                    return self._send(200, {
                        "players": roster_payload(store),
                        "db": str(self.db_path),
                        "hands": store.conn.execute(
                            "SELECT COUNT(*) c FROM hands").fetchone()["c"],
                        "hero_id": _cached_hero_id(store),
                        # An empty roster over a full hands table is a broken
                        # import, not an empty database. Say which.
                        "books_missing": store.books_missing(),
                        "fit_priors": {
                            "players": n_players,
                            "has_fitted": n_fitted > 0,
                        },
                    })
            if path.startswith("/api/player/"):
                player_id = int(path.rsplit("/", 1)[1])
                with Store(self.db_path) as store:
                    row = store.conn.execute(
                        "SELECT display_name FROM players WHERE id = ?",
                        (player_id,)).fetchone()
                    if row is None:
                        return self._send(404, {"error": "no such player"})
                    unified = store.profile(player_id)
                    profiles = [profile_payload(unified, player_id)] if unified else []
                    # The per-table breakdown stays available for anyone who
                    # wants to check that the pooling is not hiding something.
                    by_table = [profile_payload(p)
                                for p in store.profiles(player_id,
                                                        min_hands=MIN_ROSTER_HANDS)]
                    aliases = [dict(r) for r in store.conn.execute(
                        "SELECT site, account, name, hands FROM aliases WHERE player_id = ?",
                        (player_id,))]
                    return self._send(200, {
                        "player_id": player_id,
                        "display_name": row["display_name"],
                        "aliases": aliases,
                        "profiles": profiles,
                        "by_table": by_table if len(by_table) > 1 else [],
                        "notes": [dict(n) for n in store.notes(player_id)],
                        "hero_id": _cached_hero_id(store),
                    })
            if path.startswith("/api/session/"):
                token = path.rsplit("/", 1)[1]
                if token not in SESSIONS:
                    return self._send(404, {"error": "session expired -- upload again"})
                with Store(self.db_path) as store:
                    return self._send(200, session_payload(token, store))
            if path == "/api/hero":
                with Store(self.db_path) as store:
                    # "Is this going to take a while?" -- asked before the real
                    # request so the page can put a veil up first. It must not
                    # start anything: the whole point is to answer instantly.
                    if parse_qs(route.query).get("peek", ["0"])[0] == "1":
                        return self._send(200, hero_peek(store))
                    # Never block the request on the build. A cold hero is
                    # ~90s of model fitting; the page asks again rather than
                    # holding a socket open and showing nothing.
                    status = hero_status(store)
                    if status != "ready" and hero_begin(store):
                        return self._send(202, {
                            "status": "building",
                            "message": "Reading your hands -- fitting the "
                                       "strength model over every one of them. "
                                       "This runs once per import.",
                        })
                    if status == "building":
                        return self._send(202, {
                            "status": "building",
                            "message": "Reading your hands -- fitting the "
                                       "strength model over every one of them. "
                                       "This runs once per import.",
                        })
                    # Cold, and no background thread to build it on: that is
                    # the browser, where the whole tool is single-threaded.
                    # Building inside the request is slower to first paint than
                    # polling, and it is the only thing that ever finishes.
                    payload = hero_payload(store)
                    if payload is None:
                        return self._send(404, {
                            "error": "Could not identify hero automatically -- "
                                     "no player has cards known on enough of their "
                                     "own hands."})
                    return self._send(200, payload)
            if path == "/api/evidence":
                query = parse_qs(route.query)
                player_id = int(query.get("player", ["0"])[0])
                stat = query.get("stat", [""])[0]
                if not stat:
                    return self._send(400, {"error": "stat required"})
                with Store(self.db_path) as store:
                    hands = store.player_hands(player_id)
                # Count over *every* matching hand, then truncate. Truncating
                # first made "count" a synonym for the cap and, worse, computed
                # "hits" inside that window -- so a player who limped 4 times in
                # 6,210 hands showed 0 of 60, because none of the four fell in
                # the slice. The instances are what you came to see, so they go
                # first and the rest fill the remainder.
                found = find_evidence(hands, str(player_id), stat)
                hits = [e for e in found if e.hit]
                misses = [e for e in found if not e.hit]
                def recent(xs):
                    return sorted(xs, key=lambda e: e.started_at or 0, reverse=True)
                shown = recent(hits)[:60] + recent(misses)[:max(0, 60 - len(hits))]
                # One line saying what the count actually means, from the
                # glossary's own high/low readings -- a number without a
                # reading is the thing this whole tool exists to avoid.
                reading, rate, pop = "", None, None
                against = "the field"
                with Store(self.db_path) as store:
                    prof = store.profile(player_id)
                if prof is None:
                    pass
                elif stat.startswith(VS_HERO):
                    # An against-you slice is not among the shrunk stats and
                    # has no population -- there is no field frequency for
                    # "folds to that guy". What it is read against is the
                    # player's own baseline, so that is what the verdict
                    # compares it with, and it says which.
                    parent = stat[len(VS_HERO):]
                    match = next((a for a in prof.adjustments
                                  if a.stat == parent), None)
                    against = "everyone else"
                    if match is not None:
                        rate, pop = match.versus, match.baseline
                        entry = stat_help(parent) or {}
                        reading = entry.get("high" if rate >= pop else "low", "")
                elif prof.stats.get(stat) is not None:
                    rate = prof.stats[stat].value
                    pop = prof.population(stat)
                    entry = stat_help(stat) or {}
                    reading = entry.get("high" if rate >= pop else "low", "")
                return self._send(200, {
                    "stat": stat, "count": len(found), "hits": len(hits),
                    "rate": None if rate is None else round(rate, 4),
                    "population": None if pop is None else round(pop, 4),
                    "compared_to": against,
                    "reading": reading,
                    "shown_hits": sum(1 for e in shown if e.hit),
                    "hands": [vars(e) for e in shown],
                })
            if path.startswith("/api/hand/"):
                hand_id = path.rsplit("/", 1)[1]
                focus = parse_qs(route.query).get("focus", [None])[0]
                with Store(self.db_path) as store:
                    row = store.conn.execute(
                        "SELECT payload FROM hands WHERE hand_id = ?", (hand_id,)).fetchone()
                    if row is None:
                        return self._send(404, {"error": "no such hand"})
                    data = json.loads(gzip.decompress(row["payload"]))
                    hand = hand_from_dict(data)
                    accounts = {
                        (r["site"], r["account"]): int(r["player_id"])
                        for r in store.conn.execute(
                            "SELECT site, account, player_id FROM aliases")}
                for seat in hand.seats:
                    pid = (accounts.get((hand.site, split_key(seat.player_id, seat.name)))
                           or accounts.get((hand.site, seat.player_id)))
                    if pid is not None:
                        seat.player_id = str(pid)
                return self._send(200, replay(hand, focus=focus))
            if path == "/api/meta":
                with Store(self.db_path) as store:
                    return self._send(200, {
                        "tabs": tab_availability(store),
                        # The hosted shell decides whether a POST it just made
                        # has to be uploaded to the account. It asks here so
                        # that the answer comes from the module that owns the
                        # routes, not from a regex kept in step by hand.
                        "writing_routes": sorted(WRITING_POST_ROUTES),
                    })
            if path == "/api/glossary":
                return self._send(200, glossary_payload())
            return self._send(404, {"error": "not found"})
        except Exception as exc:                      # keep the server alive
            return self._send(500, {"error": str(exc)})

    def _same_origin(self) -> bool:
        """Accept only requests the local UI itself could have made.

        Checks Origin when the browser sends one, falls back to Referer, and
        validates Host either way so a DNS-rebinding name cannot point at this
        port and read the database. A request with neither header is allowed:
        that is curl and the CLI, which are not a browser and carry no
        ambient cookies or cross-site risk.
        """
        host = (self.headers.get("Host") or "").split(":")[0]
        if host and host not in LOCAL_HOSTS:
            return False
        stated = self.headers.get("Origin") or self.headers.get("Referer")
        if not stated:
            return True
        try:
            parsed = urlparse(stated)
        except ValueError:
            return False
        return parsed.hostname in LOCAL_HOSTS

    def do_POST(self):
        if not self._same_origin():
            # Every POST here is a write, and two of them are irreversible: a
            # merge cannot be undone and a reset empties the database. Without
            # this check any page open in the same browser could fire one at
            # localhost -- a text/plain body is a CORS-simple request, so it is
            # sent without a preflight and the typed "delete everything"
            # confirmation, which lives in the page, never enters into it.
            return self._send(403, {"error": "cross-origin request refused"})
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            return self._send(400, {"error": "bad content-length"})
        if length < 0 or length > MAX_BODY_BYTES:
            return self._send(413, {"error": "body too large"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "bad json"})
        if not isinstance(body, dict):
            return self._send(400, {"error": "body must be an object"})
        route = urlparse(self.path).path
        try:
            if route == "/api/upload":
                return self._upload(body)
            if route == "/api/sim/new":
                return self._sim_new(body)
            if route == "/api/sim/act":
                return self._sim_act(body)
            if route == "/api/sim/step":
                return self._sim_step(body)
            if route == "/api/sim/next":
                return self._sim_next(body)
            if route == "/api/sim/analysis":
                return self._sim_analysis(body)
            if route == "/api/reset":
                if body.get("confirm") != "delete everything":
                    return self._send(400, {"error": "reset not confirmed"})
                with Store(self.db_path) as store:
                    return self._send(200, store.reset())
            if route == "/api/player/delete":
                player_id = int(body.get("player_id", 0))
                with Store(self.db_path) as store:
                    # Hero is not a villain you can forget: the whole Hero tab
                    # is built from that identity, and deleting it would leave
                    # the tool reading a profile that no longer exists. The UI
                    # disables the button for the same reason, but the check
                    # belongs here too -- the button is not the only caller.
                    if player_id == _cached_hero_id(store):
                        return self._send(400, {"error":
                            "That is you. Deleting the hero would empty the Hero tab; "
                            "reset the database if that is really what you want."})
                    try:
                        result = store.delete_player(player_id)
                    except LookupError as exc:
                        return self._send(404, {"error": str(exc)})
                    # Every cached answer about the hero names players by id,
                    # so all of them have to go: one of those ids is now free.
                    forget_hero(store)
                    return self._send(200, result)
            if route.startswith("/api/session/") and route.endswith("/identity"):
                token = route.split("/")[3]
                if token not in SESSIONS:
                    return self._send(404, {"error": "session expired -- upload again"})
                # Being answered counts as being alive. Reading a dialog about
                # six accounts takes as long as it takes, and a session evicted
                # while somebody was thinking loses the whole import.
                SESSIONS[token]["created"] = time.time()
                apply_answers(SESSIONS[token], body.get("answers") or {})
                # Brief unless the caller is showing the preview. This is the
                # same trap as the upload response: building the full payload
                # profiles every hand in the session, which on a large import
                # is minutes of work behind a dialog that said "Applying".
                # `route` is the path alone in do_POST -- unlike do_GET, where it
                # is the whole parsed URL. Reading .query off it raised
                # "'str' object has no attribute 'query'" and took down every
                # apply.
                if parse_qs(urlparse(self.path).query).get("full", ["0"])[0] == "1":
                    with Store(self.db_path) as store:
                        return self._send(200, session_payload(token, store))
                return self._send(200, session_brief(token))
            if route.startswith("/api/session/") and route.endswith("/plan"):
                token = route.split("/")[3]
                if token not in SESSIONS:
                    return self._send(404, {"error": "session expired -- upload again"})
                return self._send(
                    200, [question_payload(q) for q in SESSIONS[token].get("questions", [])])
            if route.startswith("/api/session/") and route.endswith("/commit"):
                token = route.split("/")[3]
                if token not in SESSIONS:
                    return self._send(404, {"error": "session expired -- upload again"})
                SESSIONS[token]["created"] = time.time()
                with Store(self.db_path) as store:
                    return self._send(200, commit_session(
                        store, token, body.get("answers") or {}))
            with Store(self.db_path) as store:
                if route == "/api/unlink":
                    try:
                        new_id = store.unlink(int(body["player_id"]),
                                              str(body["site"]), str(body["account"]))
                    except LookupError as exc:
                        # No such alias on that player -- the caller asked
                        # about something that is not there, the same shape of
                        # answer /api/player/delete already gives. Served as a
                        # 500 it read as the tool breaking rather than as the
                        # answer to the question, which is what sends somebody
                        # looking for a bug that is not there.
                        return self._send(404, {"error": str(exc)})
                    # Splitting an account moves hands to a new identity without
                    # changing how many there are, and the hero caches key on
                    # exactly that count -- so if the account that just left was
                    # hero's, nothing would notice.
                    forget_hero(store)
                    return self._send(200, {"ok": True, "player_id": new_id})
            return self._send(404, {"error": "not found"})
        except ValueError as exc:
            return self._send(409, {"error": str(exc)})
        except Exception as exc:
            return self._send(500, {"error": str(exc)})

    def _sim_new(self, body: dict):
        import secrets

        from ..sim import Game, Villain
        vids = [int(x) for x in (body.get("villains") or [])][:5]
        if not vids:
            return self._send(400, {"error": "pick at least one villain"})
        stack = max(20, int(body.get("stack", 200)))
        bb = max(2, int(body.get("bb", 2)))
        sb = max(1, int(body.get("sb", bb // 2)))
        names, profiles = ["You"], [None]
        with Store(self.db_path) as store:
            known = {int(r["id"]): r["display_name"] for r in store.players()}
            for pid in vids:
                names.append(known.get(pid, f"Villain {pid}"))
                # Both books: the pooled one, and one per table size. Which is
                # used depends on how many seats the game actually has.
                by_regime = {p.regime: p for p in store.profiles(pid)}
                profiles.append(Villain(store.profile(pid), by_regime))
        token = secrets.token_urlsafe(9)
        game = Game(names, profiles, hero_seat=0, start_stack=stack, sb=sb, bb=bb)
        SIM_GAMES[token] = game
        for stale in list(SIM_GAMES)[:-8]:          # bound memory to a few games
            SIM_GAMES.pop(stale, None)
        return self._send(200, {"token": token, "state": game.state()})

    def _sim_act(self, body: dict):
        game = SIM_GAMES.get(body.get("token"))
        if game is None:
            return self._send(404, {"error": "game not found -- start a new one"})
        try:
            game.act(str(body.get("kind")), int(body.get("amount", 0)))
        except (RuntimeError, ValueError) as exc:
            return self._send(400, {"error": str(exc)})
        return self._send(200, {"state": game.state()})

    def _sim_step(self, body: dict):
        game = SIM_GAMES.get(body.get("token"))
        if game is None:
            return self._send(404, {"error": "game not found -- start a new one"})
        event = game.step()
        return self._send(200, {"state": game.state(), "event": event})

    def _sim_next(self, body: dict):
        game = SIM_GAMES.get(body.get("token"))
        if game is None:
            return self._send(404, {"error": "game not found -- start a new one"})
        game.new_hand()
        return self._send(200, {"state": game.state()})

    def _sim_analysis(self, body: dict):
        game = SIM_GAMES.get(body.get("token"))
        if game is None:
            return self._send(404, {"error": "game not found -- start a new one"})
        return self._send(200, {"analysis": game.analysis()})

    def _upload(self, body: dict):
        """Parse uploaded files into a session held in memory."""
        files = body.get("files") or []
        if not files and not body.get("token"):
            return self._send(400, {"error": "no files"})
        hands, names, rejected = [], [], []
        for item in files:
            name = str(item.get("name", "upload"))
            try:
                parsed = parse_upload(name, item.get("content", ""))
            except (UnknownFormat, ValueError, KeyError) as exc:
                rejected.append({"name": name, "reason": str(exc) or "unrecognised format"})
                continue
            if not parsed:
                rejected.append({"name": name, "reason": "no hands in file"})
                continue
            hands.extend(parsed)
            names.append({"name": name, "hands": len(parsed)})
        # A batch may be delivered in pieces so the page can paint between them:
        # `token` continues an open session, `more` says another piece is
        # coming. Identity questions are settled once, at the end, because they
        # are asked about the whole batch and recomputing them per piece would
        # be quadratic in the number of files.
        token = str(body.get("token") or "")
        more = bool(body.get("more"))
        if token and token in SESSIONS:
            session = SESSIONS[token]
            session["hands"].extend(hands)
            session["files"].extend(names)
            session.setdefault("rejected", []).extend(rejected)
            # Still being written to, so it is the newest session, not the
            # oldest. Without this a batch that takes longer than the others
            # sit around is the one _reap_sessions evicts -- and the import
            # fails at the end with "session expired", having done all the work.
            session["created"] = time.time()
        else:
            _reap_sessions()
            token = secrets.token_urlsafe(9)
            SESSIONS[token] = {"hands": list(hands), "files": names,
                               "created": time.time(), "rejected": list(rejected)}
            session = SESSIONS[token]

        if more:
            # Nothing is parsed twice: this is a running tally, deduplicated
            # when the batch closes.
            return self._send(200, {"token": token, "partial": True,
                                    "hands": len(session["hands"]),
                                    "files": len(session["files"])})

        rejected = session.get("rejected", rejected)
        if not session["hands"]:
            SESSIONS.pop(token, None)
            return self._send(400, {"error": "nothing could be parsed", "rejected": rejected})

        # One hand id can appear in two exports of the same game.
        unique, seen = [], set()
        for hand in sorted(session["hands"], key=lambda h: h.started_at):
            if hand.hand_id in seen:
                continue
            seen.add(hand.hand_id)
            unique.append(hand)

        names = session["files"]
        session["hands"] = unique
        # Identity is settled up front so the session being read is already
        # pooled. Reading the database to ask a better question is not the
        # same as writing to it -- nothing is stored until you save.
        with Store(self.db_path) as store:
            questions = session_questions(store, unique)
            SESSIONS[token]["questions"] = questions
            # Clear matches to existing players (and same-account renames) are
            # applied immediately — keep the database display name, only ask
            # about leftover net-new / ambiguous pairs.
            auto = auto_answers(questions)
            if auto:
                apply_answers(SESSIONS[token], auto)
        # Deliberately the brief payload: an import never shows the preview,
        # and building it means profiling every hand in the session a second
        # time. The session view asks for the full one when it opens.
        payload = session_brief(token)
        payload["rejected"] = rejected
        return self._send(200, payload)


def serve(db: Path = DEFAULT_PATH, port: int = 8766, open_browser: bool = True):
    Handler.db_path = Path(db)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"villain UI on {url}  (database: {db})")
    print("ctrl-c to stop")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(prog="villain test", description=__doc__.split("\n")[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    serve(db=args.db, port=args.port, open_browser=not args.no_browser)
    return 0


