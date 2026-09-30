"""One sitting, reviewed the way a coach reads it.

The rest of the tool is built on frequencies, which is right for a profile and
wrong for a sitting: two hundred hands put three or four hands in the pots
that decided the night, a few hundred preflop decisions in spots with known
right answers, and a handful of reads on each opponent that are true *tonight*
whether or not they match the season. This module assembles those:

* what to fix first, ranked, with the hands behind each item;
* the biggest pots, with all-in equity separating the cooler from the punt,
  and the decisions in them worth another look;
* your preflop graded spot by spot (:mod:`villain.charts`);
* how you answered bets by size, against the most a bet of that size lets you
  fold;
* the same measurements across your recent sittings, so a leak that keeps
  coming back reads differently from one bad night;
* per opponent, how they played tonight, where that differs from their usual
  game, and what to do about it.

Everything here is computed from the sitting's own hands. A tip always quotes
its count and its hands, because a tip you cannot check is a claim, and on a
couple of hundred hands a claim is mostly luck.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from statistics import median

from .analyze import enrich
from .archetypes import PLAN_PREMISE
from .charts import THREE_BET_BLUFFS, SpotReport, audit, flatted_bluffs
from .features import derived, record_hands
from .gto import targets_for
from .hero import hand_class
from .model import ACT_LABELS, Act, Hand, Street
from .priors import REGIME_LABELS, regime
from .stats import HandView

#: A shown hand this far up the board's holdings was a big hand.
STRONG_SHOWN = 0.8

#: How many of the biggest pots the review lists.
KEY_HANDS = 6

#: A hand this strong on the board -- the share of possible holdings it
#: beats -- is a made hand, not a bluff. Below it, a big bet is a bluff or a
#: thin value bet at best.
BLUFF_BELOW = 0.5

#: Between these a hand mostly beats bluffs: too strong to turn into a bluff
#: by raising, too weak to raise for value against a big bet.
CATCHER = (0.5, 0.8)

#: A bet this big relative to the pot it went into is a strong statement.
BIG_BET = 0.75

#: All-in with at least this much of the board beaten and under this much
#: equity is a cooler: the hand deserved the money, the cards did not.
COOLER_STRENGTH, COOLER_EQUITY = 0.9, 0.35

#: Postflop bet sizes, as a fraction of the pot the bet went into. The last
#: number of each band is the upper edge.
SIZE_BANDS = (("up to 1/3 pot", 0.34), ("1/3 to 1/2 pot", 0.51),
              ("1/2 to 3/4 pot", 0.76), ("3/4 pot to pot", 1.01),
              ("overbet", math.inf))

#: Folds are flagged only past the most the bet lets you fold by this much,
#: and only with enough bets faced to mean anything.
FOLD_SLACK, MIN_FACED = 0.10, 5

#: A preflop spot is flagged when your rate sits this far from the reference
#: and at least this many hands would have changed.
SPOT_GAP, MIN_SPOT_HANDS = 0.08, 3

#: An opponent needs this many hands tonight for a read at all.
MIN_OPPONENT_HANDS = 30

#: Tonight differs from usually when the gap is this big, this unlikely to be
#: chance (two-proportion z), and both sides have enough chances behind them.
CHANGE_GAP, CHANGE_Z, CHANGE_MIN_TONIGHT, CHANGE_MIN_USUAL = 0.10, 2.0, 15, 30

#: Statistics compared tonight against usually. Per-hand ones plus the
#: postflop reads a plan leans on hardest.
CHANGE_STATS = ("vpip", "pfr", "raise_share", "limp", "three_bet", "fold_to_three_bet",
                "bb_defend", "cbet:flop", "check_raise:flop", "fold_vs_bet:flop",
                "fold_vs_bet:turn", "fold_vs_bet:river", "wtsd")

#: Sittings looked back over for the trend, this one included.
TREND_SESSIONS = 5

#: A big-pot flag counts as one decision per this many big blinds you put in,
#: when fixes are ranked. Folding one 30bb pot too often and putting 190bb in
#: as a bluff are not the same size of mistake, and nothing else puts them on
#: one scale without inventing a price.
BB_PER_DECISION = 20


# -- small helpers ----------------------------------------------------------------

def _seat_of(hand: Hand, key: str):
    return next((s for s in hand.seats if s.player_id == key), None)


def _bb(chips: float, hand: Hand) -> float:
    return round(chips / (hand.big_blind or 1), 1)


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def _ref(hand: Hand, note: str = "") -> dict:
    return {"hand_id": hand.hand_id, "note": note}


def _faced_size(action) -> float:
    """The bet faced, as a fraction of the pot before it went in. The action
    records the pot *including* the bet, which is what a caller's price is
    read against and not what the bettor chose."""
    before = action.pot_before - action.to_call
    return action.to_call / before if before > 0 else 0.0


def _size_band(fraction: float) -> str:
    return next(label for label, top in SIZE_BANDS if fraction < top)


# -- the biggest pots ---------------------------------------------------------------

def _flags(hand: Hand, hero, names: dict[str, str]) -> list[dict]:
    """Decisions in this hand worth another look. Neutral by design: each one
    says what happened and against what, and leaves the verdict to the reader
    -- a rule can find the shape of a punt, not prove one."""
    strength = derived(hand, "strength")
    view = HandView(hand)
    out: list[dict] = []
    showed_strength: set[int] = set()     # opponents who raised, or called a raise, postflop
    raiser: dict[Street, int] = {}        # the last raise on each street, by seat
    for d in view.decisions():
        a = d.action
        if a.street is Street.PREFLOP:
            continue
        if a.act is Act.RAISE:
            raiser[a.street] = a.seat
        mine = a.seat == hero.seat
        if not mine:
            if a.act is Act.RAISE or (a.act is Act.CALL
                                      and raiser.get(a.street) == hero.seat):
                showed_strength.add(a.seat)
            continue
        pct = strength.get((hero.seat, a.street))
        if pct is None:
            continue
        street = a.street.label
        put_in = _bb(a.amount, hand)
        if a.act is Act.RAISE and a.street is Street.RIVER \
                and _faced_size(a) >= BIG_BET and CATCHER[0] <= pct < CATCHER[1]:
            out.append({"kind": "raised_catcher", "street": street, "bb": put_in,
                        "text": (f"Raised the river to {_bb(a.to_amount, hand)}bb into a "
                                 f"{_pct(_faced_size(a))}-pot bet with a hand that beat "
                                 f"{_pct(pct)} of possible holdings. A hand that mostly "
                                 f"beats bluffs gets called by the hands that beat it.")})
        elif a.act.is_aggressive and a.street >= Street.TURN and pct < BLUFF_BELOW \
                and a.pot_before and a.amount / a.pot_before >= 0.5:
            behind = [s for s in showed_strength if s not in view.folded_on
                      or view.folded_on[s] > a.street]
            if behind:
                who = ", ".join(names.get(hand.seat(s).player_id, hand.seat(s).name)
                                for s in behind)
                out.append({"kind": "bluff_into_strength", "street": street, "bb": put_in,
                            "text": (f"Put {put_in}bb in on the {street} with a hand that beat "
                                     f"{_pct(pct)} of possible holdings, after {who} had "
                                     f"already raised or called a raise. A range that has done "
                                     f"that folds far less than a bluff needs.")})
        elif a.act is Act.FOLD and pct >= COOLER_STRENGTH and d.facing_bet:
            out.append({"kind": "strong_fold", "street": street, "bb": 0.0,
                        "text": (f"Folded on the {street} with a hand that beat {_pct(pct)} of "
                                 f"possible holdings, to a {_pct(_faced_size(a))}-pot bet.")})
    return out


def _allin(hand: Hand, hero) -> dict | None:
    """Hero's all-in equity and what it was worth, when the money went in face
    up. The gap between what the pot was worth and what happened is luck."""
    table = derived(hand, "allin")
    if not table or hero.seat not in table:
        return None
    chips, equity = table[hero.seat]
    expected = _bb(chips - hero.invested, hand)
    strength = derived(hand, "strength")
    last = max((a.street for a in hand.actions if a.seat == hero.seat), default=Street.PREFLOP)
    pct = strength.get((hero.seat, last))
    cooler = pct is not None and pct >= COOLER_STRENGTH and equity < COOLER_EQUITY
    return {"equity": round(equity, 3), "expected_bb": expected,
            "luck_bb": round(_bb(hero.net, hand) - expected, 1), "cooler": cooler}


def key_hands(hands: list[Hand], hero_key: str | None, names: dict[str, str]) -> list[dict]:
    """The biggest pots of the sitting, whoever played them."""
    ranked = sorted(hands, key=lambda h: -h.pot / (h.big_blind or 1))[:KEY_HANDS]
    out = []
    for hand in sorted(ranked, key=lambda h: h.started_at):
        hero = _seat_of(hand, hero_key) if hero_key else None
        involved = [s for s in hand.seats
                    if any(a.seat == s.seat and a.act.is_voluntary and a.act is not Act.FOLD
                           for a in hand.actions)]
        out.append({
            "hand_id": hand.hand_id,
            "pot_bb": _bb(hand.pot, hand),
            "board": list(hand.board),
            "players": [{"name": names.get(s.player_id, s.name),
                         "is_hero": hero is not None and s.seat == hero.seat,
                         "cards": list(s.hole_cards) if (hero is not None and s.seat == hero.seat)
                         or s.showed else [],
                         "net_bb": _bb(s.net, hand)} for s in involved],
            "allin": _allin(hand, hero) if hero else None,
            "flags": _flags(hand, hero, names) if hero else [],
        })
    return out


# -- how you answered bets, by size ----------------------------------------------------

@dataclass
class SizeBand:
    label: str
    faced: int = 0
    folded: int = 0
    max_fold_total: float = 0.0
    folds: list[dict] = field(default_factory=list)

    @property
    def fold_rate(self) -> float:
        return self.folded / self.faced if self.faced else 0.0

    @property
    def max_fold(self) -> float:
        """The most a bet this size lets you fold before any two cards profit
        from betting it: bet / (pot + bet), averaged over the bets you faced."""
        return self.max_fold_total / self.faced if self.faced else 0.0

    @property
    def excess(self) -> float:
        return self.folded - self.max_fold_total

    @property
    def flagged(self) -> bool:
        return self.faced >= MIN_FACED and self.fold_rate > self.max_fold + FOLD_SLACK


def folds_by_size(hands: list[Hand], hero_key: str) -> list[SizeBand]:
    """Your first answer to a bet on each postflop street, heads-up only.

    Heads-up only because the price argument is: against two players, calling
    has to beat both ranges, and folding more than one bettor's price allows
    is correct. Raises faced are left out -- a raise is not a bet, and a fold
    to one is priced differently."""
    bands = {label: SizeBand(label) for label, _ in SIZE_BANDS}
    for hand in hands:
        hero = _seat_of(hand, hero_key)
        if hero is None:
            continue
        seen: set[Street] = set()
        for d in HandView(hand).decisions():
            a = d.action
            if (a.seat != hero.seat or a.street is Street.PREFLOP or not d.facing_bet
                    or d.aggression_level != 1 or d.players_in != 2 or a.street in seen):
                continue
            seen.add(a.street)
            size = _faced_size(a)
            band = bands[_size_band(size)]
            band.faced += 1
            band.max_fold_total += size / (1 + size)
            if a.act is Act.FOLD:
                band.folded += 1
                band.folds.append(_ref(hand, f"{a.street.label}, {_pct(size)} pot"))
    return [b for b in bands.values() if b.faced]


# -- preflop ----------------------------------------------------------------------------

def _decision_ref(d) -> dict:
    return {"hand_id": d.hand_id, "cls": d.cls, "action": d.action}


def _spot_flagged(r: SpotReport) -> bool:
    """Off the reference by enough, with enough named hands to show for it.
    A rate gap alone flagged spots where the cards dealt were simply bad: a
    tight small blind that was dealt 42o and 93o all night is not a leak."""
    return abs(r.gap) >= SPOT_GAP and len(
        r.misfolds if r.gap < 0 else r.loose) >= MIN_SPOT_HANDS


def _spot_json(r: SpotReport, limit: int | None = None) -> dict:
    """One spot for the page. ``limit`` keeps the most recent hands on each
    list: over a whole history a spot can hold hundreds of misfolds, and the
    count says that while the chips show the latest few."""
    def recent(decisions):
        ordered = sorted(decisions, key=lambda d: -d.started_at)
        return [_decision_ref(d) for d in (ordered[:limit] if limit else ordered)]
    return {"key": r.chart.key, "label": r.chart.label, "n": r.n,
            "played": r.played, "raised": r.raised, "called": r.called,
            "rate": round(r.rate, 3), "target": r.chart.target,
            "flagged": _spot_flagged(r),
            "misfold_count": len(r.misfolds), "loose_count": len(r.loose),
            "misfolds": recent(r.misfolds), "loose": recent(r.loose)}


def three_bets(reports: list[SpotReport], regime_key: str) -> dict:
    """How you used the 3-bet, and how you answered one.

    The frequency alone hid the finding that mattered: a normal 13% made of
    almost nothing but value, with the natural bluffs flatted instead."""
    facing = [r for r in reports if r.chart.plays == "continue" and r.chart.key != "vs3bet"]
    n = sum(r.n for r in facing)
    raised = [d for r in facing for d in r.decisions if d.action == "raise"]
    vs = next((r for r in reports if r.chart.key == "vs3bet"), None)
    targets = targets_for(regime_key)
    out = {
        "faced_opens": n,
        "three_bets": [_decision_ref(d) for d in raised],
        "rate": round(len(raised) / n, 3) if n else None,
        "target": targets.get("three_bet"),
        "bluffs": sum(d.cls in THREE_BET_BLUFFS for d in raised),
        "flatted_bluffs": [_decision_ref(d) for d in flatted_bluffs(reports)],
        "vs": None,
    }
    if vs is not None:
        weak_calls = [d for d in vs.decisions if d.action == "call"
                      and d.cls not in vs.chart.hands and d.cls.endswith("o")]
        out["vs"] = {
            "n": vs.n,
            "folded": sum(d.action == "fold" for d in vs.decisions),
            "called": vs.called, "four_bet": vs.raised,
            "fold_target": targets.get("fold_to_three_bet"),
            "four_bet_target": targets.get("four_bet"),
            "weak_calls": [_decision_ref(d) for d in weak_calls],
            "decisions": [_decision_ref(d) for d in vs.decisions],
        }
    return out


# -- what to fix first --------------------------------------------------------------------

def fixes(spots: list[dict], three: dict, sizes: list[SizeBand], keys: list[dict]) -> list[dict]:
    """Every flagged finding, most decisions touched first.

    Ranked by count rather than by a price: pricing a misfold needs the EV of
    the hand you did not play, which no hand history contains."""
    items = []
    for s in spots:
        if not s["flagged"]:
            continue
        tight = s["rate"] < s["target"]
        hands = s["misfolds"] if tight else s["loose"]
        items.append({
            "kind": "preflop_tight" if tight else "preflop_loose",
            "title": (f"Play more hands: {s['label'].lower()}" if tight
                      else f"Play fewer hands: {s['label'].lower()}"),
            "detail": (f"You played {_pct(s['rate'])} of {s['n']} hands here; the reference "
                       f"range plays about {_pct(s['target'])}."),
            "weight": len(hands), "hands": hands})
    if len(three["flatted_bluffs"]) >= 2:
        items.append({
            "kind": "flatted_bluffs",
            "title": "3-bet the hands you flatted that bluff best",
            "detail": (f"{len(three['three_bets'])} 3-bets, {three['bluffs']} of them bluffs; "
                       f"{len(three['flatted_bluffs'])} flats with suited wheel aces or suited "
                       f"kings, which are the natural bluffs."),
            "weight": len(three["flatted_bluffs"]), "hands": three["flatted_bluffs"]})
    vs = three.get("vs")
    if vs and len(vs["weak_calls"]) >= 2:
        items.append({
            "kind": "vs3bet_calls",
            "title": "Fold weak offsuit hands to 3-bets",
            "detail": (f"Called {len(vs['weak_calls'])} 3-bets with offsuit hands that play "
                       f"badly in a big pot."),
            "weight": len(vs["weak_calls"]), "hands": vs["weak_calls"]})
    for band in sizes:
        if band.flagged:
            items.append({
                "kind": "overfold",
                "title": f"Fold less to {band.label} bets",
                "detail": (f"Folded {band.folded} of {band.faced} ({_pct(band.fold_rate)}); a bet "
                           f"that size lets you fold at most about {_pct(band.max_fold)}."),
                "weight": round(band.excess), "hands": band.folds})
    for hand in keys:
        flags = [f for f in hand["flags"] if f["kind"] != "strong_fold"]
        if not flags:
            continue
        # One item per hand: a turn bluff and the river shove that followed it
        # are one line, and listing them apart ranked one punt as two.
        items.append({
            "kind": flags[0]["kind"],
            "title": ("Raised a hand that mostly beats bluffs"
                      if flags[0]["kind"] == "raised_catcher"
                      else "Big bluff into someone who had shown strength"),
            "detail": " ".join(f["text"] for f in flags),
            "weight": max(1, round(sum(f["bb"] for f in flags) / BB_PER_DECISION)),
            "hands": [{"hand_id": hand["hand_id"], "note": f"{hand['pot_bb']}bb pot"}]})
    items.sort(key=lambda x: -x["weight"])
    return items


# -- the same measurements, sitting after sitting -------------------------------------------

def trend(sittings: list[tuple[dict, list[Hand]]], hero_key: str) -> list[dict]:
    """Your flagged measurements across recent sittings, oldest first.

    One bad night is variance or a mood; the same gap four sittings running
    is how you play. Each row says which it is: ``persistent`` when it was off
    in the latest sitting and at least one before, ``new`` when only the latest,
    ``fixed`` when it was off before and is not now."""
    rows: dict[str, dict] = {}

    def put(key: str, label: str, idx: int, value: float, target: float, n: int, off: bool):
        row = rows.setdefault(key, {"key": key, "label": label, "target": target,
                                    "cells": [None] * len(sittings)})
        row["cells"][idx] = {"value": round(value, 3), "n": n, "off": off}

    for idx, (_, hands) in enumerate(sittings):
        for r in audit(hands, hero_key):
            if r.chart.key == "vs3bet" or r.n < 10:
                continue
            put(r.chart.key, r.chart.label, idx, r.rate, r.chart.target, r.n,
                _spot_flagged(r))
        small = [b for b in folds_by_size(hands, hero_key) if b.label in
                 (SIZE_BANDS[0][0], SIZE_BANDS[1][0])]
        faced = sum(b.faced for b in small)
        if faced >= MIN_FACED:
            folded = sum(b.folded for b in small)
            limit = sum(b.max_fold_total for b in small) / faced
            put("small_bets", "Folds to bets of half pot or less", idx, folded / faced,
                round(limit, 3), faced, folded / faced > limit + FOLD_SLACK)
    out = []
    for row in rows.values():
        cells = row["cells"]
        measured = [(i, c) for i, c in enumerate(cells) if c]
        if len(measured) < 2 and cells[-1] is None:
            continue
        latest = cells[-1]
        before = [c for c in cells[:-1] if c]
        if latest and latest["off"]:
            status = "persistent" if any(c["off"] for c in before) else "new"
        elif latest and any(c["off"] for c in before):
            status = "fixed"
        else:
            status = "fine" if latest else "not measured tonight"
        row["status"] = status
        out.append(row)
    order = {"persistent": 0, "new": 1, "fixed": 2, "fine": 3, "not measured tonight": 4}
    out.sort(key=lambda r: order[r["status"]])
    return out


# -- opponents -------------------------------------------------------------------------------

def _z(h1: float, n1: float, h2: float, n2: float) -> float:
    p = (h1 + h2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2)) if 0 < p < 1 else 0.0
    return abs(h1 / n1 - h2 / n2) / se if se else 0.0


def changes(tonight: dict, stored: dict) -> list[dict]:
    """Statistics where tonight and the rest of their history disagree by more
    than chance, compared inside one table size.

    The baseline is their *other* hands: a sitting compared with a total that
    contains it can only understate the difference."""
    out = []
    for reg, book in tonight.items():
        base = stored.get(reg)
        if base is None:
            continue
        for stat in CHANGE_STATS:
            now = book.ratios.get(stat)
            total = base.ratios.get(stat)
            if now is None or total is None or now.opps < CHANGE_MIN_TONIGHT:
                continue
            rest_hits, rest_opps = total.hits - now.hits, total.opps - now.opps
            if rest_opps < CHANGE_MIN_USUAL:
                continue
            a, b = now.hits / now.opps, rest_hits / rest_opps
            if abs(a - b) < CHANGE_GAP or _z(now.hits, now.opps, rest_hits, rest_opps) < CHANGE_Z:
                continue
            out.append({"stat": stat, "regime": reg,
                        "regime_label": REGIME_LABELS.get(reg, reg),
                        "tonight": round(a, 3), "usual": round(b, 3),
                        "tonight_n": round(now.opps), "usual_n": round(rest_opps)})
    out.sort(key=lambda c: -abs(c["tonight"] - c["usual"]))
    return out


def stale_read(archetype: str | None, moved: list[dict]) -> dict | None:
    """Whether tonight contradicts what the plan for their usual read assumes.

    A limper who stopped limping makes "raise their limps" advice for a player
    who is not at the table. Checked against the plan's premise, not the
    archetype's strongest matching trait -- those can differ, and the premise
    is what the advice actually stands on."""
    premise = PLAN_PREMISE.get(archetype or "")
    if not premise:
        return None
    for change in moved:
        direction = premise.get(change["stat"])
        if direction and (change["tonight"] - change["usual"]) * direction < 0:
            return {"archetype": archetype, "stat": change["stat"],
                    "tonight": change["tonight"], "usual": change["usual"]}
    return None


def _tips(hands: list[Hand], key: str) -> tuple[list[dict], dict]:
    """How to beat one player, from how they played these hands."""
    cbet = cbet_n = 0
    cbet_sizes: list[float] = []
    raised_after_bet: Counter = Counter()
    raised_refs: list[dict] = []
    reraise_shown: list[tuple[float, dict]] = []
    three = three_n = 0
    three_sizes: list[float] = []
    three_refs: list[dict] = []
    open_faced3 = open_folded3 = 0
    limp = first_in = 0
    river_big: list[tuple[float, dict]] = []
    river_small: list[tuple[float, dict]] = []
    river_faced = river_folded = 0
    for hand in hands:
        seat = _seat_of(hand, key)
        if seat is None:
            continue
        view = HandView(hand)
        sd = derived(hand, "sd")
        # preflop: first in, facing one raise, facing a 3-bet after opening
        raises = callers = 0
        open_to = 0
        opened = False
        acted = False
        for a in hand.actions:
            if a.street is not Street.PREFLOP:
                break
            if not a.act.is_voluntary:
                continue
            if a.seat == seat.seat:
                if not acted:
                    acted = True
                    if raises == 0 and callers == 0 and seat.position != "BB":
                        first_in += 1
                        limp += a.act is Act.CALL
                        if a.act.is_aggressive:
                            opened, raises, open_to = True, 1, a.to_amount
                        continue
                    if raises == 1 and callers == 0:
                        three_n += 1
                        if a.act.is_aggressive:
                            three += 1
                            three_sizes.append(a.to_amount / open_to if open_to else 0.0)
                            three_refs.append(_ref(
                                hand, f"to {a.to_amount / (open_to or 1):.1f}x"
                                + (f", {hand_class(seat.hole_cards)}"
                                   if seat.showed and len(seat.hole_cards) == 2 else "")))
                    break
                if opened and raises == 2:
                    open_faced3 += 1
                    open_folded3 += a.act is Act.FOLD
                break
            if a.act.is_aggressive:
                raises += 1
                if raises == 1:
                    open_to = a.to_amount
            elif a.act is Act.CALL:
                callers += 1
        # postflop
        last_aggressive: Street | None = None
        for d in view.decisions():
            a = d.action
            if a.street is Street.PREFLOP:
                continue
            if a.seat != seat.seat:
                continue
            if a.street is Street.FLOP and not d.facing_bet and d.has_initiative \
                    and d.aggression_level == 0:
                cbet_n += 1
                if a.act is Act.BET:
                    cbet += 1
                    cbet_sizes.append(d.bet_fraction)
            if d.facing_bet and last_aggressive is a.street:
                label = {Act.FOLD: "fold", Act.CALL: "call", Act.RAISE: "reraise"}.get(a.act)
                if label:
                    raised_after_bet[label] += 1
                    raised_refs.append(_ref(hand, f"{label} on the {a.street.label}"))
                    if label == "reraise" and seat.seat in sd:
                        reraise_shown.append((sd[seat.seat], _ref(
                            hand, f"{hand_class(seat.hole_cards)}, beat {_pct(sd[seat.seat])}")))
            if a.street is Street.RIVER and d.facing_bet and d.aggression_level == 1 \
                    and d.players_in == 2:
                river_faced += 1
                river_folded += a.act is Act.FOLD
            if a.act.is_aggressive:
                last_aggressive = a.street
                if a.street is Street.RIVER and seat.seat in sd and a.pot_before:
                    size = a.amount / a.pot_before
                    ref = _ref(hand, f"{hand_class(seat.hole_cards)}, "
                                     f"{_pct(size)} pot, beat {_pct(sd[seat.seat])}")
                    (river_big if size >= BIG_BET else river_small).append((sd[seat.seat], ref))

    tips: list[dict] = []
    folds_raised = raised_after_bet["fold"]
    raised_total = sum(raised_after_bet.values())
    if cbet_n >= 8 and cbet / cbet_n >= 0.7 and raised_total >= 4 \
            and folds_raised / raised_total >= 0.4:
        tips.append({"kind": "raise_their_bets",
                     "text": (f"Check-raise their flop bets. They bet {cbet} of {cbet_n} flops "
                              f"after raising preflop (median size {_pct(median(cbet_sizes))} pot) "
                              f"and folded {folds_raised} of {raised_total} times they were "
                              f"raised after betting."),
                     "hands": [r for r in raised_refs if r["note"].startswith("fold")]})
    if raised_after_bet["reraise"] >= 2 and reraise_shown \
            and all(p >= STRONG_SHOWN for p, _ in reraise_shown):
        tips.append({"kind": "believe_reraises",
                     "text": ("When they re-raise after being raised, fold unless you are "
                              "strong: every re-raise they showed down was a big hand."),
                     "hands": [r for _, r in reraise_shown]})
    if len(river_big) >= 2 and len(river_small) >= 2:
        big = sum(p for p, _ in river_big) / len(river_big)
        small = sum(p for p, _ in river_small) / len(river_small)
        if big - small >= 0.2:
            tips.append({"kind": "river_sizing",
                         "text": (f"Read their river size. Big river bets they showed down "
                                  f"beat {_pct(big)} of hands on average; small ones beat "
                                  f"{_pct(small)}. Fold to the big ones, call and raise the "
                                  f"small ones more."),
                         "hands": [r for _, r in river_big + river_small]})
    if three_n >= 20 and three / three_n >= 0.12:
        size = f", to {median(three_sizes):.1f}x the open" if three_sizes else ""
        tips.append({"kind": "light_three_bets",
                     "text": (f"They 3-bet {three} of {three_n} opens ({_pct(three / three_n)}"
                              f"{size}). That is light: call more in position and 4-bet some "
                              f"bluffs with suited aces."),
                     "hands": three_refs})
    if open_faced3 >= 5 and open_folded3 / open_faced3 >= 0.6:
        tips.append({"kind": "fold_to_three_bet",
                     "text": (f"3-bet them more: they folded {open_folded3} of {open_faced3} "
                              f"opens that got 3-bet."), "hands": []})
    if first_in >= 15 and limp / first_in >= 0.2:
        tips.append({"kind": "limps",
                     "text": (f"Raise their limps: they limped {limp} of {first_in} times they "
                              f"were first in."), "hands": []})
    if river_faced >= 6 and river_folded / river_faced <= 0.2:
        tips.append({"kind": "calls_rivers",
                     "text": (f"Value-bet thinner and stop bluffing rivers: they folded "
                              f"{river_folded} of {river_faced} river bets."), "hands": []})
    numbers = {
        "cbet_flop": (cbet, cbet_n),
        "cbet_size": round(median(cbet_sizes), 3) if cbet_sizes else None,
        "folds_when_raised": (folds_raised, raised_total),
        "three_bet": (three, three_n),
        "fold_to_three_bet": (open_folded3, open_faced3),
        "limp": (limp, first_in),
        "river_fold": (river_folded, river_faced),
    }
    return tips, numbers


def opponent(hands: list[Hand], key: str, name: str, tonight_books: dict,
             stored_books: dict, usual_archetype: str | None) -> dict:
    tips, numbers = _tips(hands, key)
    moved = changes(tonight_books, stored_books)
    return {"player_id": int(key), "name": name,
            "numbers": {k: list(v) if isinstance(v, tuple) else v for k, v in numbers.items()},
            "tips": tips, "changes": moved, "usual_archetype": usual_archetype,
            "stale": stale_read(usual_archetype, moved)}


# -- the export for a conversation --------------------------------------------------------

def export_text(hands: list[Hand], names: dict[str, str], hero_key: str | None) -> str:
    """The sitting as compact text, for pasting into a chat with an assistant.

    Hand-by-hand reasoning -- "this card completes their draws" -- is the part
    a rule cannot do well. Rather than send hands anywhere, the app hands you
    this and you choose where it goes. Amounts are in big blinds so the reader
    does not need the stakes."""
    lines = [f"# {len(hands)} hands. Amounts in big blinds. "
             + (f"Hero is {names.get(hero_key, hero_key)}; hero's cards are always shown."
                if hero_key else "")]
    for hand in hands:
        bb = hand.big_blind or 1
        seats = ", ".join(
            f"{s.position} {names.get(s.player_id, s.name)} {s.stack / bb:.0f}"
            + (f" [{' '.join(s.hole_cards)}]" if s.hole_cards and (
                s.player_id == hero_key or s.showed) else "")
            for s in hand.seats)
        lines.append(f"\n#{hand.hand_id} ({seats})")
        street = None
        for a in hand.actions:
            if a.act.is_post:
                continue
            if a.street is not street:
                street = a.street
                if street is not Street.PREFLOP:
                    lines.append(f"  {street.label}: {' '.join(hand.board_at(street))}")
            who = next((names.get(s.player_id, s.name) for s in hand.seats
                        if s.seat == a.seat), str(a.seat))
            amount = f" {a.to_amount / bb:g}" if a.act in (Act.BET, Act.RAISE) else (
                f" {a.amount / bb:g}" if a.act is Act.CALL else "")
            lines.append(f"  {who} {ACT_LABELS[a.act]}{amount}")
        results = ", ".join(f"{names.get(s.player_id, s.name)} {s.net / bb:+g}"
                            for s in hand.seats if s.net)
        lines.append(f"  result: {results}")
    return "\n".join(lines) + "\n"


# -- the whole sitting ----------------------------------------------------------------------

def _stakes(hands: list[Hand]) -> list[dict]:
    seen = Counter((h.small_blind, h.big_blind, h.unit) for h in hands)
    return [{"sb": sb, "bb": bb, "unit": unit, "hands": n}
            for (sb, bb, unit), n in seen.most_common()]


def session_review(store, session: dict, hero_id: int | None) -> dict:
    """The review of one sitting. Reads the store, never writes to it."""
    hands = store.session_hands(session["hand_ids"])
    tonight = record_hands(hands)
    players = store.session_detail(session, books=tonight)
    names = {str(r["id"]): r["display_name"] for r in store.players()}
    hero_key = str(hero_id) if hero_id is not None else None
    if hero_key and not any(_seat_of(h, hero_key) for h in hands):
        hero_key = None
    out: dict = {"stakes": _stakes(hands), "hero_seated": hero_key is not None,
                 "key_hands": key_hands(hands, hero_key, names)}
    if hero_key:
        mine = [h for h in hands if _seat_of(h, hero_key)]
        common = Counter(regime(len(h.seats)) for h in mine).most_common(1)[0][0]
        reports = audit(mine, hero_key)
        spots = [_spot_json(r) for r in reports if r.chart.key != "vs3bet"]
        three = three_bets(reports, common)
        sizes = folds_by_size(mine, hero_key)
        out["preflop"] = spots
        out["three_bets"] = three
        out["folds_by_size"] = [
            {"label": b.label, "faced": b.faced, "folded": b.folded,
             "fold_rate": round(b.fold_rate, 3), "max_fold": round(b.max_fold, 3),
             "flagged": b.flagged, "folds": b.folds} for b in sizes]
        out["fixes"] = fixes(spots, three, sizes, out["key_hands"])
        played = store.player_hand_ids(hero_id)
        earlier = sorted((s for s in store.sessions()
                          if s["started_at"] < session["started_at"]
                          and not played.isdisjoint(s["hand_ids"])),
                         key=lambda s: s["started_at"])[-(TREND_SESSIONS - 1):]
        sittings = [(s, store.session_hands(s["hand_ids"])) for s in earlier]
        sittings.append((session, hands))
        out["trend"] = {"sessions": [{"id": s["id"], "started_at": s["started_at"],
                                      "hands": s["hands"]} for s, _ in sittings],
                        "rows": trend(sittings, hero_key)}
    opponents = []
    for p in players:
        key = str(p["player_id"])
        if key == hero_key or p["hands"] < MIN_OPPONENT_HANDS:
            continue
        profile = store.profile(p["player_id"])
        if profile is not None:
            enrich(profile)
        opponents.append(opponent(
            hands, key, p["name"], tonight.get(key, {}), store.books(p["player_id"]),
            profile.archetype if profile else None))
    out["opponents"] = opponents
    for p in players:
        p["is_hero"] = str(p["player_id"]) == hero_key
    # Ranked on the sitting's own read, provisional or not: the page says how
    # sure each number is, and an unordered table answers nothing.
    out["players"] = sorted(players, key=lambda p: -p["skill_score"])
    return out


def recent_read(store, player_id: int) -> dict | None:
    """How to beat a player on the evidence of the last sitting they played.

    Their profile's plan comes from their whole history, which is what goes
    stale: a regular who changed their game last month is still described by
    the two years before it. The last sitting is the freshest honest sample,
    and the review already knows how to read one."""
    played = store.player_hand_ids(player_id)
    if not played:
        return None
    last = max((s for s in store.sessions() if not played.isdisjoint(s["hand_ids"])),
               key=lambda s: s["started_at"], default=None)
    if last is None:
        return None
    hands = store.session_hands(last["hand_ids"])
    key = str(player_id)
    mine = sum(1 for h in hands if _seat_of(h, key))
    if mine < MIN_OPPONENT_HANDS:
        return None
    names = {str(r["id"]): r["display_name"] for r in store.players()}
    profile = store.profile(player_id)
    if profile is not None:
        enrich(profile)
    read = opponent(hands, key, names.get(key, key), record_hands(hands).get(key, {}),
                    store.books(player_id), profile.archetype if profile else None)
    return read | {"session_id": last["id"], "started_at": last["started_at"], "hands": mine}
