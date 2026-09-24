"""The review after a practice session: what you did, graded against what the
villains actually held.

A real hand history never shows a folded hand, so the rest of the tool reads
players through the showdowns they reach. The simulator dealt every card, so
this review can do the thing nothing else here can: take each fold, call and
bluff you made and put it next to the hand you were up against. Everything in
it comes from the hands of this session -- no stored profile is read -- so a
tip about a villain is a tip about how they played *today*, with the hands
that show it.
"""

from __future__ import annotations

from collections import Counter

from .cards import HIGH_CARD, category_of, describe, evaluate_cards
from .equity import equities
from .hero import hand_class
from .holdem import STREETS

#: Board cards visible on each street.
BOARD_AT = (0, 3, 4, 5)

#: Fewer chances than this and a rate is an anecdote. Tips quote their counts
#: either way, but below this they are not written at all.
MIN_CHANCES = 3

#: How many hands the review lists. The flagged ones and the biggest swings;
#: a session of 200 hands does not want 200 rows.
KEY_HANDS = 8

#: Tips per villain. Past four the card stops being read.
MAX_TIPS = 4


def review(game) -> dict:
    hero = game.hero_seat
    hands = game.hands
    bb = game.bb or 1
    n = len(hands)
    graded = [_grade(h, hero) for h in hands]
    decisions = [d for g in graded for d in g]
    right = sum(1 for d in decisions if d["right"])
    keep, stop = _keep_stop(hands, hero, decisions)
    return {
        "hands": n,
        "pnl": game.pnl,
        "pnl_bb": round(game.pnl / bb, 1),
        "bb100": round(game.pnl / bb / max(n, 1) * 100, 1),
        "sb": game.sb, "bb": game.bb,
        "graded": {"decisions": len(decisions), "right": right},
        "line": _line(hands, hero),
        "grid": _grid(hands, hero),
        "positions": _positions(hands, hero),
        "decisions": _decision_groups(decisions),
        "keep": keep, "stop": stop,
        "villains": [_villain(hands, hero, v, game.names[v], bb)
                     for v in range(len(game.names)) if v != hero],
        "key_hands": _key_hands(hands, hero, graded, bb),
    }


# -- one hand, read -----------------------------------------------------------

def _board(hand, street: int) -> list[str]:
    return hand["board"][:BOARD_AT[street]]


def _category(hole, board) -> int:
    return category_of(evaluate_cards(list(hole) + list(board)))


def _made(hole, board) -> str:
    return describe(evaluate_cards(list(hole) + list(board))) if len(board) >= 3 else ""


def _live_before(hand) -> list[set[int]]:
    """Who was still in the pot at each action, in trace order."""
    live = set(range(len(hand["seats"])))
    out = []
    for a in hand["actions"]:
        out.append(set(live))
        if a["kind"] == "fold":
            live.discard(a["seat"])
    return out


def _next_by(hand, k: int, seat: int) -> dict | None:
    """``seat``'s next action on the same street as action ``k``."""
    street = hand["actions"][k]["street"]
    for a in hand["actions"][k + 1:]:
        if a["street"] != street:
            return None
        if a["seat"] == seat:
            return a
    return None


def _grade(hand, hero: int) -> list[dict]:
    """Every postflop decision of yours that the villains' cards can judge.

    Folds and calls against a bet are priced: your equity against the hand the
    bettor really held, next to the share of the pot you had to put in. Only
    heads-up spots are graded -- against two players, beating the bettor says
    nothing about the third. Bets with no pair are judged by whether the pot
    came back without a showdown, and a river check by whether it let the
    winning hand check through."""
    out = []
    lives = _live_before(hand)
    hole = hand["seats"][hero]["hole"]
    acts = hand["actions"]
    for k, a in enumerate(acts):
        if a["seat"] != hero or a["street"] == 0:
            continue
        street, board = a["street"], _board(hand, a["street"])
        base = {"hand_no": hand["hand_no"], "street": STREETS[street]}
        if a["owed"] > 0 and a["kind"] in ("fold", "call"):
            bettor = a["aggressor"]
            if bettor is None or bettor == hero or len(lives[k]) != 2:
                continue
            theirs = hand["seats"][bettor]["hole"]
            eq = equities([hole, theirs], board)[0]
            need = a["owed"] / (a["pot"] + a["owed"])
            enough = eq >= need
            kind = "fold" if a["kind"] == "fold" else "call"
            out.append(base | {
                "kind": kind, "right": enough if kind == "call" else not enough,
                "equity": round(eq, 3), "needed": round(need, 3),
                "against": hand["seats"][bettor]["name"],
                "their_hole": theirs, "their_made": _made(theirs, board),
            })
        elif a["kind"] == "raise" and _category(hole, board) == HIGH_CARD:
            worked = (not hand["showdown"] and hand["street"] == street
                      and hand["seats"][hero]["won"])
            out.append(base | {"kind": "bluff", "right": worked})
        elif (a["kind"] == "check" and street == 3 and a["owed"] == 0
              and hand["showdown"] and hand["seats"][hero]["won"]
              and not any(b["kind"] == "raise" and b["street"] == 3
                          for b in acts[k + 1:])):
            out.append(base | {"kind": "check", "right": False})
    return out


