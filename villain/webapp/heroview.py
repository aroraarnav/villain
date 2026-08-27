"""The hero page: expensive to build, so cached in memory and on disk.

Hero analysis walks every hand the exporting player appears in and fits a
strength model over it -- seconds, not milliseconds. The result is cached
against the hand count that produced it and reused until the database grows.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from ..db import Store
from .payloads import profile_payload

_HERO_MODEL_CACHE: dict[str, tuple[int, object]] = {}
_HERO_PAYLOAD_CACHE: dict[tuple[str, int | None], tuple[int, dict | None]] = {}
#: The server handles requests on their own thread, so two Hero tab loads
#: landing close together used to each start their own fit -- the actual
#: incident this guards against: two ~40s fits running at once pegged every
#: core for minutes and starved every other tab's requests, not just Hero's.
#: One lock serialises fitting; the cache re-check after acquiring it means
#: the second request pays nothing once the first finishes.
_HERO_LOCK = threading.Lock()


def _hand_count(store: Store) -> int:
    return store.conn.execute("SELECT COUNT(*) c FROM hands").fetchone()["c"]


#: find_hero() itself is cheap (no model fit, just a scan of every seat), but
#: the roster loads on every visit to the Database tab, so it is cached the
#: same way -- by hand count -- rather than re-scanning every hand each time.
_HERO_ID_CACHE: dict[str, tuple[int, int | None]] = {}


def _cached_hero_id(store: Store, progress=None, hands=None) -> int | None:
    from ..hero import find_hero

    key = str(store.path)
    hand_count = _hand_count(store)
    cached = _HERO_ID_CACHE.get(key)
    if cached and cached[0] == hand_count:
        return cached[1]
    hero_id = find_hero(store, progress=progress, hands=hands)
    _HERO_ID_CACHE[key] = (hand_count, hero_id)
    return hero_id


def _hero_model(store: Store, progress=None, hands=None):
    from ..hero import fit_population_model

    key = str(store.path)
    hand_count = _hand_count(store)
    cached = _HERO_MODEL_CACHE.get(key)
    if cached and cached[0] == hand_count:
        return cached[1]
    model = fit_population_model(store, progress=progress, hands=hands)
    _HERO_MODEL_CACHE[key] = (hand_count, model)
    return model


#: Bump whenever _build_hero_payload's returned shape changes, so an old
#: cache file from a previous version of this module is a miss rather than a
#: served-stale response with fields the current frontend does not expect.
_HERO_CACHE_VERSION = 9


def forget_hero(store: Store) -> None:
    """Drop every cached answer about who the hero is.

    Both in-memory caches are keyed by hand count, which is precisely what an
    identity change does *not* alter: splitting the hero's account onto its own
    player, or deleting a player outright, moves hands between identities
    without adding or removing a single one. So the caches have to be dropped
    by hand, or the tool keeps answering with a hero id that no longer means
    what it did -- and the disk cache keeps serving a whole payload built
    around it."""
    key = str(store.path)
    _HERO_ID_CACHE.pop(key, None)
    _HERO_MODEL_CACHE.pop(key, None)
    _hero_disk_cache_path(store).unlink(missing_ok=True)


def _hero_disk_cache_path(store: Store) -> Path:
    return store.path.with_name(store.path.name + ".hero-cache.json")


def _hero_disk_cache_load(store: Store, hero_id: int | None,
                          hand_count: int) -> tuple[bool, dict | None]:
    """(hit, payload). ``hit`` is separate from ``payload`` because a cached
    "no hero found" answer is a legitimate ``None`` that should not trigger
    a recompute -- only a genuine cache miss should."""
    path = _hero_disk_cache_path(store)
    if not path.exists():
        return False, None
    try:
        saved = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return False, None
    entry = saved.get(str(hero_id))
    if (entry and entry.get("hand_count") == hand_count
            and entry.get("version") == _HERO_CACHE_VERSION):
        return True, entry.get("payload")
    return False, None


#: True after a hero sidecar was written and the host has not yet been told
#: the file changed. Same hole as ``db._CACHE_DIRTY``: the hosted page only
#: flushes IndexedDB (and the account copy) when a request reports ``wrote``,
#: and a cold Hero build is a GET that writes a sidecar the next visit has to
#: find. Without this flag that GET reported false, the cache stayed in
#: MEMFS, and every reload walked every hand again.
_HERO_DIRTY = False


def consume_hero_dirty() -> bool:
    """True if a hero cache write landed since the last consume."""
    global _HERO_DIRTY
    dirty, _HERO_DIRTY = _HERO_DIRTY, False
    return dirty


def _hero_disk_cache_save(store: Store, hero_id: int | None, hand_count: int,
                          payload: dict | None) -> None:
    global _HERO_DIRTY
    path = _hero_disk_cache_path(store)
    try:
        saved = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        saved = {}
    saved[str(hero_id)] = {
        "hand_count": hand_count, "version": _HERO_CACHE_VERSION, "payload": payload,
    }
    try:
        path.write_text(json.dumps(saved))
    except OSError:
        pass    # a stale/missing cache costs time next request, not correctness
    else:
        _HERO_DIRTY = True


#: Hero builds in flight, so a second request can be told "still building"
#: instead of queueing behind the lock for a minute and a half.
_HERO_BUILDING: set = set()

#: Live ``(done, total, phase)`` for an in-flight build, so a peek can drive
#: the same counted veil the in-process build reports. Without this, a local
#: ``villain test`` answered 202, the page dropped the veil, and the loader
#: became a static "this page will appear" with no bar.
_HERO_PROGRESS: dict = {}

#: Whether this interpreter can start a thread at all. Pyodide cannot, and the
#: browser build runs the same server module, so "start it in the background
#: and poll" has nowhere to run there. Probed once and remembered.
_THREADS_WORK: bool | None = None


def threads_work() -> bool:
    """True where a background build is possible; False under Pyodide.

    Callers use this to choose between answering ``202`` and polling, and
    building inside the request. Without it the browser asks for a build that
    can never start and then waits for it forever."""
    global _THREADS_WORK
    if _THREADS_WORK is None:
        try:
            probe = threading.Thread(target=lambda: None)
            probe.start()
            probe.join()
            _THREADS_WORK = True
        except (RuntimeError, OSError):
            _THREADS_WORK = False
    return _THREADS_WORK


def hero_status(store: Store, hero_id: int | None = None) -> str:
    """``ready`` | ``building`` | ``cold`` -- without starting a build."""
    key = (str(store.path), hero_id)
    hand_count = _hand_count(store)
    cached = _HERO_PAYLOAD_CACHE.get(key)
    if cached and cached[0] == hand_count:
        return "ready"
    if key in _HERO_BUILDING:
        return "building"
    hit, _payload = _hero_disk_cache_load(store, hero_id, hand_count)
    return "ready" if hit else "cold"


def hero_peek(store: Store, hero_id: int | None = None) -> dict:
    """What ``GET /api/hero?peek=1`` returns: status, plus progress if building."""
    status = hero_status(store, hero_id)
    out = {"status": status}
    progress = _HERO_PROGRESS.get((str(store.path), hero_id))
    if progress is not None:
        done, total, phase = progress
        out.update(done=done, total=total, phase=phase)
    return out


def hero_begin(store: Store, hero_id: int | None = None) -> bool:
    """Start the build on a background thread. True if one was started here.

    The work is seconds to minutes -- it walks every hand the exporting player
    appears in and fits a strength model over it -- and it was done inside the
    request, so the browser sat on a blank Hero tab with no way to know
    whether anything was happening. Same lock, same caches; only who waits
    changes."""
    key = (str(store.path), hero_id)
    if hero_status(store, hero_id) != "cold" or key in _HERO_BUILDING:
        return False
    if not threads_work():
        return False                   # caller builds inline; see server.py
    _HERO_BUILDING.add(key)
    _HERO_PROGRESS[key] = (0, 0, "starting")
    path = store.path

    def report(done, total, phase):
        _HERO_PROGRESS[key] = (int(done), int(total), str(phase))

    def run():
        try:
            with Store(path) as own:
                hero_payload(own, hero_id, progress=report)
        except Exception:
            pass                       # a failed build must not wedge the flag
        finally:
            _HERO_BUILDING.discard(key)
            _HERO_PROGRESS.pop(key, None)

    try:
        threading.Thread(target=run, name="hero-build", daemon=True).start()
    except (RuntimeError, OSError):
        # The flag is set before the thread exists, so a start that fails would
        # otherwise leave the key in _HERO_BUILDING with no `finally` to clear
        # it -- and every later request would be told "building" forever.
        _HERO_BUILDING.discard(key)
        _HERO_PROGRESS.pop(key, None)
        return False
    return True


def hero_payload(store: Store, hero_id: int | None = None, progress=None) -> dict | None:
    key = (str(store.path), hero_id)
    hand_count = _hand_count(store)

    cached = _HERO_PAYLOAD_CACHE.get(key)
    if cached and cached[0] == hand_count:
        return cached[1]

    with _HERO_LOCK:
        # Re-check both caches: another thread may have finished this exact
        # computation while this one was waiting for the lock.
        cached = _HERO_PAYLOAD_CACHE.get(key)
        if cached and cached[0] == hand_count:
            return cached[1]
        hit, payload = _hero_disk_cache_load(store, hero_id, hand_count)
        if not hit:
            payload = _build_hero_payload(store, hero_id, progress=progress)
            _hero_disk_cache_save(store, hero_id, hand_count, payload)
        _HERO_PAYLOAD_CACHE[key] = (hand_count, payload)
        return payload


#: Third person to second: the profile machinery writes about "them", but the
#: hero read is about you. English second-person plural agreement matches
#: "they", so a whole-word swap reads correctly on descriptive text; the only
#: opponent-directed fields (a leak's "do", the plan) are dropped by the hero
#: UI, so mistranslating them is harmless.
_HERO_PRONOUNS = {
    "they're": "you're", "they've": "you've", "themselves": "yourself",
    "theirs": "yours", "their": "your", "them": "you", "they": "you",
}


def _second_person(text: str) -> str:
    import re

    def swap(m):
        w = m.group(0)
        repl = _HERO_PRONOUNS.get(w.lower())
        if repl is None:
            return w
        return repl.capitalize() if w[0].isupper() else repl

    return re.sub(r"[A-Za-z']+", swap, text)


def _to_you(obj):
    if isinstance(obj, str):
        return _second_person(obj)
    if isinstance(obj, list):
        return [_to_you(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_you(v) for k, v in obj.items()}
    return obj


def _hero_self(store: Store, hero_id: int) -> dict | None:
    """Hero read through the ordinary villain machinery. Hero is just another
    player in the database, so their own StatBook yields a Profile with priced
    leaks and headline numbers exactly like a villain's -- the difference is
    only that the UI frames them for self-coaching. Never priced against a
    villain range; these are deviations from the population you were measured
    against."""
    try:
        prof = store.profile(hero_id)
    except Exception:
        return None
    if prof is None:
        return None
    return _to_you(profile_payload(prof, hero_id))


def _build_hero_payload(store: Store, hero_id: int | None, progress=None) -> dict | None:
    from ..hero import combined_grid, fold_grades, hero_visibility, missed_value, preflop_range, range_narrowing, sizing_tell, timing_tell
    from ..model import STREET_LABELS
    from ..reads import NotEnoughData  # raised by the strength-model fit hero.py calls, not by hero itself

    # Loaded once, used by both halves of the build. Working out whose seat is
    # whose and fitting the model each need every stored hand, and each used to
    # fetch its own copy -- so a cold Hero page decompressed and parsed the
    # whole database twice before it drew anything.
    loading = (lambda done, total: progress(done, total, "loading")) if progress else None
    all_hands = store.player_hands(progress=loading)

    # The sit at 100% "Reading your hand histories" was this stretch: the
    # decompress had finished and reported so, then find_hero and three
    # strength_by_street walks ran under that same label. They are countable
    # -- they are loops over hands we already have -- so they get their own
    # bars rather than a capped one.
    finding = (lambda done, total: progress(done, total, "finding")) if progress else None
    if hero_id is None:
        hero_id = _cached_hero_id(store, progress=finding, hands=all_hands)
    if hero_id is None:
        return None
    row = next((r for r in store.players() if int(r["id"]) == hero_id), None)
    if row is None:
        return None

    # Filtered from the batch already in hand, not fetched again. Hero's hands
    # are a subset of every hand, the seats here are already re-keyed to
    # internal ids, and a second query would decompress and parse a large slice
    # of the database for a second time.
    hero_key = str(hero_id)
    hero_hands = [hand for hand in all_hands
                  if any(str(seat.player_id) == hero_key for seat in hand.seats)]
    ranges = preflop_range(hero_hands, hero_id)
    seen, total = hero_visibility(hero_hands, hero_id)
    n_hero = len(hero_hands) or 1

    def walked(phase, parts):
        """One bar across ``parts`` equal walks of hero's hands.

        After the model has scored the database, each leftover walk is a
        similar cache hit -- splitting them into fake thirds of unequal
        cost was the bar that jumped 0-33% over minutes and 33-100% in
        a blink. Before the model, the first walk is the cold one and
        this is still the least-wrong count we have."""
        def part(i):
            if progress is None:
                return None
            def inner(done, total, _i=i):
                progress(_i * n_hero + done, parts * n_hero, phase)
            return inner
        return part

    try:
        # Score every known hand, then fit. That is the long countable
        # work; doing the hero-only walks first filled the strength cache
        # for *your* hands and then "reading" still had the rest of the
        # field to score -- two long bars, the first of them pretending
        # three equal walks.
        model = _hero_model(store, progress=progress, hands=all_hands)
        chunk = walked("grading", 5)
        if progress is not None:
            progress(0, 5 * n_hero, "grading")
        sizing = sizing_tell(hero_hands, hero_id, progress=chunk(0))
        timing = timing_tell(hero_hands, hero_id, progress=chunk(1))
        narrowing = range_narrowing(hero_hands, hero_id, progress=chunk(2))
        report = fold_grades(hero_hands, hero_id, model, progress=chunk(3))
        missed_report = missed_value(hero_hands, hero_id, model, progress=chunk(4))
        grade_error = None
    except NotEnoughData as exc:
        chunk = walked("measuring", 3)
        if progress is not None:
            progress(0, 3 * n_hero, "measuring")
        sizing = sizing_tell(hero_hands, hero_id, progress=chunk(0))
        timing = timing_tell(hero_hands, hero_id, progress=chunk(1))
        narrowing = range_narrowing(hero_hands, hero_id, progress=chunk(2))
        report = missed_report = None
        grade_error = str(exc)

    def _fold_json(g):
        return {"hand_id": g.hand_id, "street": STREET_LABELS.get(g.street, g.street),
               "hole_cards": list(g.hole_cards), "board": g.board, "texture": g.texture,
               "summary": g.summary, "in_words": g.in_words}

    def _bucketed_json(report):
        return {
            "graded": report.graded, "flagged": len(report.flagged),
            "rate": report.rate,
            "by_street": {STREET_LABELS.get(s, s): {"flagged": m, "graded": n}
                         for s, (m, n) in sorted(report.by_street().items())},
            "by_texture": {t: {"flagged": m, "graded": n}
                          for t, (m, n) in sorted(report.by_texture().items())},
            "worst": [_fold_json(g) for g in report.worst()],
        }

    def _tell_json(tell):
        tell_streets = {s for s, _ in tell.tells()}
        return {
            STREET_LABELS.get(street, street): {
                "strong": {"hands": strong.hands, "avg": strong.avg},
                "weak": {"hands": weak.hands, "avg": weak.avg},
                "in_words": tell.describe(street, lead=False),
                "is_tell": street in tell_streets,
            }
            for street, (strong, weak) in sorted(tell.by_street.items())
            if strong.hands or weak.hands
        }

    return {
        "hero_id": hero_id, "name": row["display_name"],
        "visibility": seen / total if total else 0.0, "hands": row["hands"] or 0,
        "ranges": [
            {"position": p.position, "hands": p.hands, "raised": p.raised,
             "called": p.called, "checked": p.checked, "folded": p.folded}
            for p in ranges.values()
        ],
        "grid": {cls: {"played": played, "dealt": dealt}
                for cls, (played, dealt) in combined_grid(ranges).items()},
        "fold_grades": None if report is None else _bucketed_json(report),
        "missed_value": None if missed_report is None else _bucketed_json(missed_report),
        "grade_error": grade_error,
        "sizing": _tell_json(sizing),
        "timing": _tell_json(timing),
        "narrowing": [
            {"street": STREET_LABELS.get(s.street, s.street), "hands": s.hands,
             "avg_strength": s.avg_strength}
            for s in sorted(narrowing, key=lambda s: s.street)
        ],
        "self": _hero_self(store, hero_id),
    }


