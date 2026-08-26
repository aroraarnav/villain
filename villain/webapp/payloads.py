"""Profiles and the roster, shaped for the browser.

Where a :class:`villain.profile.Profile` becomes JSON. The payload adds the
reference points a chart needs to be readable -- the population frequency and,
where one exists, the breakeven threshold -- which the profile itself has no
reason to carry.
"""

from __future__ import annotations

import copy

from ..analyze import as_dict, enrich
from ..archetypes import ARCHETYPE_BY_NAME, deviations
from ..db import Store
from ..exploits import RULES, find_watchlist
from ..skill import weaknesses
from ..timing import timing_tells
from .jsonutil import as_json

DISPLAY_STATS = [
    ("vpip", "VPIP", "hands played"),
    ("pfr", "PFR", "hands raised preflop"),
    ("three_bet", "3-bet", "raises facing a raise"),
    ("fold_to_three_bet", "fold to 3-bet", "after opening"),
    ("four_bet", "4-bet", "facing a 3-bet"),
    ("five_bet", "5-bet", "facing a 4-bet"),
    ("squeeze", "squeeze", "after a raise and a caller"),
    ("cold_call", "cold call", "calls a raise, no money in"),
    ("rfi", "open (RFI)", "first in, folded to them"),
    ("bb_defend", "BB defense", "big blind vs a raise"),
    ("cbet:flop", "c-bet flop", "as the preflop raiser"),
    ("cbet:turn", "c-bet turn", "after betting the flop"),
    ("fold_vs_bet:flop", "fold vs flop bet", "facing a bet"),
    ("fold_vs_bet:turn", "fold vs turn bet", "facing a bet"),
    ("fold_vs_bet:river", "fold vs river bet", "facing a bet"),
    ("check_raise:flop", "check-raise flop", "after checking"),
    ("wtsd", "went to showdown", "after seeing the flop"),
    ("wsd", "won at showdown", "of showdowns reached"),
    ("aggression:flop", "flop aggression", "bets+raises of all actions"),
    ("aggression:turn", "turn aggression", "bets+raises of all actions"),
    ("tank_fold", "tank-fold", "folds after a long pause"),
    ("tank_fold:flop", "tank-fold flop", "flop folds after a long pause"),
    ("tank_fold:turn", "tank-fold turn", "turn folds after a long pause"),
    ("tank_fold:river", "tank-fold river", "river folds after a long pause"),
    ("snap_call", "snap-call", "calls made instantly"),
    ("snap_call:flop", "snap-call flop", "flop calls made instantly"),
    ("snap_call:turn", "snap-call turn", "turn calls made instantly"),
    ("snap_call:river", "snap-call river", "river calls made instantly"),
]

# Stats whose exploit threshold is worth drawing as a second reference tick.
_THRESHOLD_RULES = {rule.stat: rule for rule in RULES}


def _references(stat: str, _regime: str, profile) -> dict:
    """Population frequency and, where one exists, the breakeven threshold.

    The tick has to be the same field the estimate was shrunk toward. After
    ``villain fit`` that is ``profile.population`` (the pool), not the built-in
    online mean: drawing the online number next to a home-game posterior is how
    most of a loose pool read "high vs field".
    """
    out = {"population": profile.population(stat)}
    rule = _THRESHOLD_RULES.get(stat)
    if rule is not None:
        try:
            out["breakeven"] = rule.threshold(profile)
            out["breakeven_label"] = ("bluff breaks even"
                                      if stat.startswith(("fold_vs_bet", "fold_to_cbet"))
                                      else "exploit threshold")
        except Exception:
            pass
    return out