# -- your line ----------------------------------------------------------------

def _hero_pre(hand, hero):
    return [a for a in hand["actions"] if a["seat"] == hero and a["street"] == 0]


def _pre_raiser(hand) -> int | None:
    last = None
    for a in hand["actions"]:
        if a["street"] == 0 and a["kind"] == "raise":
            last = a["seat"]
    return last


def _saw_flop(hand, seat) -> bool:
    folded_pre = any(a["seat"] == seat and a["street"] == 0 and a["kind"] == "fold"
                     for a in hand["actions"])
    return not folded_pre and len(hand["board"]) >= 3


def _line(hands, hero) -> list[dict]:
    """Your session numbers, keyed to the glossary so each one explains itself.

    Counted the way :mod:`villain.features` counts them for a stored player, so
    a number here and the same number on your Hero tab measure one thing."""
    c = Counter()
    for h in hands:
        pre = _hero_pre(h, hero)
        c["dealt"] += 1
        c["vpip"] += any(a["kind"] in ("call", "raise") for a in pre)
        c["pfr"] += any(a["kind"] == "raise" for a in pre)
        facing_open = [a for a in pre if a["raises"] == 1 and a["owed"] > 0]
        if facing_open:
            c["3b_n"] += 1
            c["3b"] += facing_open[0]["kind"] == "raise"
        flop = [a for a in h["actions"] if a["seat"] == hero and a["street"] == 1]
        if _pre_raiser(h) == hero and flop and flop[0]["owed"] == 0:
            c["cb_n"] += 1
            c["cb"] += flop[0]["kind"] == "raise"
        for a in flop:
            if a["owed"] > 0:
                c["fvb_n"] += 1
                c["fvb"] += a["kind"] == "fold"
        if _saw_flop(h, hero):
            c["flop"] += 1
            if h["showdown"] and not h["seats"][hero]["folded"]:
                c["sd"] += 1
                c["sdw"] += h["seats"][hero]["won"]
    rows = [("vpip", "VPIP", c["vpip"], c["dealt"]),
            ("pfr", "PFR", c["pfr"], c["dealt"]),
            ("three_bet", "3-bet", c["3b"], c["3b_n"]),
            ("cbet:flop", "flop c-bet", c["cb"], c["cb_n"]),
            ("fold_vs_bet:flop", "fold to flop bet", c["fvb"], c["fvb_n"]),
            ("wtsd", "to showdown", c["sd"], c["flop"]),
            ("wsd", "won at showdown", c["sdw"], c["sd"])]
    return [{"stat": s, "label": lab, "hits": int(k), "chances": int(m),
             "value": round(k / m, 3) if m else None} for s, lab, k, m in rows]


def _grid(hands, seat) -> dict:
    """Dealt and played per hand class -- the shape the Hero tab's grid draws."""
    grid: dict[str, dict] = {}
    for h in hands:
        cls = hand_class(tuple(h["seats"][seat]["hole"]))
        cell = grid.setdefault(cls, {"dealt": 0, "played": 0})
        cell["dealt"] += 1
        cell["played"] += any(a["seat"] == seat and a["street"] == 0
                              and a["kind"] in ("call", "raise") for a in h["actions"])
    return grid


