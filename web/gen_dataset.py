#!/usr/bin/env python3
"""Generate the preloaded demo database: all ten archetypes, playing real hands.

The sample that ships with the repo is twenty heads-up hands -- enough to prove
the parser, too thin to show what the tool does. This plays a table of villains,
each driven by its archetype's own measured frequencies through the very engine
the practice simulator uses (:mod:`villain.holdem` + :mod:`villain.botplay`), and
records every hand in canonical form. So the demo opens on a full roster -- a
nit, a station, a maniac and the rest -- with priced leaks, a skill ranking, and
a hero to read, none of it hand-authored.

Deterministic: one fixed seed, so the demo is the same every build. The players
are fictional (a house rule of this repo -- see CONTRIBUTING.md); none maps to a
real person, they are personas named for nothing in particular.
"""

from __future__ import annotations

import numpy as np

from villain.archetypes import ARCHETYPE_BY_NAME, target_frequency
from villain.botplay import decide
from villain.cards import card_text
from villain.holdem import Hand as SimHand
from villain.holdem import Seat as SimSeat
from villain.model import Act, Action, Hand, Seat, Street, positions_for
from villain.profile import PROFILE_FEATURES, build_profile
from villain.stats import StatBook

SITE = "pokernow"
START_STACK_BB = 100
SB, BB = 1, 2
START_STACK = START_STACK_BB * BB

#: Fictional handles, one per archetype. Named for nothing; see the module docstring.
HANDLES = {
    "hero": "Ada",
    "nit": "Boaz", "station": "Cleo", "overfolder": "Dex", "maniac": "Esme",
    "lag": "Fitz", "tag": "Goro", "tight passive": "Hana",
    "loose passive": "Ivo", "limper": "Juno", "trapper": "Kai",
}


#: Open-raise-first-in rate per archetype. The single biggest preflop
#: differentiator, and ``rfi`` is *not* a profile feature -- so the policy in
#: :mod:`villain.botplay` falls back to one flat rate for everyone unless it is
#: supplied. Injecting it (below) is what makes a nit fold to the button and a
#: maniac open it, instead of every seat playing the same 22%.
ENTRY_RFI = {
    "nit": 0.12, "station": 0.30, "overfolder": 0.18, "maniac": 0.55,
    "lag": 0.34, "tag": 0.22, "tight passive": 0.14, "loose passive": 0.40,
    "limper": 0.12, "trapper": 0.17,
}


def _profile(archetype: str, regime: str, seed: int):
    """A profile that plays like ``archetype`` at ``regime``.

    Same construction the test-suite uses for archetype fixtures -- freeze each
    feature at the archetype's target frequency over a healthy sample -- plus
    an injected ``rfi`` the policy needs but the feature set does not carry.
    """
    arch = ARCHETYPE_BY_NAME[archetype]
    book = StatBook(player_id=archetype, name=archetype, regime=regime, hands=600)
    for feature in PROFILE_FEATURES:
        p = target_frequency(arch, feature, regime)
        book.ratios[feature].hits = round(p * 200)
        book.ratios[feature].opps = 200
    # rfi is not a profile feature, but build_profile carries any ratio in the
    # book -- so adding it here yields a proper shrunk estimate the policy can
    # read, with enough opps that it lands on the archetype's rate not the prior.
    rfi = ENTRY_RFI.get(archetype, 0.22)
    book.ratios["rfi"].hits = round(rfi * 400)
    book.ratios["rfi"].opps = 400
    size = {"hu": 2, "3max": 3, "6max": 6, "full": 9}[regime]
    book.meters["table_size"].add(size, 1)
    return build_profile(book)


def _regime(n: int) -> str:
    return {2: "hu", 3: "3max"}.get(n, "6max" if n <= 6 else "full")