def profile_payload(profile, player_id: int | None = None) -> dict:
    """``as_dict`` plus the reference points the charts need to be readable."""
    enrich(profile)
    payload = as_dict(profile)
    # Carried so the UI can link a read back to the hands behind it. Absent for
    # an unsaved session, whose hands are not in the database to look up.
    payload["player_id"] = player_id
    payload["rows"] = []
    for stat, label, denominator in DISPLAY_STATS:
        est = profile.stats.get(stat)
        if est is None or est.opps <= 0:
            continue
        payload["rows"].append({
            "stat": stat, "label": label, "denominator": denominator,
            "value": est.value, "lo": est.lo, "hi": est.hi,
            "raw": est.raw,
            # Opportunity counts are fractional inside the model (pooling
            # across table sizes), but a sample size rendered as
            # 92.86041666666667 is noise on screen.
            "opps": round(est.opps, 1), "weight": est.weight,
            **_references(stat, profile.regime, profile),
        })
    arch = ARCHETYPE_BY_NAME.get(profile.archetype)
    payload["plan"] = arch.plan if arch else ""
    payload["summary"] = arch.summary if arch else ""
    payload["regime_label"] = profile.regime_label
    payload["deviations"] = [
        {"feature": f, "z": z}
        for f, z in sorted(deviations(profile).items(), key=lambda kv: -abs(kv[1]))[:10]
    ]
    payload["timing"] = {
        key.split(":", 1)[1]: {"seconds": round(profile.means[key] / 1000, 2),
                               "n": int(profile.means.get(f"{key}#n", 0) or 0)}
        for key in ("think:fold", "think:call", "think:check", "think:aggro",
                    "think:pf", "think:flop", "think:turn", "think:river")
        if profile.means.get(key)
    }
    payload["timing_tells"] = [as_json(c, "action_label") for c in timing_tells(profile)]
    from ..gto import compare as _gto_compare
    from ..gto import rating as _gto_rating
    _grows = _gto_compare(profile)
    payload["gto"] = {
        "rating": _gto_rating(_grows),
        "rows": [as_json(r, "deviation") | {"opps": round(r.opps, 1)}
                 for r in _grows],
    }
    return payload


#: A book this small is a rounding error, not a profile -- somebody who sat
#: down for one hand at a different table size should not get their own row.
MIN_ROSTER_HANDS = 5


#: The roster is the Database tab's whole payload, and it costs one full
#: profile assembly -- shrinkage, archetype match, leak pricing, GTO compare --
#: per player. On a home-game database of 160 players that is 1.4 seconds of
#: arithmetic per visit to the tab, repeated for an answer that only changes
#: when the stored hands do. The hosted app runs the same code single-threaded
#: in a Pyodide worker with nothing repainting while it works, which is where
#: that cost stopped being merely wasteful.
_ROSTER_CACHE: dict[str, tuple[tuple, list[dict]]] = {}


def _roster_fingerprint(store: Store) -> tuple:
    """Everything the roster is computed from, cheaply enough to check first.

    Row *counts* alone are not enough and the difference matters: refitting
    priors rewrites the same number of rows in ``fitted_priors`` with different
    values, and every profile is read through them, so a count-keyed cache would
    serve pre-fit reads forever. Summing the columns catches a value that
    changed without the shape changing.

    Measured against the live database: 4ms, versus 1,440ms to rebuild -- so
    this is checked on every request rather than invalidated by hand from the
    routes that write. An invalidation hook is a thing to forget; this is not.
    """
    hands, players = store.conn.execute(
        "SELECT (SELECT COUNT(*) FROM hands), (SELECT COUNT(*) FROM players)"
    ).fetchone()
    ratios = tuple(store.conn.execute(
        "SELECT COUNT(*), SUM(hits), SUM(opps) FROM ratios").fetchone())
    priors = tuple(store.conn.execute(
        "SELECT COUNT(*), SUM(strength) FROM fitted_priors").fetchone())
    return (hands, players, ratios, priors)


def roster_payload(store: Store) -> list[dict]:
    """One row per player. Table sizes are pooled, not listed separately."""
    key = str(store.path)
    fingerprint = _roster_fingerprint(store)
    cached = _ROSTER_CACHE.get(key)
    if cached is not None and cached[0] == fingerprint:
        # Copied out: the caller owns what it gets back, and one caller sorting
        # or annotating the list in place would otherwise corrupt every later
        # reader. 0.4ms against the 1,440ms this is avoiding.
        return copy.deepcopy(cached[1])
    rows = _build_roster(store)
    _ROSTER_CACHE[key] = (fingerprint, rows)
    return copy.deepcopy(rows)