def _positions(hands, hero) -> list[dict]:
    rows: dict[str, Counter] = {}
    for h in hands:
        pos = h["seats"][hero]["position"]
        pre = _hero_pre(h, hero)
        row = rows.setdefault(pos, Counter())
        row["hands"] += 1
        if any(a["kind"] == "raise" for a in pre):
            row["raised"] += 1
        elif any(a["kind"] == "call" for a in pre):
            row["called"] += 1
    return [{"position": p, "hands": r["hands"], "raised": r["raised"],
             "called": r["called"]} for p, r in rows.items()]


def _decision_groups(decisions) -> dict:
    out = {}
    for kind in ("fold", "call", "bluff", "check"):
        rows = [d for d in decisions if d["kind"] == kind]
        out[kind] = {"n": len(rows), "right": sum(d["right"] for d in rows),
                     "wrong": sorted({d["hand_no"] for d in rows if not d["right"]})}
    return out


def _tip(text: str, hand_nos) -> dict:
    return {"text": text, "hands": sorted(set(hand_nos))[:12]}


def _plural(k: int, word: str) -> str:
    return f"{k} {word}" + ("" if k == 1 else "s")


def _keep_stop(hands, hero, decisions) -> tuple[list[dict], list[dict]]:
    """What to keep doing and what to stop, each backed by the hands behind it.

    Nothing is said without a count. A sentence that cannot point at the hands
    it came from is advice from somewhere else, and this review is about the
    session that just happened."""
    keep, stop = [], []
    by = {k: [d for d in decisions if d["kind"] == k]
          for k in ("fold", "call", "bluff", "check")}

    folds = by["fold"]
    bad = [d for d in folds if not d["right"]]
    if bad:
        stop.append(_tip(
            f"Folding hands that had the price. {len(bad)} of your {len(folds)} "
            f"graded folds had enough equity against what they really held to call.",
            [d["hand_no"] for d in bad]))
    elif len(folds) >= 2:
        keep.append(_tip(
            f"Your folds. All {len(folds)} graded folds were behind what "
            "they held by more than the price.", [d["hand_no"] for d in folds]))

    calls = by["call"]
    paid = [d for d in calls if not d["right"]]
    if len(paid) >= 2 or (paid and len(paid) * 2 >= len(calls)):
        stop.append(_tip(
            f"Paying off. {len(paid)} of your {len(calls)} graded calls were short "
            "of the odds against their actual hand.", [d["hand_no"] for d in paid]))
    elif len(calls) >= 2 and not paid:
        keep.append(_tip(f"Your calls. All {len(calls)} graded calls had the odds.",
                         [d["hand_no"] for d in calls]))

    bluffs = by["bluff"]
    if len(bluffs) >= 2:
        won = [d for d in bluffs if d["right"]]
        lost = [d for d in bluffs if not d["right"]]
        if len(won) * 2 >= len(bluffs):
            keep.append(_tip(
                f"Betting with no pair. {len(won)} of {len(bluffs)} took the pot "
                "without a showdown.", [d["hand_no"] for d in won]))
        else:
            stop.append(_tip(
                f"Betting with no pair into callers. Only {len(won)} of "
                f"{len(bluffs)} got through -- see which villains fold, below.",
                [d["hand_no"] for d in lost]))

    missed = by["check"]
    if missed:
        stop.append(_tip(
            f"Checking the winner on the river. {_plural(len(missed), 'time')} you "
            "checked the best hand and the river checked through: a bet was free money.",
            [d["hand_no"] for d in missed]))

    # Preflop, from counts alone -- no cards need revealing for these.
    btn = [h for h in hands if h["seats"][hero]["position"] == "BTN"]
    btn_played = [h for h in btn if any(a["kind"] in ("call", "raise")
                                        for a in _hero_pre(h, hero))]
    if len(btn) >= 4 and len(btn_played) * 10 <= len(btn) * 3:
        stop.append(_tip(
            f"Folding the button. You played {len(btn_played)} of {len(btn)} -- "
            "the seat that acts last every street is where the wide opens pay.",
            [h["hand_no"] for h in btn if h not in btn_played]))
    flats = [h for h in hands
             if any(a["kind"] == "call" and a["owed"] > 0 for a in _hero_pre(h, hero))
             and not any(a["kind"] == "raise" for a in _hero_pre(h, hero))]
    raises = [h for h in hands if any(a["kind"] == "raise" for a in _hero_pre(h, hero))]
    if len(flats) >= 4 and len(flats) > len(raises):
        stop.append(_tip(
            f"Calling preflop. You called {_plural(len(flats), 'time')} and raised "
            f"{len(raises)}. A raise can win the pot now; a call only waits.",
            [h["hand_no"] for h in flats]))
    elif len(raises) >= 4 and len(raises) >= 2 * max(len(flats), 1):
        keep.append(_tip(
            f"Raising, not calling. {len(raises)} raises against "
            f"{_plural(len(flats), 'call')} preflop.", [h["hand_no"] for h in raises]))
    return keep, stop