def _play_hand(players: list[dict], button: int, rng, hand_id: str,
               started_at: int, table_id: str, hero_seat: int) -> Hand:
    """Play one hand to completion and record it as a canonical :class:`Hand`.

    The engine resets a seat's per-street commitment when a betting round
    closes, so every action's size is read from the pre-action state and the
    action it is about to take -- never from the seat afterward.
    """
    n = len(players)
    regime = _regime(n)
    profiles = [_profile(p["archetype"], regime, seed=i) for i, p in enumerate(players)]
    sim = SimHand([SimSeat(p["name"], START_STACK) for p in players], button, SB, BB, rng)

    actions: list[Action] = []
    if n == 2:
        sb_seat, bb_seat = button, (button + 1) % n
    else:
        sb_seat = (button + 1) % n
        bb_seat = (sb_seat + 1) % n
    actions.append(Action(Street.PREFLOP, sb_seat, Act.POST_SB, amount=SB, to_amount=SB))
    actions.append(Action(Street.PREFLOP, bb_seat, Act.POST_BB, amount=BB, to_amount=BB, pot_before=SB))

    while not sim.over and sim.to_act is not None:
        seat = sim.to_act
        s = sim.seats[seat]
        street = sim.street
        bet_before = sim.bet
        put_before = s.street_put
        pot_before = sim.pot
        to_call = max(0, sim.bet - put_before)
        lg = sim.legal()
        kind, amount, _reason = decide(sim, seat, profiles[seat], rng, players[seat]["name"])

        if kind == "fold":
            act, added, to_amt = Act.FOLD, 0, put_before
        elif kind == "check":
            act, added, to_amt = Act.CHECK, 0, put_before
        elif kind == "call":
            act, added, to_amt = Act.CALL, lg.call_amount, put_before + lg.call_amount
        else:                                    # a bet opens an unbet pot; else a raise
            act = Act.RAISE if bet_before > 0 else Act.BET
            to_amt, added = amount, amount - put_before

        think_ms = int(abs(rng.normal(1600, 1100))) + 250
        sim.act(kind, amount)
        actions.append(Action(street=Street(street), seat=seat, act=act, amount=added,
                              to_amount=to_amt, all_in=sim.seats[seat].all_in,
                              at=started_at + len(actions) * 1000,
                              think_ms=think_ms, pot_before=pot_before, to_call=to_call))

    live = [i for i, s in enumerate(sim.seats) if not s.folded]
    showdown = len(live) > 1
    winners = sim.winners or {}
    positions = positions_for(list(range(n)), button)

    seats: list[Seat] = []
    for i, sim_s in enumerate(sim.seats):
        is_hero = i == hero_seat
        shown = showdown and not sim_s.folded
        reveal = is_hero or shown
        hole = tuple(card_text(c) for c in sim_s.hole)
        seats.append(Seat(
            seat=i, player_id=players[i]["account"], name=players[i]["name"],
            stack=START_STACK, position=positions.get(i, "?"),
            hole_cards=hole if reveal else (),
            revealed=hole if shown else (),
            invested=sim_s.hand_put, won=winners.get(i, 0), showed=shown))

    return Hand(
        hand_id=hand_id, site=SITE, table_id=table_id, started_at=started_at,
        big_blind=BB, small_blind=SB, unit="chips", seats=seats, actions=actions,
        board=[card_text(c) for c in sim.board], pot=sum(s.hand_put for s in sim.seats),
        winners=[i for i, w in winners.items() if w > 0], hero_seat=hero_seat)


#: Each session: the villains at the table with the hero (always seat 0), the
#: table size implied by the count, and how many hands to deal. Between them the
#: ten archetypes are all represented; the heads-up sitting gives the hero an
#: against-you read and one villain a second, cross-regime book.
SESSIONS = [
    (["nit", "station", "maniac", "lag", "trapper"], 170),
    (["overfolder", "tight passive", "loose passive", "limper", "tag"], 170),
    (["maniac"], 150),
]
DAY_MS = 24 * 60 * 60 * 1000


def generate() -> list[Hand]:
    """Every hand of the demo database, deterministic under a fixed seed."""
    rng = np.random.default_rng(20260820)
    hands: list[Hand] = []
    t0 = 1_700_000_000_000                       # a fixed wall-clock origin
    for si, (villains, count) in enumerate(SESSIONS):
        players = [{"archetype": "tag", "account": "hero", "name": HANDLES["hero"]}]
        for arch in villains:
            players.append({"archetype": arch, "account": arch, "name": HANDLES[arch]})
        n = len(players)
        base = t0 + si * DAY_MS
        for h in range(count):
            hands.append(_play_hand(
                players, button=h % n, rng=rng,
                hand_id=f"demo-{si}-{h:04d}", started_at=base + h * 90_000,
                table_id=f"demo-table-{si}", hero_seat=0))
    return hands


def build_demo_db(db_path) -> dict:
    """Store the generated hands and build every profile, the real import way."""
    from villain.db import ImportReport, Store

    hands = generate()
    report = ImportReport()
    with Store(db_path) as store:
        store.add_hands(hands, report, defer_rebuild=True)
        store.rebuild_pending()
        store.fit_priors()
    return {"hands": len(hands), "players": len({s.player_id for h in hands for s in h.seats})}


if __name__ == "__main__":
    import sys
    from pathlib import Path
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "villain-demo.db")
    print(build_demo_db(out), "->", out)
