"""The hands behind a number.

An exploit says "folds too often to river bets, seen 16 times". The obvious
next question is *which* sixteen, and until you can answer it the number is
something to be trusted rather than something to be checked. That is a bad
position for a tool whose whole job is to tell you things you cannot verify at
the table.

Nothing is stored to make this work. Hands are already the source of truth in
:mod:`villain.db`, and a statistic's definition already lives in exactly one
place, so the contributing hands are found by replaying each hand on its own
through the same extraction the statistics use and asking which ones moved the
counter. That means the evidence can never drift from the number: change a
definition and both change together, because they are the same code.

The cost is a re-extraction per hand, which for the few hundred hands a real
player has is a few milliseconds and not worth a schema for.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .features import _pace_thresholds, _think_pass, record_hand
from .hero import hero_of
from .model import Act, Hand, Street

#: Which street a statistic is about, read off its name.
_STREETS = {"flop": Street.FLOP, "turn": Street.TURN, "river": Street.RIVER}


@dataclass
class Evidence:
    hand_id: str
    started_at: int
    hit: bool                 # did this hand count toward the numerator
    regime: str
    street: str
    summary: str              # what the player actually did
    board: list[str] = field(default_factory=list)
    hole_cards: list[str] = field(default_factory=list)
    net_bb: float = 0.0
    pot_bb: float = 0.0


def street_of(stat: str) -> Street | None:
    for name, street in _STREETS.items():
        if stat.endswith(f":{name}") or f":{name}:" in stat:
            return street
    return None


def find(hands: list[Hand], player_key: str, stat: str,
        limit: int | None = None) -> list[Evidence]:
    """Every hand that moved ``stat`` for this player, most recent first.

    A hand counts when it contributed to the statistic's *denominator* -- the
    opportunity -- and is marked ``hit`` when it also moved the numerator. Both
    matter: sixteen fold-to-river-bet opportunities of which nine were folds is
    the whole picture, and showing only the folds would misrepresent it."""
    # Freeze the snap/tank cutoffs over the whole hand list, exactly as
    # record_hands does when the profile is built. Replaying one hand at a
    # time leaves each hand below MIN_PACE_SAMPLES, so _pace_thresholds falls
    # back to absolute cutoffs and the evidence panel disagrees with the very
    # number it is opened to explain -- a player whose own mean is 20s had
    # every fold shown as a "tank fold" against a profile that counted none.
    scratch: dict = {}
    for hand in hands:
        _think_pass(hand, scratch)
    locks: dict = {}
    for pid, by_regime in scratch.items():
        for reg, book in by_regime.items():
            locks[(pid, reg)] = _pace_thresholds(book)

    # Resolved over the whole list for the same reason the cutoffs are: one
    # hand cannot say who exported it, and without the answer a vs: statistic
    # would open onto an empty panel rather than the hands behind it.
    hero = hero_of(hands)

    out: list[Evidence] = []
    for hand in hands:
        books: dict = {}
        record_hand(hand, books, pace_locks=locks, hero=hero)
        by_regime = books.get(player_key)
        if not by_regime:
            continue
        for regime, book in by_regime.items():
            ratio = book.ratios.get(stat)
            if not ratio or ratio.opps <= 0:
                continue
            seat = _seat_of(hand, player_key)
            if seat is None:
                continue
            street = street_of(stat)
            out.append(Evidence(
                hand_id=hand.hand_id,
                started_at=hand.started_at,
                hit=ratio.hits > 0,
                regime=regime,
                street=street.label if street else "preflop",
                summary=describe(hand, seat.seat, street),
                board=list(hand.board_at(street)) if street else list(hand.board),
                hole_cards=list(seat.hole_cards),
                net_bb=round(seat.net / hand.big_blind, 2) if hand.big_blind else 0.0,
                pot_bb=round(hand.pot / hand.big_blind, 1) if hand.big_blind else 0.0,
            ))
            break
    out.sort(key=lambda e: -e.started_at)
    return out[:limit] if limit else out


def _seat_of(hand: Hand, player_key: str):
    for seat in hand.seats:
        if seat.player_id == player_key:
            return seat
    return None


def describe(hand: Hand, seat: int, street: Street | None) -> str:
    """One line: what this player did in the spot the statistic is about."""
    actions = [a for a in hand.actions
               if a.seat == seat and a.is_voluntary
               and (street is None or a.street is street)]
    if not actions:
        return "no action on that street"
    bb = hand.big_blind or 1
    parts = []
    for action in actions:
        if action.act is Act.FOLD:
            price = f" facing {action.to_call / bb:.1f}bb into {action.pot_before / bb:.1f}bb"
            parts.append(f"folded{price if action.to_call else ''}")
        elif action.act is Act.CHECK:
            parts.append("checked")
        elif action.act is Act.CALL:
            parts.append(f"called {action.amount / bb:.1f}bb "
                         f"into {action.pot_before / bb:.1f}bb")
        else:
            label = "bet" if action.act is Act.BET else "raised to"
            parts.append(f"{label} {action.to_amount / bb:.1f}bb "
                         f"into {action.pot_before / bb:.1f}bb")
    return ", then ".join(parts)