# -- each villain -------------------------------------------------------------

def _villain(hands, hero, v: int, name: str, bb: int) -> dict:
    """How one villain played this session, and what it says to do next time.

    Their cards are all on the table now, so "they had nothing" is a fact
    about the hand rather than a guess from a showdown sample."""
    c = Counter()
    sds, your_bets, their_bets = [], [], []
    for h in hands:
        seats = h["seats"]
        pre = [a for a in h["actions"] if a["seat"] == v and a["street"] == 0]
        c["dealt"] += 1
        c["vpip"] += any(a["kind"] in ("call", "raise") for a in pre)
        c["pfr"] += any(a["kind"] == "raise" for a in pre)
        both_in = (any(a["kind"] in ("call", "raise") for a in pre)
                   and any(a["kind"] in ("call", "raise") for a in _hero_pre(h, hero)))
        c["together"] += both_in
        lives = _live_before(h)
        for k, a in enumerate(h["actions"]):
            if a["street"] == 0 or hero not in lives[k] or v not in lives[k]:
                continue
            board = _board(h, a["street"])
            if a["seat"] == hero and a["kind"] == "raise":
                reply = _next_by(h, k, v)
                if reply:
                    your_bets.append((h["hand_no"], reply["kind"]))
            elif a["seat"] == v and a["kind"] == "raise":
                reply = _next_by(h, k, hero)
                if reply:
                    their_bets.append((h["hand_no"], reply["kind"],
                                       _category(seats[v]["hole"], board)))
        if h["showdown"] and not seats[v]["folded"] and not seats[hero]["folded"]:
            sds.append({"hand_no": h["hand_no"], "hole": seats[v]["hole"],
                        "board": h["board"], "made": _made(seats[v]["hole"], h["board"]),
                        "won": seats[v]["won"],
                        "category": _category(seats[v]["hole"], h["board"])})

    dealt = c["dealt"]
    do, dont = [], []
    folded_to_you = [hn for hn, kind in your_bets if kind == "fold"]
    if len(your_bets) >= MIN_CHANCES:
        rate = len(folded_to_you) / len(your_bets)
        if rate >= 0.6:
            do.append(_tip(f"Bet into them. They folded {len(folded_to_you)} of the "
                           f"{len(your_bets)} times you bet.", folded_to_you))
        elif rate <= 0.25:
            dont.append(_tip(
                f"Bluff them. They folded {len(folded_to_you)} of {len(your_bets)} "
                "bets -- bet the hands that want a call, bigger.",
                [hn for hn, kind in your_bets if kind != "fold"]))

    you_folded = [(hn, cat) for hn, kind, cat in their_bets if kind == "fold"]
    air = [hn for hn, cat in you_folded if cat == HIGH_CARD]
    if len(you_folded) >= 2 and len(air) * 2 >= len(you_folded):
        dont.append(_tip(
            f"Hand them their bluffs. {len(air)} of the {len(you_folded)} times you "
            "folded to their bet, they had no pair.", air))
        do.append(_tip("Call or raise their bets lighter -- the folds above were "
                       "the wrong ones.", air))
    elif len(their_bets) >= MIN_CHANCES:
        real = [hn for hn, _, cat in their_bets if cat > HIGH_CARD]
        if len(real) * 5 >= len(their_bets) * 4:
            do.append(_tip(
                f"Believe their bets. {len(real)} of {len(their_bets)} came with a "
                "pair or better -- fold the hands that only beat a bluff.", real))

    if dealt >= 15:
        vp = c["vpip"] / dealt
        if vp >= 0.5:
            do.append(_tip(
                f"Raise your good hands bigger preflop. They played {c['vpip']} of "
                f"{dealt} hands and will call.", []))
            dont.append(_tip("Try to push them off a pot preflop -- they do not fold "
                             "there.", []))
        elif vp <= 0.15:
            do.append(_tip(
                f"Steal their blinds, and give them credit when they enter. They played "
                f"{c['vpip']} of {dealt} hands.", []))

    weak = [s for s in sds if s["category"] <= 1]
    if len(sds) >= 2 and len(weak) * 2 >= len(sds):
        do.append(_tip(
            f"Value bet thinner. {len(weak)} of their {len(sds)} showdowns with you "
            "were one pair or worse.", [s["hand_no"] for s in weak]))

    return {
        "seat": v, "name": name,
        "net": sum(h["seats"][v]["net"] for h in hands),
        "net_bb": round(sum(h["seats"][v]["net"] for h in hands) / bb, 1),
        "dealt": dealt, "together": c["together"],
        "vpip": {"stat": "vpip", "hits": c["vpip"], "chances": dealt},
        "pfr": {"stat": "pfr", "hits": c["pfr"], "chances": dealt},
        "your_bets": {"n": len(your_bets), "folded": len(folded_to_you)},
        "their_bets": {"n": len(their_bets), "you_folded": len(you_folded),
                       "no_pair": sum(1 for *_, cat in their_bets if cat == HIGH_CARD)},
        "grid": _grid(hands, v),
        "showdowns": [{k: s[k] for k in ("hand_no", "hole", "made", "won")}
                      for s in sds][-6:],
        "do": do[:MAX_TIPS], "dont": dont[:MAX_TIPS],
    }