def _build_roster(store: Store) -> list[dict]:
    from ..gto import compare as _gto_compare
    from ..gto import rating as _gto_rating
    rows = []
    for player in store.players():
        profile = store.profile(int(player["id"]))
        if profile is not None:
            enrich(profile)
            top = profile.tags[0] if profile.tags else None
            # Fall back through what is known: a priced leak, then an
            # unconfirmed one, then the weakest rated part of their game.
            # "None clears the bar" is true and useless -- it leaves the
            # weakest player on the table looking like the safest.
            headline, status, note = None, None, ""
            if top is not None:
                headline, status = top.headline, "confirmed"
                note = f"{top.severity:.2f} bb/100, {top.tier} read"
            else:
                watch = find_watchlist(profile)
                if watch:
                    headline, status = watch[0].headline, "watch"
                    note = (f"{watch[0].confidence:.0%} sure over "
                            f"{watch[0].opps:.0f} spots -- not confirmed")
                else:
                    weak = weaknesses(profile.skill)
                    if weak:
                        headline, status = weak[0].name, "rated"
                        note = (f"scores {weak[0].score:.0f}/100 here"
                                + (f" ({weak[0].note})" if weak[0].note else "")
                                + " -- from the rating, not a measured frequency")
            rows.append({
                "player_id": int(player["id"]),
                "name": profile.name or player["display_name"],
                "aliases": player["aliases"],
                "regime": profile.regime,
                "regime_label": profile.regime_label,
                "table_mix": profile.table_mix,
                "hands": profile.hands,
                "sample_quality": profile.sample_quality,
                "archetype": profile.archetype,
                "confidence": profile.archetype_confidence,
                # Unmeasured rows carry no skill number so the default sort
                # (high first) puts them last rather than in the middle at 50.
                "skill": (None if not profile.skill.measured
                          else profile.skill.base),
                "skill_tier": profile.skill.tier,
                "skill_confidence": profile.skill.confidence,
                "skill_measured": profile.skill.measured,
                "exploitability": profile.skill.exploitability,
                "gto": _gto_rating(_gto_compare(profile)),
                "top_leak": headline,
                "top_leak_status": status,
                "top_leak_note": note,
                "top_leak_severity": round(top.severity, 2) if top else 0.0,
                "leak_count": len(profile.tags),
                "last_seen": profile.last_seen,
            })
    rows.sort(key=lambda r: (-r["hands"],))
    return rows


# -- uploaded sessions, held in memory until saved -----------------------------
# A session is deliberately not written anywhere. You can drop a file in, read
# the table, and close the tab without the database gaining a single hand.



# -- which tabs can be opened yet ----------------------------------------------

#: A player needs this many hands before the simulator can act from a measured
#: profile rather than from the prior alone -- below it, every villain plays the
#: population average and the practice is against nobody in particular. Same
#: cliff as ``sample_quality == "usable"`` / ``MIN_REGIME_HANDS``: five hands
#: is the prior with a name attached.
MIN_SIM_HANDS = 150


def tab_availability(store: Store) -> dict[str, dict]:
    """Which tabs have something to show, and why not when they do not.

    A tab that opens onto an explanation of its own emptiness is worse than one
    that is visibly not ready: the reader has to click it to find out, and then
    click back. The reason travels with the answer so the interface can say why
    on hover instead of spending a navigation on it.

    Reasons name the fix, not the deficiency -- "import an export you played
    in" rather than "no hero found" -- because every one of these is reached by
    someone who has just arrived and has no idea what the tool wanted.
    """
    # Local, not top-level: heroview imports this module, so importing it back
    # at module scope is a cycle.
    from .heroview import _cached_hero_id

    hands = store.conn.execute("SELECT COUNT(*) c FROM hands").fetchone()["c"]
    profiled = sum(1 for p in store.players() if (p["hands"] or 0) >= MIN_SIM_HANDS)

    if not hands:
        empty = "Import a hand history first — there is nothing in this database yet."
        return {
            "hero": {"ok": False, "why": empty},
            "play": {"ok": False, "why": empty},
        }

    hero_id = _cached_hero_id(store)
    hero_why = None if hero_id else (
        "None of these hands show your own cards, so the tool cannot tell which "
        "seat is yours. Import an export from a game you played in.")

    play_why = None if profiled else (
        f"No opponent has {MIN_SIM_HANDS} hands yet — there is nobody measured "
        "enough to play against.")

    return {
        "hero": {"ok": hero_id is not None, "why": hero_why},
        "play": {"ok": profiled > 0, "why": play_why},
    }
