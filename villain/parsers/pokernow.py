"""PokerNow JSON export parser (``handVersion`` 2).

PokerNow ships hands as an event log with numeric opcodes and no schema. The
mapping below was recovered by replaying exports and checking that the money
balances: every hand must satisfy ``sum(contributions) - returned == sum(pot
awards)``, and any hand that does not is flagged rather than silently averaged
into somebody's stats.

Opcodes, as decoded:

===== ==========================================================
Code   Meaning
===== ==========================================================
0      check
2      post big blind (``value``)
3      post small blind (``value``)
7      call (``value`` = street-cumulative wager)
8      bet or raise (``value`` = street-cumulative, ``allIn`` flag)
9      board cards (``turn`` 1/2/3 = flop/turn/river, ``run`` for run-it-twice)
10     pot award (``value``, plus showdown ``cards``/``combination``)
11     fold
12     show cards
14     run-it-twice approval (ignored)
15     end of betting / showdown marker
16     uncalled bet returned (``value``)
===== ==========================================================

The two subtleties that matter downstream. First, ``value`` on a bet or call is
the player's *cumulative* wager for the street, not the increment -- a raise to
60 over a bet of 20 reports 60, and the caller who already put in 20 also
reports 60. Second, opcode 8 covers both bets and raises; which one it is
depends on whether anything is already wagered on the street, so an open raise
preflop is a raise (there are blinds out) while a flop lead is a bet.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..model import Act, Action, Hand, Seat, Street, positions_for
from .base import register

CHECK, POST_BB, POST_SB = 0, 2, 3
#: A straddle: a voluntary blind from the seat after the big blind, posted
#: before any cards are seen. Always twice the big blind, and always the seat
#: after the one that posted it -- which is how it was identified, the site
#: documenting none of these codes.
STRADDLE = 6
CALL, AGGRESS, BOARD, AWARD, FOLD = 7, 8, 9, 10, 11
SHOW, SHOWDOWN, RETURN = 12, 15, 16
#: Run-it-twice approval — no money or cards change hands.
RUN_IT_TWICE = 14

# Opcodes that carry no state we model. Anything outside this set and the ones
# handled below is recorded on the hand as an ``unknown_event`` flag.
IGNORED = {SHOWDOWN, RUN_IT_TWICE}

_STREET_BY_TURN = {1: Street.FLOP, 2: Street.TURN, 3: Street.RIVER}

_RANKS, _SUITS = set("23456789TJQKA"), set("cdhs")


def _cards(raw) -> tuple[str, ...]:
    """Well-formed cards only.

    PokerNow writes ``None`` in place of a card the player kept hidden, which
    happens when somebody shows just one card at the end of a hand. That is
    information rather than corruption -- the reveal is recorded, but a partial
    hand can never be evaluated, so it is never treated as a holding."""
    if not raw:
        return ()
    return tuple(c for c in raw
                 if isinstance(c, str) and len(c) == 2
                 and c[0].upper() in _RANKS and c[1].lower() in _SUITS)


def sniff(path: Path) -> bool:
    if path.suffix.lower() != ".json":
        return False
    with path.open("rb") as fh:
        head = fh.read(4096)
    return b'"hands"' in head and (b'"handVersion"' in head or b'"gameId"' in head)


def parse(path: Path) -> Iterator[Hand]:
    payload = json.loads(Path(path).read_text())
    table_id = payload.get("gameId") or Path(path).stem
    # Whoever pressed the export button. PokerNow names them outright, which
    # saves inferring it from whose cards are visible.
    exporter = payload.get("playerId")
    for raw in payload.get("hands", []):
        hand = _parse_hand(raw, table_id, exporter)
        if hand is not None:
            yield hand


def _parse_hand(raw: dict[str, Any], table_id: str,
                exporter: str | None = None) -> Hand | None:
    players = raw.get("players") or []
    if len(players) < 2:
        return None   # a hand that never started

    hand = Hand(
        hand_id=raw["id"],
        site="pokernow",
        table_id=table_id,
        started_at=raw["startedAt"],
        big_blind=int(raw["bigBlind"]),
        small_blind=int(raw["smallBlind"]),
        ante=int(raw.get("ante") or 0),
        game="nlhe" if raw.get("gameType") == "th" else str(raw.get("gameType")),
        unit="cents" if raw.get("cents") else "chips",
    )
    if raw.get("bombPot"):
        hand.flags.add("bomb_pot")
    if raw.get("straddleSeat"):
        hand.flags.add("straddle")
    if raw.get("doubleBoard"):
        hand.flags.add("double_board")

    seats = [
        Seat(
            seat=int(p["seat"]),
            player_id=str(p["id"]),
            name=p.get("name") or str(p["id"]),
            stack=int(p.get("stack") or 0),
            hole_cards=_cards(p.get("hand")),
        )
        for p in players
    ]
    hand.seats = seats
    by_seat = {s.seat: s for s in seats}
    hand.seats.sort(key=lambda s: s.seat)
    if exporter is not None:
        hand.hero_seat = next(
            (s.seat for s in seats if s.player_id == str(exporter)), None)

    pos = positions_for([s.seat for s in seats], int(raw["dealerSeat"]))
    for s in seats:
        s.position = pos.get(s.seat, "?")

    _replay(hand, raw.get("events") or [], by_seat)
    return hand


def _replay(hand: Hand, events: list[dict[str, Any]], by_seat: dict[int, Seat]) -> None:
    """Walk the event log, resolving pot and street state as we go."""
    street = Street.PREFLOP
    street_wager: dict[int, int] = {}      # seat -> cumulative wager this street
    # Seeded with the ante, which every seat posts before a card is dealt. It
    # was previously counted in ``pot_before`` but never in ``committed``, so
    # ``hand.pot`` came up short by exactly the antes while ``awarded``
    # included them -- every hand of an ante game failed the balance check and
    # was dropped, silently, for every player at the table.
    committed: dict[int, int] = dict.fromkeys(by_seat, hand.ante) if hand.ante else {}
    returned = 0
    awarded = 0
    prev_at: int | None = None
    runs_seen: set[int] = set()

    for ev in events:
        p = ev["payload"]
        code = p["type"]
        at = ev.get("at")
        seat = p.get("seat")

        if code == BOARD:
            run = int(p.get("run", 1))
            runs_seen.add(run)
            if run == 1:
                hand.board.extend(_cards(p.get("cards")))
            street = _STREET_BY_TURN.get(int(p.get("turn", 1)), street)
            # New street: prior wagers become committed, wagers reset.
            for s, v in street_wager.items():
                committed[s] = committed.get(s, 0) + v
            street_wager = {}
            prev_at = at
            continue

        if code == AWARD:
            value = int(p.get("value") or 0)
            awarded += value
            # Awards occasionally name a seat that is not in the seating chart
            # (busted players, late joins). Count the money either way; only
            # attach it to a seat we know.
            if seat is not None and seat in by_seat:
                by_seat[seat].won += value
                if seat not in hand.winners:
                    hand.winners.append(seat)
                if p.get("cards"):
                    shown = _cards(p.get("cards"))
                    by_seat[seat].showed = True
                    by_seat[seat].revealed = shown
                    if len(shown) == 2 and not by_seat[seat].hole_cards:
                        by_seat[seat].hole_cards = shown
            continue

        if code == SHOW:
            if seat in by_seat:
                shown = _cards(p.get("cards"))
                by_seat[seat].showed = True
                by_seat[seat].revealed = shown or by_seat[seat].revealed
                if len(shown) == 2 and not by_seat[seat].hole_cards:
                    by_seat[seat].hole_cards = shown
            continue

        if code == RETURN:
            value = int(p.get("value") or 0)
            returned += value
            if seat in by_seat:
                # An uncalled bet never really went in; unwind it so invested
                # and net are the amounts that were actually at risk.
                street_wager[seat] = street_wager.get(seat, 0) - value
            continue

        if code in IGNORED:
            continue

        act = _ACTS.get(code)
        if act is None:
            hand.flags.add(f"unknown_event:{code}")
            continue

        prior = street_wager.get(seat, 0)
        street_max = max(street_wager.values(), default=0)
        pot_before = sum(committed.values()) + sum(street_wager.values())

        if act is Act.FOLD or act is Act.CHECK:
            to_amount, amount = prior, 0
        else:
            to_amount = int(p.get("value") or 0)
            amount = to_amount - prior
            if code == AGGRESS:
                act = Act.RAISE if street_max > 0 else Act.BET

        action = Action(
            street=street,
            seat=seat,
            act=act,
            amount=amount,
            to_amount=to_amount,
            all_in=bool(p.get("allIn")),
            at=at,
            think_ms=(at - prev_at) if (at is not None and prev_at is not None) else None,
            pot_before=pot_before,
            to_call=max(0, street_max - prior),
        )
        hand.actions.append(action)
        if amount:
            street_wager[seat] = to_amount
        elif seat not in street_wager:
            street_wager[seat] = prior
        prev_at = at

    for s, v in street_wager.items():
        committed[s] = committed.get(s, 0) + v
    for seat, total in committed.items():
        if seat in by_seat:
            by_seat[seat].invested = total

    hand.pot = sum(committed.values())
    hand.run_count = max(len(runs_seen), 1)
    if awarded and hand.pot and awarded != hand.pot:
        hand.rake = hand.pot - awarded
        if hand.rake > 0:
            # Less was paid out than went in: rake, which is expected and
            # explains itself. It makes the money figures for this hand
            # slightly optimistic, but it says nothing about who folded to
            # what -- and dropping the hand outright, as a ``pot_mismatch``
            # did, discarded every behavioral statistic in a raked game.
            hand.flags.add("raked")
        elif hand.rake < 0:
            # More was paid out than went in. That cannot happen at a real
            # table, so the decode is wrong and the hand is not trustworthy.
            hand.flags.add("pot_mismatch")


_ACTS = {
    CHECK: Act.CHECK,
    FOLD: Act.FOLD,
    CALL: Act.CALL,
    AGGRESS: Act.BET,      # refined to RAISE during replay
    POST_SB: Act.POST_SB,
    POST_BB: Act.POST_BB,
    # Chips, not just a flag. The hand already knew a straddle had happened --
    # `straddleSeat` in the metadata sets a flag -- but the event carrying the
    # money was unrecognised and skipped, so the pot came up short by the
    # straddle while the awards did not. Less went in than came out, which
    # cannot happen at a real table, so every straddled hand was decoded as
    # untrustworthy and dropped: 4.64% of a real 71,456-hand database.
    #
    # A post, not a raise. It is money in before cards, so it must not count
    # as a voluntary preflop action -- Act.is_post already draws that line.
    STRADDLE: Act.POST_STRADDLE,
}

register("pokernow", sniff, parse)