# -- key hands ----------------------------------------------------------------

FLAG_TEXT = {
    ("fold", False): "folded with the price",
    ("call", False): "paid off",
    ("bluff", True): "no-pair bet took it",
    ("bluff", False): "no-pair bet called",
    ("check", False): "checked the winner",
}


def _key_hands(hands, hero, graded, bb) -> list[dict]:
    """The hands worth opening again: every flagged one, then the biggest swings."""
    rows = []
    for h, marks in zip(hands, graded):
        flags = list(dict.fromkeys(FLAG_TEXT[(d["kind"], d["right"])] for d in marks
                                   if (d["kind"], d["right"]) in FLAG_TEXT))
        rows.append((h, flags))
    flagged = [r for r in rows if r[1]]
    swings = sorted(rows, key=lambda r: -abs(r[0]["net"]))
    chosen, seen = [], set()
    for h, flags in sorted(flagged, key=lambda r: -abs(r[0]["net"])) + swings:
        if h["hand_no"] in seen or (not flags and h["net"] == 0):
            continue
        seen.add(h["hand_no"])
        chosen.append((h, flags))
        if len(chosen) >= KEY_HANDS:
            break
    return [_detail(h, hero, bb, flags, log=False)
            for h, flags in sorted(chosen, key=lambda r: r[0]["hand_no"])]


def hand_detail(game, hand_no: int) -> dict | None:
    """One hand of the session, every card face up."""
    for h in game.hands:
        if h["hand_no"] == hand_no:
            marks = _grade(h, game.hero_seat)
            flags = list(dict.fromkeys(FLAG_TEXT[(d["kind"], d["right"])] for d in marks
                                       if (d["kind"], d["right"]) in FLAG_TEXT))
            return _detail(h, game.hero_seat, game.bb or 1, flags, log=True) | {
                "graded": marks}
    return None


def _detail(h, hero, bb, flags, log: bool) -> dict:
    me = h["seats"][hero]
    out = {
        "hand_no": h["hand_no"], "position": me["position"], "hole": me["hole"],
        "board": h["board"], "net": h["net"], "net_bb": round(h["net"] / bb, 1),
        "made": _made(me["hole"], h["board"]), "flags": flags,
        "seats": [{"name": s["name"], "position": s["position"], "hole": s["hole"],
                   "folded": s["folded"], "net": s["net"], "is_hero": i == hero}
                  for i, s in enumerate(h["seats"])],
    }
    if log:
        out["log"] = h["log"]
    return out
