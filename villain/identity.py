"""Recognizing a player you have seen before.

Name similarity after stripping case, punctuation and trailing digits, plus
a hard constraint: accounts dealt into the same hand are different people.
Play style is not used -- two tight-passives looking alike is not evidence
they are one person.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .archetypes import _log_beta_binomial
from .db import SPURIOUS_OVERLAP
from .priors import prior_for
from .profile import PROFILE_FEATURES
from .stats import StatBook

#: A Bayes factor above this is strong evidence for one player, below it for two.
STRONG_EVIDENCE = 4.0

#: Minimum hands each side needs before behavior is worth testing at all.
MIN_HANDS_FOR_BEHAVIOUR = 60

#: Similarity required before a merge is even mentioned. Trailing digits and
#: punctuation normalize away first (``PlayerG``/``PlayerG2`` score 1.0).
HIGH_NAME_SCORE = 0.92

#: Two accounts showing one screen name are usually a reconnect, occasionally
#: two people who picked the same nickname. The discriminator is whether they
#: were ever dealt in *together*, not whether their active periods overlap -- a
#: regular who reconnects every session overlaps himself by construction.
#: Co-occurrence is already enforced absolutely by ``SPURIOUS_OVERLAP``.


@dataclass
class Suggestion:
    keep: int
    absorb: int
    keep_name: str
    absorb_name: str
    name_score: float
    behavior_log_bf: float | None
    confidence: float
    reason: str
    matched_a: str = ""
    matched_b: str = ""


def normalize(name: str) -> str:
    """Strip the noise a screen name accumulates across sessions."""
    text = re.sub(r"[^a-z0-9]+", "", name.lower())
    stripped = re.sub(r"\d+$", "", text)
    return stripped or text


def display_key(name: str) -> str:
    """Case- and punctuation-insensitive, but digits intact.

    :func:`normalize` strips trailing digits, which is the wrong tool for
    "is this literally the same screen name" -- there ``Vik``/``Vik2`` differ.
    """
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def name_similarity(a: str, b: str) -> float:
    """Similarity of two screen names, 0-1: the better of shared runs
    (additions, truncations) and edit distance (transpositions, typos).
    ``PlayerK``/``PlaeyrK`` scores 0.73 on the first and 0.82 on the second.
    """
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    blocks = SequenceMatcher(None, na, nb).ratio()
    edits = 1.0 - _levenshtein(na, nb) / max(len(na), len(nb))
    return max(blocks, edits, _skeleton_score(na, nb), _containment_score(na, nb))


#: Shortest stem a name-plus-suffix match accepts. At 3+ it proposes
#: ``PlayerA``/``PlayerALaptop``; at 1-2 it matches on a shared first letter.
MIN_CONTAINED = 3


def _containment_score(na: str, nb: str) -> float:
    """Catch a name that is another name with something stuck on the end.

    ``PlayerA``/``PlayerALaptop``, ``PlayerB``/``PlayerB1hr`` -- one person
    labelling a device or a sitting. Neither other measure sees it: the suffix
    is most of the longer string, so both score 0.80, under the bar.

    A prefix, not a substring, since the suffix is appended and a substring
    rule matches the middle of unrelated names. Evidence for a question, never
    an answer: ``PlayerG``/``PlayerG North`` has the same shape and is two
    people.
    """
    short, long = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(short) < MIN_CONTAINED or short == long or not long.startswith(short):
        return 0.0
    return 0.93


def _skeleton(name: str) -> str:
    """The consonants, in order. ``PlayerD`` and ``PlyrD`` share one."""
    return re.sub(r"[aeiou]", "", name)


def _skeleton_score(na: str, nb: str) -> float:
    """Catch the vowel-dropped nickname, which neither other measure sees.

    ``PlayerD``/``PlyrD`` scores 0.73 on shared runs and 0.57 on edit distance,
    both under the bar, and dropping vowels is how people shorten a name.

    Skeletons must match exactly and be three consonants long -- at two,
    ``Dan`` and ``Dean`` both become ``dn``. Scores below an exact-name match,
    being weaker evidence.
    """
    sa, sb = _skeleton(na), _skeleton(nb)
    if len(sa) < 3 or sa != sb:
        return 0.0
    return 0.93


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1,          # deletion
                               current[j - 1] + 1,       # insertion
                               previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def behavior_log_bf(a: StatBook, b: StatBook) -> float | None:
    """Log Bayes factor for "one player" over "two"; positive favors a merge.

    Each statistic contributes pooled marginal likelihood minus separate, so
    agreeing on a rare tendency counts for far more than on a common one.
    """
    if a.hands < MIN_HANDS_FOR_BEHAVIOUR or b.hands < MIN_HANDS_FOR_BEHAVIOUR:
        return None
    regime = a.regime or b.regime
    total = 0.0
    used = 0
    for stat in PROFILE_FEATURES:
        ra, rb = a.ratios.get(stat), b.ratios.get(stat)
        if not ra or not rb or ra.opps < 8 or rb.opps < 8:
            continue
        mean, strength = prior_for(stat, regime)
        pooled = _log_beta_binomial(ra.hits + rb.hits, ra.opps + rb.opps, mean, strength)
        apart = (_log_beta_binomial(ra.hits, ra.opps, mean, strength)
                 + _log_beta_binomial(rb.hits, rb.opps, mean, strength))
        total += pooled - apart
        used += 1
    return total if used >= 5 else None


def suggest_links(store, min_name_score: float = HIGH_NAME_SCORE) -> list[Suggestion]:
    """Candidate merges, most confident first. Name match only and at a high
    bar -- two nits looking alike is not evidence, and a wrong merge corrupts
    both profiles. Distinct pairs are skipped; nothing merges automatically.
    """
    players = {int(r["id"]): r for r in store.players()}
    aliases: dict[int, list[str]] = {}
    for row in store.conn.execute("SELECT player_id, name FROM aliases"):
        aliases.setdefault(int(row["player_id"]), []).append(row["name"])

    out: list[Suggestion] = []
    ids = sorted(players)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if store.are_distinct(a, b):
                continue
            best = (0.0, "", "")
            for x in aliases.get(a, []):
                for y in aliases.get(b, []):
                    score = name_similarity(x, y)
                    if score > best[0]:
                        best = (score, x, y)
            score, matched_a, matched_b = best
            if score < min_name_score:
                continue
            confidence, _ = _combine(score, None)
            reason = _name_match_reason(matched_a, matched_b, score)
            # The busier account keeps its identity, so the merged profile
            # keeps the name the player is better known by.
            keep, absorb = (a, b) if players[a]["hands"] >= players[b]["hands"] else (b, a)
            if keep != a:
                matched_a, matched_b = matched_b, matched_a
            out.append(Suggestion(
                keep=keep, absorb=absorb,
                keep_name=players[keep]["display_name"],
                absorb_name=players[absorb]["display_name"],
                name_score=round(score, 3),
                behavior_log_bf=None,
                confidence=round(confidence, 3), reason=reason,
                matched_a=matched_a, matched_b=matched_b,
            ))
    out.sort(key=lambda s: -s.confidence)
    return out


def matched_only_by_containment(a: str, b: str, bar: float) -> bool:
    """True when a name-plus-suffix match is the *only* thing linking two names.

    Those are asked, never applied: the shape that makes ``PlayerA`` and
    ``PlayerALaptop`` one person makes ``PlayerG`` and ``PlayerG North`` two.
    """
    na, nb = normalize(a), normalize(b)
    if _containment_score(na, nb) < bar:
        return False
    from difflib import SequenceMatcher as _SM
    blocks = _SM(None, na, nb).ratio()
    edits = 1.0 - _levenshtein(na, nb) / max(len(na), len(nb), 1)
    return max(blocks, edits, _skeleton_score(na, nb)) < bar


def _name_match_reason(a: str, b: str, score: float) -> str:
    """Explain the match from the aliases that scored, not display names --
    those drift, and citing one makes a real hit look fake."""
    if a == b:
        return f"both appeared as “{a}”"
    if normalize(a) == normalize(b):
        # Same root, different strings. Saying "both appeared as Jay2" when one
        # of them is "Jay 5:30" states something that did not happen.
        return f"both shorten to “{normalize(a)}”"
    if _skeleton_score(normalize(a), normalize(b)):
        return f"“{a}” is “{b}” with the vowels dropped"
    return f"“{a}” ≈ “{b}” ({score:.0%})"


def _short_account(value) -> str:
    text = str(value or "")
    return text if len(text) <= 10 else f"{text[:6]}\u2026{text[-3:]}"


def _when(entry: dict) -> str:
    first, last = entry.get("first"), entry.get("last")
    if not first:
        return ""
    day = time.strftime("%-d %b", time.localtime(first / 1000))
    if last and time.strftime("%j", time.localtime(last / 1000)) != \
            time.strftime("%j", time.localtime(first / 1000)):
        return f"{day}\u2013{time.strftime('%-d %b', time.localtime(last / 1000))}"
    return day


def _distinguish(left: dict, right: dict, overlap: int = 0) -> str:
    """What separates two accounts wearing the same screen name. The name is
    not evidence, so: account id, volume, and when each was seen."""
    parts = []
    for side in (left, right):
        bits = [f"account {_short_account(side.get('account'))}"]
        if side.get("hands"):
            bits.append(f"{side['hands']} hands")
        when = _when(side)
        if when:
            bits.append(when)
        parts.append(", ".join(bits))
    return f"Same name, two account ids \u2014 {parts[0]}; {parts[1]}."


def _combine(name_score: float, log_bf: float | None = None) -> tuple[float, str]:
    """A name score as a probability and a one-line reason. ``log_bf`` is
    accepted for callers that compute it, but suggestions are name-only."""
    name_odds = min(3.0, max(-2.0, 8.0 * (name_score - 0.75)))
    confidence = 1.0 / (1.0 + math.exp(-(name_odds + (log_bf or 0.0))))
    return confidence, f"names match ({name_score:.0%})"


# -- import-time reconciliation ------------------------------------------------
# Two opposite things go wrong when a session is added: one account id picks up
# a new display name, or two account ids belong to one person. Both are asked
# rather than guessed -- a wrong answer either way corrupts a profile.


@dataclass
class Question:
    """Something the import needs a human to settle.

    ``names`` carries the display names in play, because saying "yes, same
    person" leaves a second question unanswered: what to call the result.
    ``auto`` marks matches safe to apply without asking (same account rename,
    or a clear match to somebody already in the database); the UI only prompts
    for the rest — typically two session accounts that might be one person.
    """

    id: str
    kind: str                 # "rename" or "alias"
    prompt: str
    detail: str
    default: bool             # the answer to preselect
    confidence: float | None
    left: dict
    right: dict
    names: list[str] = field(default_factory=list)
    default_name: str = ""
    auto: bool = False        # apply without prompting; prefer the DB name
    #: Every account this one decision covers. Two for an ordinary pair; more
    #: when a screen name reconnected many times and the whole run is one
    #: person. ``left``/``right`` stay the two representative sides so anything
    #: rendering a question does not have to know about clusters.
    members: list[dict] = field(default_factory=list)


def auto_answers(questions: list[Question]) -> dict[str, dict]:
    """Answers for questions marked ``auto``: same person, keep the DB name."""
    return {
        q.id: {"same": True, "name": q.default_name}
        for q in questions if q.auto
    }


def askable_questions(questions: list[Question]) -> list[Question]:
    """Questions that still need a human — net-new / ambiguous cases."""
    return [q for q in questions if not q.auto]

def _account_index(hands) -> dict[tuple[str, str], dict]:
    """Every (site, account) in a batch of hands, with its name and volume."""
    out: dict[tuple[str, str], dict] = {}
    for hand in hands:
        for seat in hand.seats:
            key = (hand.site, seat.player_id)
            entry = out.setdefault(key, {"site": hand.site, "account": seat.player_id,
                                         "name": seat.name, "hands": 0,
                                         "first": hand.started_at, "last": hand.started_at})
            entry["hands"] += 1
            entry["name"] = seat.name or entry["name"]
            if hand.started_at is not None:
                if entry["first"] is None or hand.started_at < entry["first"]:
                    entry["first"] = hand.started_at
                if entry["last"] is None or hand.started_at > entry["last"]:
                    entry["last"] = hand.started_at
    return out


def _same_name_clusters(incoming: dict, blocked: dict) -> list[list[tuple[str, str]]]:
    """Runs of accounts sharing one screen name that were never dealt in together.

    A site that issues a new account id per session turns one regular into
    dozens of accounts under the same name -- 36 of them for one player in the
    database this was written against. Asked pairwise that is 630 identical
    questions about one human; asked as a cluster it is one.

    Splitting is greedy and conservative: an account joins a run only if it
    co-occurred with nothing already in it, so two people who really do share
    a nickname stay in separate runs instead of being chained together by
    transitivity.
    """
    from .db import SPURIOUS_OVERLAP

    groups: dict[str, list[tuple[str, str]]] = {}
    for key, entry in incoming.items():
        groups.setdefault(display_key(entry["name"]), []).append(key)

    clusters: list[list[tuple[str, str]]] = []
    for keys in groups.values():
        if len(keys) < 2:
            continue
        # Busiest first, so a run forms around the account with the most hands.
        remaining = sorted(keys, key=lambda k: -incoming[k]["hands"])
        while remaining:
            run = [remaining.pop(0)]
            rest = []
            for key in remaining:
                if any(blocked.get(frozenset((key, member)), 0) > SPURIOUS_OVERLAP
                       for member in run):
                    rest.append(key)
                else:
                    run.append(key)
            if len(run) > 1:
                clusters.append(run)
            remaining = rest
    return clusters


def _incoming_co_occurrence(hands) -> dict[frozenset, int]:
    """How many hands each pair of accounts was dealt into together.

    A count rather than a flag. Sitting at a table together is normally proof
    of two different people, but a reconnect can leave a stale seat for a hand
    or two, and treating that as proof makes a legitimate merge permanently
    impossible.
    """
    pairs: dict[frozenset, int] = {}
    for hand in hands:
        accounts = [(hand.site, s.player_id) for s in hand.seats]
        for i, a in enumerate(accounts):
            for b in accounts[i + 1:]:
                key = frozenset((a, b))
                pairs[key] = pairs.get(key, 0) + 1
    return pairs


def session_questions(store, hands, min_name_score: float = HIGH_NAME_SCORE) -> list[Question]:
    """What to ask before folding a session into the database.

    Renames and clear matches against somebody already in the database are
    marked ``auto``: same person, keep the database's display name. The UI
    applies those silently and only prompts for leftover cases — usually two
    session accounts that might be one person, i.e. net-new identity questions.
    Alias prompts require a high name match; play style is not considered.
    """
    incoming = _account_index(hands)
    blocked = _incoming_co_occurrence(hands)
    questions: list[Question] = []

    stored = {
        (r["site"], r["account"]): r
        for r in store.conn.execute(
            "SELECT site, account, name, player_id, hands FROM aliases")
    }

    # 1. Same account id, different display name.
    for key, entry in sorted(incoming.items()):
        row = stored.get(key)
        if row is None or not row["name"] or row["name"] == entry["name"]:
            continue
        db_name, new_name = row["name"], entry["name"]
        questions.append(Question(
            id=f"rename:{key[0]}:{key[1]}",
            kind="rename",
            prompt=f"Is “{new_name}” the same player as “{db_name}”?",
            detail=(f"Both are account {key[1]} on {key[0]}. Same id usually means "
                    f"one person who renamed themselves; answer no and they are "
                    f"kept as two players from here on."),
            default=True,
            confidence=None,
            left={"name": db_name, "hands": row["hands"], "player_id": row["player_id"],
                  "where": "already in the database"},
            right={"name": new_name, "hands": entry["hands"],
                   "site": key[0], "account": key[1], "where": "in the hands you are adding"},
            # Prefer the name already on file so a reconnect does not invent a
            # second display name for somebody you already track.
            names=[db_name, new_name],
            default_name=db_name,
            auto=True,
        ))

    # 2. Different account ids that look like the same person.
    db_players = {int(r["id"]): r for r in store.players()}
    db_aliases: dict[int, list[str]] = {}
    for row in store.conn.execute("SELECT player_id, name FROM aliases"):
        db_aliases.setdefault(int(row["player_id"]), []).append(row["name"])

    def add_alias_question(qid, score, left, right, log_bf=None, overlap=0,
                           auto=False, matched_a=None, matched_b=None):
        confidence, _ = _combine(score, log_bf)
        reason = _name_match_reason(
            matched_a or left["name"], matched_b or right["name"], score)
        if overlap > SPURIOUS_OVERLAP:
            # Not asked at all. Above this many shared hands the two are
            # provably different people, and `Store.link` refuses the merge --
            # so asking produced a question whose "yes" could never be carried
            # out, and the import ended with a red error explaining why the
            # answer had been thrown away. Say nothing rather than offer a
            # choice that does not exist.
            return
        if overlap:
            # A handful of shared hands is a site glitch rather than evidence,
            # and the merge is still allowed -- so this one is asked, with the
            # overlap stated, because the reader is being invited to overrule
            # the strongest signal the tool has.
            reason += (f" \u2014 but seated together in {overlap} hand"
                       f"{'s' if overlap > 1 else ''}")
            confidence *= 0.5
            auto = False
        # When one side is already in the database, keep that display name.
        # Otherwise keep the busier account's name.
        db_side = next((s for s in (left, right)
                        if "database" in (s.get("where") or "")), None)
        if db_side is not None:
            default_name = db_side["name"]
            names = [db_side["name"]] + [s["name"] for s in (left, right)
                                         if s["name"] != db_side["name"]]
        else:
            busier = left if left.get("hands", 0) >= right.get("hands", 0) else right
            default_name = busier["name"]
            names = [left["name"], right["name"]]
        # Two accounts showing the *same* screen name, never dealt in together,
        # is what a reconnect looks like: the site issues a new account id and
        # the player retypes nothing. Asking "Are Vik and Vik the same person?"
        # gave the reader two identical strings and no way to answer, so say
        # what actually differs and lead with the likely answer. A merge is
        # still the expensive mistake, so the co-occurrence guard above stays
        # absolute -- it is only the wording and the default that change here.
        identical = display_key(left["name"]) == display_key(right["name"])
        if identical:
            prompt = (f"Two accounts are both called “{left['name']}”. "
                      "Same person?")
            reason = _distinguish(left, right, overlap)
        else:
            prompt = f"Are “{left['name']}” and “{right['name']}” the same person?"
        questions.append(Question(
            id=qid, kind="alias",
            prompt=prompt,
            detail=reason,
            # Clear matches to a known player default to yes; session-only
            # pairs still default to no (merging two real people is costly)
            # unless the screen name is identical and they never met.
            default=auto or (db_side is not None and overlap == 0)
            or (identical and overlap == 0),
            confidence=confidence,
            left=left, right=right,
            names=names, default_name=default_name, auto=auto))

    def already_one_player(a_key, b_key) -> bool:
        """Skip pairs the database has already been told are one person."""
        a, b = stored.get(a_key), stored.get(b_key)
        return bool(a and b and a["player_id"] == b["player_id"])

    # 2a. One screen name, many account ids, never at a table together: a
    # reconnect run. Applied without asking -- it is the same evidence the
    # co-occurrence guard treats as decisive everywhere else, and asking the
    # user 1,181 times whether "playerf" is "playerf" is not a question, it is
    # a wall. `villain unlink` splits one back out if it was ever wrong.
    reconnect_runs = _same_name_clusters(incoming, blocked)
    for run in reconnect_runs:
        sides = [{"name": incoming[k]["name"], "hands": incoming[k]["hands"],
                  "site": k[0], "account": k[1],
                  "where": "in the hands you are adding"} for k in run]
        sides.sort(key=lambda side: -side["hands"])
        busiest = sides[0]
        total = sum(side["hands"] for side in sides)
        questions.append(Question(
            id="reconnects:" + "|".join(sorted(k[1] for k in run)),
            kind="alias",
            prompt=(f"{len(sides)} accounts are all called \u201c{busiest['name']}\u201d. "
                    "Same person?"),
            detail=(f"The site issued a new account id each time they joined. "
                    f"None of the {len(sides)} was ever dealt into a hand with "
                    f"another, which is what a reconnect looks like and what "
                    f"two different people sharing a nickname would not look "
                    f"like. Together they are {total} hands."),
            default=True,
            confidence=0.98,
            left=busiest,
            right=sides[1],
            names=[busiest["name"]],
            default_name=busiest["name"],
            auto=True,
            members=sides,
        ))

    # Fuzzy matches are asked between *runs*, not between raw accounts: with
    # three accounts called Jack and two called Jack2 already clustered, the
    # pairwise form asked the same question six times.
    leader = {k: run[0] for run in reconnect_runs for k in run}

    seen_pairs: set[frozenset] = set()
    items = sorted(incoming.items())
    for i, (key, entry) in enumerate(items):
        # incoming vs incoming — never auto; two session seats may be two people
        for other_key, other in items[i + 1:]:
            pair = frozenset((leader.get(key, key), leader.get(other_key, other_key)))
            if pair in seen_pairs:
                continue
            overlap = blocked.get(frozenset((key, other_key)), 0)
            if overlap > SPURIOUS_OVERLAP:
                continue
            if already_one_player(key, other_key):
                continue
            score = name_similarity(entry["name"], other["name"])
            if score < min_name_score:
                continue
            # Identical screen names are handled as one cluster above, not as
            # C(n,2) separate yes/no questions about the same person.
            if display_key(entry["name"]) == display_key(other["name"]):
                continue
            seen_pairs.add(pair)
            add_alias_question(
                f"alias:{key[1]}|{other_key[1]}", score,
                {"name": entry["name"], "hands": entry["hands"], "site": key[0],
                 "account": key[1], "where": "in the hands you are adding",
                 "first": entry.get("first"), "last": entry.get("last")},
                {"name": other["name"], "hands": other["hands"], "site": other_key[0],
                 "account": other_key[1], "where": "in the hands you are adding",
                 "first": other.get("first"), "last": other.get("last")},
                overlap=overlap, auto=False)

        # incoming vs the database — clear name match auto-merges onto the
        # existing player and keeps their database display name.
        if key in stored:
            continue                    # already a known account, not a new face
        for player_id, row in db_players.items():
            best = (0.0, "")
            for n in db_aliases.get(player_id, []):
                score = name_similarity(entry["name"], n)
                if score > best[0]:
                    best = (score, n)
            if best[0] < min_name_score:
                continue
            pair = frozenset((key, ("db", str(player_id))))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            add_alias_question(
                f"alias:{key[1]}|db{player_id}", best[0],
                {"name": row["display_name"], "hands": row["hands"] or 0,
                 "player_id": player_id, "where": "already in the database"},
                {"name": entry["name"], "hands": entry["hands"], "site": key[0],
                 "account": key[1], "where": "in the hands you are adding"},
                # A name-plus-suffix match is evidence for a question and never
                # an answer, which is what `_containment_score` returning 0.93
                # against a 0.92 bar was always meant to buy: enough to raise
                # the pair, not enough to settle it. Auto-merging it anyway made
                # the shape that makes `PlayerA`/`PlayerALaptop` one person also
                # silently pool `PlayerG`/`PlayerG North`, who are two -- and a
                # wrong merge costs an unlink and a rebuild to undo, where a
                # wrong question costs a click.
                auto=not matched_only_by_containment(
                    best[1], entry["name"], min_name_score),
                matched_a=best[1], matched_b=entry["name"])

    questions.sort(key=lambda q: (q.kind != "rename", -(q.confidence or 1.0)))
    return questions
