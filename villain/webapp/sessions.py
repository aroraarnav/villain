"""Uploaded hand histories, held in memory until the user commits them.

A session is a parse that has not been saved: the hands, the identity
questions they raise, and the answers given so far. Nothing here writes to
the store until :func:`commit_session`, so dropping a file in to read the
table and closing the tab leaves the database untouched.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from ..analyze import enrich
from ..db import Store, split_key
from ..dynamics import adjustments
from ..features import record_hands
from ..identity import askable_questions, auto_answers
from ..model import hand_from_dict, hand_to_dict
from ..parsers import parse_file
from ..profile import build_unified, primary_regime
from .heroview import _to_you
from .payloads import profile_payload

SESSIONS: dict[str, dict] = {}
#: Live practice games, held in memory only. Keyed by an opaque token.
SIM_GAMES: dict = {}
SESSION_TTL = 6 * 3600
MAX_SESSIONS = 12


def _reap_sessions() -> None:
    now = time.time()
    stale = [k for k, v in SESSIONS.items() if now - v["created"] > SESSION_TTL]
    for key in stale:
        SESSIONS.pop(key, None)
    while len(SESSIONS) > MAX_SESSIONS:
        oldest = min(SESSIONS, key=lambda k: SESSIONS[k]["created"])
        SESSIONS.pop(oldest, None)


def parse_upload(filename: str, content: str):
    """Parse an uploaded file by writing it somewhere a parser can sniff it.

    The parser registry works off file paths so it can identify a format from
    the extension and the first few bytes; a temporary file keeps that contract
    intact rather than adding a second, divergent code path for uploads.
    """
    suffix = Path(filename).suffix or ".json"
    with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False) as fh:
        fh.write(content)
        temp = Path(fh.name)
    try:
        return parse_file(temp)
    finally:
        temp.unlink(missing_ok=True)


def database_merges(store: Store, hands: list) -> dict:
    """Accounts this session shares that the database already calls one player.

    So a merge made anywhere shows up everywhere. Answering at upload and
    merging later from the suggestions panel are the same decision, and a
    session that ignored the second would contradict the database it was about
    to be saved into.
    """
    alias = {(r["site"], r["account"]): (int(r["player_id"]), r["name"])
             for r in store.conn.execute(
                 "SELECT site, account, player_id, name FROM aliases")}
    names = {int(r["id"]): r["display_name"] for r in store.players()}

    seen: dict[tuple[str, str], int] = {}
    counts: dict[tuple[str, str], int] = {}
    for hand in hands:
        for seat in hand.seats:
            key = (hand.site, seat.player_id)
            counts[key] = counts.get(key, 0) + 1
            hit = alias.get(key) or alias.get((hand.site, split_key(seat.player_id, seat.name)))
            if hit:
                seen[key] = hit[0]

    grouped: dict[int, list] = {}
    for key, player_id in seen.items():
        grouped.setdefault(player_id, []).append(key)

    merges = {}
    for player_id, keys in grouped.items():
        if len(keys) < 2:
            continue
        target = max(keys, key=lambda k: counts.get(k, 0))
        for key in keys:
            merges[key] = {"account": target[1],
                           "name": names.get(player_id, target[1])}
    return merges


def merged_hands(session: dict, extra: dict | None = None) -> list:
    """The session's hands with confirmed same-person accounts pooled.

    Applied to a copy. The stored hands must keep the account ids the site
    actually wrote, because identity is a decision layered on top of them and
    decisions get revised; the hands themselves are evidence and do not.
    """
    merges = dict(session.get("merges") or {})
    merges.update(extra or {})
    if not merges:
        return session["hands"]
    hands = [hand_from_dict(hand_to_dict(h)) for h in session["hands"]]
    for hand in hands:
        for seat in hand.seats:
            target = merges.get((hand.site, seat.player_id))
            if target:
                seat.player_id, seat.name = target["account"], target["name"]
    return hands


def session_identity_labels(session: dict) -> dict[str, dict]:
    """For each pooled display name, session aliases and a database name if linked.

    After auto-merge the roster title is often already the database name; the
    muted line still needs the other side so you can see what is merging with
    what.
    """
    answers = session.get("answers") or {}
    by_keep: dict[str, dict] = {}
    for question in session.get("questions") or []:
        answer = answers.get(question.id)
        if not answer or not answer.get("same"):
            continue
        keep = answer.get("name") or question.default_name
        if not keep:
            continue
        entry = by_keep.setdefault(keep, {"db_name": None, "session_names": []})
        sides = [s for s in (question.left, question.right) if s]
        db_side = next((s for s in sides
                        if "database" in (s.get("where") or "")), None)
        if db_side and db_side.get("name"):
            entry["db_name"] = db_side["name"]
        for side in sides:
            name = side.get("name")
            where = side.get("where") or ""
            if name and "session" in where and name not in entry["session_names"]:
                entry["session_names"].append(name)
    return by_keep


def _conflicting_pairs(session: dict) -> list[list[str]]:
    """Accounts in this batch that provably are not the same person.

    Two accounts dealt into the same hand more than a glitch's worth of times
    are two people, and `Store.link` refuses to merge them. The dialog needs to
    know: it can then show a whole knot of similar names together -- which is
    the only way to make sense of one -- while keeping the pairs that cannot
    merge in separate columns and saying why, instead of accepting the drop and
    failing afterwards.

    Only pairs among accounts the dialog is about, which is a handful.
    """
    from ..db import SPURIOUS_OVERLAP
    from ..identity import _incoming_co_occurrence

    questions = session.get("questions") or []
    interesting = set()
    for q in questions:
        for side in (q.left, q.right):
            if side and side.get("account"):
                interesting.add((side.get("site") or "pokernow", side["account"]))
    if len(interesting) < 2:
        return []

    overlaps = _incoming_co_occurrence(session.get("hands") or [])
    out = []
    for key, count in overlaps.items():
        if count <= SPURIOUS_OVERLAP:
            continue
        pair = [k for k in key if k in interesting]
        if len(pair) == 2:
            out.append([f"ac{pair[0][1]}", f"ac{pair[1][1]}"])
    return out


def session_brief(token: str) -> dict:
    """What an upload needs to know, without profiling anything.

    :func:`session_payload` builds the whole preview -- every statistic for
    every hand in the session, all-in equities included -- because the session
    *view* shows profiles before you save. An import does not: it needs the
    token, the counts, and the identity questions, and then it commits, which
    computes all of that again from the stored hands.

    On a small session the duplicate pass costs nothing worth naming. On a
    71,000-hand import it was eighty seconds of native CPU, and the browser is
    an order of magnitude slower than that -- a quarter of an hour of work
    thrown away, under a progress message that said "matching players".
    """
    session = SESSIONS[token]
    return {
        "token": token,
        "files": session["files"],
        "hands": len(session["hands"]),
        "saved": session.get("saved", False),
        "questions": [question_payload(q) for q in askable_questions(
            session.get("questions") or [])],
        # Pairs already settled as the same person. Not asked about, but sent
        # anyway: they are the edges that join two clusters of accounts, and
        # without them the dialog showed "tin/tintin" and "Tins white gf/Tin"
        # as two unrelated questions when the four are one knot.
        "linked": [question_payload(q) for q in (session.get("questions") or [])
                   if q.auto],
        # Pairs that can never be one person, so the dialog can keep them apart
        # rather than accepting a merge the database will refuse.
        "conflicts": _conflicting_pairs(session),
        "answered": bool(set(session.get("answers") or {})
                         - {q.id for q in (session.get("questions") or []) if q.auto}),
        "auto_merged": len(auto_answers(session.get("questions") or [])),
        "merges": [{"from": k[1], "to": v["name"]}
                   for k, v in (session.get("merges") or {}).items()],
    }


def session_payload(token: str, store: Store | None = None) -> dict:
    """Profiles for an uploaded session. Reads the store, never writes to it."""
    from ..hero import hero_of
    session = SESSIONS[token]
    extra = database_merges(store, session["hands"]) if store is not None else None
    mhands = list(merged_hands(session, extra))
    books = record_hands(mhands)
    # The exporter is hero, the same person the Hero tab is about.
    hero_key = hero_of(mhands)

    def _unified(by_regime):
        # Same shrink as database profiles when this pool has fitted priors.
        priors = None
        if store is not None and by_regime:
            fitted = store.fitted_priors(primary_regime(by_regime))
            priors = fitted or None
        profile = build_unified(by_regime, priors=priors)
        if profile is not None:
            # Store.profile attaches these for saved players; an uploaded
            # session has no store to do it, and a preview that silently drops
            # a section the same hands produce once saved is a preview of
            # something else.
            profile.adjustments = adjustments(by_regime, priors=priors)
        return profile

    keyed = [(k, _unified(by_regime)) for k, by_regime in books.items()]
    keyed = [(k, p) for k, p in keyed if p is not None]
    keyed.sort(key=lambda kp: -kp[1].hands)

    labels = session_identity_labels(session)
    # Also surface database display names from already-linked aliases when the
    # session did not need a question (same account id, same name).
    if store is not None:
        alias_names = {
            (r["site"], r["account"]): r["name"]
            for r in store.conn.execute(
                "SELECT site, account, name FROM aliases")
        }
        player_names = {int(r["id"]): r["display_name"] for r in store.players()}
        alias_player = {
            (r["site"], r["account"]): int(r["player_id"])
            for r in store.conn.execute(
                "SELECT site, account, player_id FROM aliases")
        }
        for hand in session["hands"]:
            for seat in hand.seats:
                key = (hand.site, seat.player_id)
                pid = alias_player.get(key) or alias_player.get(
                    (hand.site, split_key(seat.player_id, seat.name)))
                if pid is None:
                    continue
                db_name = player_names.get(pid) or alias_names.get(key)
                if not db_name:
                    continue
                # Profile name after merge is the keep/db name.
                keep = db_name
                entry = labels.setdefault(
                    keep, {"db_name": None, "session_names": []})
                entry["db_name"] = db_name
                if seat.name and seat.name not in entry["session_names"]:
                    entry["session_names"].append(seat.name)

    rows = []
    profile_payloads = []
    for player_key, profile in keyed:
        enrich(profile)
        top = profile.tags[0] if profile.tags else None
        link = labels.get(profile.name) or {}
        db_name = link.get("db_name")
        session_names = [n for n in (link.get("session_names") or [])
                         if n and n != profile.name]
        if db_name and db_name != profile.name and profile.name not in session_names:
            session_names = [profile.name] + session_names
        is_hero = hero_key is not None and player_key == hero_key
        row = {
            "player_id": None, "name": profile.name, "is_hero": is_hero,
            "db_name": db_name if db_name else None,
            "session_names": session_names,
            "regime": profile.regime, "regime_label": profile.regime_label,
            "table_mix": profile.table_mix,
            "hands": profile.hands, "sample_quality": profile.sample_quality,
            "archetype": profile.archetype, "confidence": profile.archetype_confidence,
            "skill": profile.skill.score, "skill_tier": profile.skill.tier,
            "skill_confidence": profile.skill.confidence,
            "exploitability": profile.skill.exploitability,
            "top_leak": top.headline if top else None,
            "leak_count": len(profile.tags),
        }
        rows.append(row)
        pp = profile_payload(profile)
        pp["db_name"] = row["db_name"]
        pp["session_names"] = row["session_names"]
        pp["is_hero"] = is_hero
        if is_hero:                       # blue identity + second-person voice
            pp = _to_you(pp)
        profile_payloads.append(pp)
    return {
        "token": token,
        "files": session["files"],
        "hands": len(session["hands"]),
        "players": rows,
        "profiles": profile_payloads,
        "saved": session.get("saved", False),
        "questions": [question_payload(q) for q in askable_questions(
            session.get("questions") or [])],
        # Pairs already settled as the same person. Not asked about, but sent
        # anyway: they are the edges that join two clusters of accounts, and
        # without them the dialog showed "tin/tintin" and "Tins white gf/Tin"
        # as two unrelated questions when the four are one knot.
        "linked": [question_payload(q) for q in (session.get("questions") or [])
                   if q.auto],
        # Pairs that can never be one person, so the dialog can keep them apart
        # rather than accepting a merge the database will refuse.
        "conflicts": _conflicting_pairs(session),
        # Whether a *person* has answered, not whether the session carries
        # answers at all. Auto-applied merges live in the same dict, so the
        # plain truthiness test made every upload look already-answered the
        # moment reconnect runs started being applied -- and the UI, which
        # only opens the dialog when a session is unanswered, silently
        # skipped every question that still needed a human.
        "answered": bool(set(session.get("answers") or {})
                         - {q.id for q in (session.get("questions") or []) if q.auto}),
        "auto_merged": len(auto_answers(session.get("questions") or [])),
        "merges": [{"from": k[1], "to": v["name"]}
                   for k, v in (session.get("merges") or {}).items()],
    }


def question_payload(question) -> dict:
    return {
        "id": question.id, "kind": question.kind, "prompt": question.prompt,
        "detail": question.detail, "default": question.default,
        "confidence": question.confidence, "left": question.left, "right": question.right,
        "names": question.names, "default_name": question.default_name,
        "auto": question.auto,
    }


def apply_answers(session: dict, answers: dict) -> None:
    """Record identity decisions on a session and pool the merged accounts.

    Asked at upload rather than at save, so the session you are reading has
    already combined them. One player split across two names halves both
    samples exactly when sample size is the scarce thing. New answers merge
    onto any auto-applied ones rather than replacing them.

    Pairs that sat together more than a reconnect glitch are never pooled
    here — ``commit_session`` would refuse the link, and showing a merged
    profile the save step cannot keep is worse than leaving them apart.
    """
    from ..db import SPURIOUS_OVERLAP
    from ..identity import _incoming_co_occurrence

    merged_answers = dict(session.get("answers") or {})
    merged_answers.update(answers or {})
    session["answers"] = merged_answers
    blocked = _incoming_co_occurrence(session.get("hands") or [])
    merges: dict[tuple[str, str], dict] = {}
    for question in session.get("questions", []):
        answer = merged_answers.get(question.id) or {}
        if not answer.get("same"):
            continue
        keep_name = answer.get("name") or question.default_name
        # A reconnect run covers every account under one name, not just two.
        sides = [side for side in (question.members
                                   or (question.left, question.right))
                 if side.get("account")]
        if not sides:
            continue
        keys = [(side["site"], side["account"]) for side in sides]
        overlap = 0
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                overlap = max(overlap, blocked.get(frozenset((a, b)), 0))
        if overlap > SPURIOUS_OVERLAP:
            continue
        # Everything folds onto the busiest account present in this session.
        target = max(sides, key=lambda side: side.get("hands", 0))
        for side in sides:
            merges[(side["site"], side["account"])] = {
                "account": target["account"], "name": keep_name}
    session["merges"] = merges


def commit_session(store: Store, token: str, answers: dict) -> dict:
    """Save an uploaded session, applying the identity answers given.

    Order matters. Hands are stored first so that every account exists as a
    player, then declined renames are re-keyed, then accepted aliases are
    merged. Doing the merges first would mean linking players that do not exist
    yet.
    """
    session = SESSIONS[token]
    questions = {q.id: q for q in session.get("questions", [])}
    answers = answers or session.get("answers") or {}

    def said_same(qid: str, question) -> bool:
        answer = answers.get(qid)
        if isinstance(answer, dict):
            return bool(answer.get("same"))
        if isinstance(answer, bool):
            return answer
        return question.default

    def chosen_name(qid: str, question) -> str:
        answer = answers.get(qid)
        if isinstance(answer, dict) and answer.get("name"):
            return answer["name"]
        return question.default_name

    name_splits = set()
    for qid, question in questions.items():
        if question.kind != "rename" or said_same(qid, question):
            continue
        right = question.right
        name_splits.add((right["site"], right["account"], right["name"]))

    # Defer: the merges below each rebuild the surviving player, and the
    # rebuild after them covers everything at once.
    report = store.add_hands(session["hands"], name_splits=name_splits,
                             defer_rebuild=True)
    # Refit the population from the pool itself. This used to be a button, but
    # it is not a preference: measuring a home game against a generic online
    # population makes every deviation wrong by the gap between the two, and
    # the fit already refuses (8+ players, 5+ opportunities per stat) when the
    # data cannot support it. Announced rather than silent, because it moves
    # the reference point every read is measured from.
    # A confirmed rename means the player is now known by the new name; the old
    # one stays reachable as an alias.
    for qid, question in questions.items():
        if question.kind != "rename" or not said_same(qid, question):
            continue
        player_id = _player_id_of(store, question.right)
        if player_id is not None:
            store.conn.execute("UPDATE players SET display_name = ? WHERE id = ?",
                               (chosen_name(qid, question), player_id))
    store.conn.commit()

    merged, blocked = 0, []
    for qid, question in questions.items():
        if question.kind != "alias" or not said_same(qid, question):
            continue
        # A reconnect run is one decision covering every account under the
        # name, so fold all of them in -- linking only left and right merged
        # two of thirty-six and left the rest as separate players.
        sides = question.members or [question.left, question.right]
        try:
            ids = [_player_id_of(store, side) for side in sides]
        except LookupError:
            continue
        ids = [i for i in ids if i is not None]
        if len(set(ids)) < 2:
            continue
        keep = ids[0]
        for absorb in ids[1:]:
            if absorb == keep:
                continue
            try:
                store.link(keep, absorb, rebuild=False)
                merged += 1
            except ValueError as exc:
                blocked.append(str(exc))
        store.conn.execute("UPDATE players SET display_name = ? WHERE id = ?",
                           (chosen_name(qid, question), keep))
        store.conn.commit()

    # One rebuild for the whole save: the hands, and every merge above.
    store.rebuild_pending()

    # After the rebuild, never before it. The fit reads the ratios table,
    # which does not exist until the books are built -- fitting first
    # silently produced nothing, and left every player in a home game
    # measured against online norms whose VPIP is 0.24 against this
    # pool's 0.42. Every read downstream is relative to that reference.
    priors_fitted = None
    fitted = store.fit_priors()
    if fitted:
        players = store.conn.execute(
            "SELECT COUNT(DISTINCT player_id) c FROM books").fetchone()["c"]
        priors_fitted = {"regimes": fitted, "players": players}
        # No rebuild here. Books are counts; the fitted prior is applied when a
        # profile is *read*, so refitting takes effect immediately. Rebuilding
        # recomputed every player from every stored hand for nothing, which on
        # a 12,000-hand database is most of a minute with the window blocked.


    session["saved"] = True
    return {
        "hands_new": report.hands_new, "duplicates": report.duplicates,
        "priors_fitted": priors_fitted,
        # Surfaced so a batch that silently stored unreadable hands says so.
        "unusable": report.unusable,
        "players_new": report.players_new, "merged": merged, "blocked": blocked,
    }


def _player_id_of(store: Store, side: dict) -> int | None:
    """Resolve one side of an alias question to an internal player id."""
    if side.get("player_id"):
        return int(side["player_id"])
    row = store.conn.execute(
        "SELECT player_id FROM aliases WHERE site = ? AND account = ?",
        (side.get("site"), side.get("account"))).fetchone()
    return int(row["player_id"]) if row else None


