"""Scaffolding the simulator tests build a villain out of.

Four files were driving :func:`villain.botplay.decide` and each carried its own
copy of these three -- a stub profile whose frequencies are whatever the test
names, and a bench of seats to deal them. The copies were identical, which is
the problem: ``opps`` defaults to 500 so a stubbed rate clears every sample bar
in the policy, and a file whose copy drifted off that number would have tested
the prior instead of the frequency, silently and only in that file.
"""

from dataclasses import dataclass

from villain.holdem import Seat


@dataclass
class Est:
    """One measured frequency. ``opps`` is past every bar botplay checks."""
    value: float
    opps: float = 500.0


class Prof:
    """A profile that measures exactly the frequencies named, and nothing else."""

    def __init__(self, **freqs):
        self.stats = {k: Est(v) for k, v in freqs.items()}


def seats(*stacks):
    """Seats named A, B, C... with the given starting stacks."""
    return [Seat(chr(65 + i), s) for i, s in enumerate(stacks)]


def ev(type, seat=None, value=None, **extra):
    """One PokerNow event payload: ``ev(8, 3, 300)`` is seat 3 betting to 300."""
    payload = {"type": type}
    if seat is not None:
        payload["seat"] = seat
    if value is not None:
        payload["value"] = value
    payload.update(extra)
    return payload


def pokernow_hand(players, dealer, events, sb=5, bb=10, hand_id="h1"):
    """A parsed hand built from events, for statistics the fixture never hits.

    ``players`` is ``(seat, stack, hole cards or None)``; accounts are
    ``id<seat>``."""
    from villain.parsers.pokernow import _parse_hand

    t0 = 1_000_000
    raw = {"id": hand_id, "gameType": "th", "cents": True, "smallBlind": sb,
           "bigBlind": bb, "dealerSeat": dealer, "startedAt": t0,
           "players": [{"id": f"id{s}", "seat": s, "name": f"p{s}", "stack": st,
                        **({"hand": cards} if cards else {})}
                       for s, st, cards in players],
           "events": [{"at": t0 + 1000 * i, "payload": p} for i, p in enumerate(events)]}
    return _parse_hand(raw, "t")
