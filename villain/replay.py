"""A hand, laid out for reading.

The canonical ``Hand`` is built for computation: a flat action list with pot
and stack state resolved. Reading one back needs the opposite shape -- streets,
each with its cards and the actions taken on it -- so this turns one into the
other. No analysis, no opinions, just the hand as it happened.
"""

from __future__ import annotations

from .model import ACT_LABELS, Hand, Street


def replay(hand: Hand, focus: str | None = None) -> dict:
    """Street-by-street view of a hand, ready to render.

    ``focus`` is a player key whose actions get marked, so the hand can be read
    from one player's point of view -- which is how it is always being read
    when it arrives from a piece of evidence."""
    bb = hand.big_blind or 1
    seats = [
        {"seat": s.seat, "name": s.name, "position": s.position,
         "stack_bb": round(s.stack / bb, 1),
         "hole_cards": list(s.hole_cards),
         "net_bb": round(s.net / bb, 2),
         "won": s.seat in hand.winners,
         "focus": s.player_id == focus}
        for s in hand.seats
    ]
    by_seat = {s.seat: s for s in hand.seats}

    streets = []
    for street in Street:
        actions = [a for a in hand.actions if a.street is street]
        if not actions and not hand.reached(street):
            continue
        streets.append({
            "name": street.label,
            "cards": list(hand.board_at(street)) if street is not Street.PREFLOP else [],
            "new_cards": _new_cards(hand, street),
            "actions": [
                {
                    "seat": a.seat,
                    "name": by_seat[a.seat].name if a.seat in by_seat else str(a.seat),
                    "position": by_seat[a.seat].position if a.seat in by_seat else "?",
                    "act": ACT_LABELS[a.act],
                    "amount_bb": round(a.amount / bb, 2),
                    "to_bb": round(a.to_amount / bb, 2),
                    "pot_bb": round(a.pot_before / bb, 1),
                    "to_call_bb": round(a.to_call / bb, 2),
                    "all_in": a.all_in,
                    "think_s": None if a.think_ms is None else round(a.think_ms / 1000, 1),
                    "focus": a.seat in by_seat and by_seat[a.seat].player_id == focus,
                    "post": a.act.is_post,
                }
                for a in actions
            ],
        })

    return {
        "hand_id": hand.hand_id,
        "started_at": hand.started_at,
        "site": hand.site,
        "table_id": hand.table_id,
        "blinds": f"{hand.small_blind / bb:.1f}/{1.0:.1f}",
        "big_blind": hand.big_blind,
        "players": len(hand.seats),
        "board": list(hand.board),
        "pot_bb": round(hand.pot / bb, 1),
        "seats": seats,
        "streets": streets,
        "winners": [by_seat[s].name for s in hand.winners if s in by_seat],
        "showdown": [s["name"] for s in seats if s["hole_cards"]],
    }


def _new_cards(hand: Hand, street: Street) -> list[str]:
    """Only the cards this street added, for a display that deals them out."""
    if street is Street.PREFLOP:
        return []
    previous = {Street.FLOP: 0, Street.TURN: 3, Street.RIVER: 4}[street]
    return list(hand.board[previous:{Street.FLOP: 3, Street.TURN: 4,
                                     Street.RIVER: 5}[street]])
