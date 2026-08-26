"""Canonical hand representation.

Every site's history format is different; every downstream statistic wants the
same thing. The contract of this module is that shape: a ``Hand`` is a list of
``Action`` records with pot and stack state already resolved, so a statistic can
be written as a loop over actions and never has to know what a "type 8 event"
was.

Two conventions worth stating once, because every parser and stat depends on
them:

* **Money is integers in table units** -- cents for a cents table, chips
  otherwise. Floats only appear when a stat divides by the big blind.
* **``Action.amount`` is the increment** the player put in with that action,
  while ``Action.to_amount`` is their cumulative wager for that street. Sites
  disagree on which one they report; parsers must supply both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class Street(IntEnum):
    PREFLOP = 0
    FLOP = 1
    TURN = 2
    RIVER = 3

    @property
    def label(self) -> str:
        return STREET_LABELS[self]


STREET_LABELS = {
    Street.PREFLOP: "preflop",
    Street.FLOP: "flop",
    Street.TURN: "turn",
    Street.RIVER: "river",
}

# Cards dealt on each street, used to slice a board into streets.
STREET_CARDS = {Street.PREFLOP: 0, Street.FLOP: 3, Street.TURN: 1, Street.RIVER: 1}


class Act(IntEnum):
    """What a player did. Posts are separated from voluntary actions because
    every preflop statistic hinges on the difference."""

    POST_SB = 0
    POST_BB = 1
    POST_ANTE = 2
    POST_STRADDLE = 3
    FOLD = 4
    CHECK = 5
    CALL = 6
    BET = 7
    RAISE = 8

    @property
    def is_post(self) -> bool:
        return self <= Act.POST_STRADDLE

    @property
    def is_aggressive(self) -> bool:
        return self in (Act.BET, Act.RAISE)

    @property
    def is_voluntary(self) -> bool:
        return not self.is_post


ACT_LABELS = {
    Act.POST_SB: "posts sb", Act.POST_BB: "posts bb", Act.POST_ANTE: "posts ante",
    Act.POST_STRADDLE: "straddles", Act.FOLD: "folds", Act.CHECK: "checks",
    Act.CALL: "calls", Act.BET: "bets", Act.RAISE: "raises",
}

# Position labels ordered from the small blind clockwise, indexed by table size.
# Heads-up is the special case everywhere in poker: the button posts the small
# blind and acts first preflop, last postflop.
_POSITIONS = {
    2: ["BTN", "BB"],
    3: ["SB", "BB", "BTN"],
    4: ["SB", "BB", "UTG", "BTN"],
    5: ["SB", "BB", "UTG", "CO", "BTN"],
    6: ["SB", "BB", "UTG", "HJ", "CO", "BTN"],
    7: ["SB", "BB", "UTG", "UTG1", "HJ", "CO", "BTN"],
    8: ["SB", "BB", "UTG", "UTG1", "MP", "HJ", "CO", "BTN"],
    9: ["SB", "BB", "UTG", "UTG1", "MP", "LJ", "HJ", "CO", "BTN"],
    10: ["SB", "BB", "UTG", "UTG1", "UTG2", "MP", "LJ", "HJ", "CO", "BTN"],
}

# Coarse buckets used by stats that would be too sparse per exact position.
IN_POSITION_LAST = ("BTN", "CO")
BLINDS = ("SB", "BB")


def positions_for(seats: list[int], dealer_seat: int) -> dict[int, str]:
    """Map seat number -> position label, walking clockwise from the button.

    ``seats`` is the occupied seats in table order. Heads-up the dealer *is* the
    small blind, which the ``_POSITIONS`` table encodes by starting at BTN.
    """
    n = len(seats)
    if n < 2:
        return {}
    if n > 10:
        raise ValueError(f"unsupported table size {n}")
    order = sorted(seats)
    if dealer_seat not in order:
        # Dead button: the dealer seat left the table. Anchor on the next
        # occupied seat instead so positions stay contiguous.
        later = [s for s in order if s > dealer_seat]
        dealer_seat = later[0] if later else order[0]
    start = order.index(dealer_seat)
    # Rotate so the small blind (or the button, heads-up) leads the list.
    offset = 0 if n == 2 else 1
    rotated = [order[(start + offset + i) % n] for i in range(n)]
    return dict(zip(rotated, _POSITIONS[n]))


@dataclass
class Action:
    """One decision by one player."""

    street: Street
    seat: int
    act: Act
    amount: int = 0          # chips added by this action
    to_amount: int = 0       # player's cumulative wager on this street after it
    all_in: bool = False
    at: int | None = None    # wall-clock ms, when the site records it
    think_ms: int | None = None   # ms since the previous action in the hand
    pot_before: int = 0      # total pot when the decision was made
    to_call: int = 0         # chips the player had to add to continue

    @property
    def is_voluntary(self) -> bool:
        return self.act.is_voluntary

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.street.label} s{self.seat} {ACT_LABELS[self.act]} {self.to_amount}>"


@dataclass
class Seat:
    """A player's involvement in one hand."""

    seat: int
    player_id: str
    name: str
    stack: int                       # stack at the start of the hand
    position: str = "?"
    hole_cards: tuple[str, ...] = ()      # only ever 0 or 2 well-formed cards
    revealed: tuple[str, ...] = ()        # what was turned face up, which on
                                          # some sites is a single card
    invested: int = 0                # total chips put in across all streets
    won: int = 0                     # total chips returned from pots
    showed: bool = False             # cards were turned face up

    @property
    def net(self) -> int:
        return self.won - self.invested


@dataclass
class Hand:
    """One complete hand of poker, site-independent."""

    hand_id: str
    site: str
    table_id: str
    started_at: int                  # epoch ms
    big_blind: int
    small_blind: int
    ante: int = 0
    game: str = "nlhe"
    unit: str = "cents"
    seats: list[Seat] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    board: list[str] = field(default_factory=list)
    pot: int = 0
    rake: int = 0
    winners: list[int] = field(default_factory=list)
    run_count: int = 1               # 2 when the board was run twice
    flags: set[str] = field(default_factory=set)   # e.g. {"bomb_pot", "straddle"}
    #: The seat the person who exported these hands was sitting in, when the
    #: file says. A seat rather than an account id on purpose: ``rebuild``
    #: re-keys every seat onto an internal player id, so an account recorded
    #: here would go stale the first time two of them were merged. Seat numbers
    #: are a fact about the hand and survive that. See :mod:`villain.hero`.
    hero_seat: int | None = None

    # -- lookups ---------------------------------------------------------
    def seat(self, seat: int) -> Seat:
        for s in self.seats:
            if s.seat == seat:
                return s
        raise KeyError(f"seat {seat} not in hand {self.hand_id}")

    def reached(self, street: Street) -> bool:
        """True if the hand got far enough for that street's cards to be dealt."""
        return len(self.board) >= _CARDS_BY_STREET[street]

    def board_at(self, street: Street) -> list[str]:
        return self.board[: _CARDS_BY_STREET[street]]

    @property
    def bb(self) -> float:
        return float(self.big_blind)


_CARDS_BY_STREET = {Street.PREFLOP: 0, Street.FLOP: 3, Street.TURN: 4, Street.RIVER: 5}


def postflop_rank(table_size: int) -> dict[str, int]:
    """Position -> index in postflop acting order.

    For three or more players that is just ``_POSITIONS`` (small blind first,
    button last). Heads-up it inverts: the button posts the small blind and acts
    first preflop, so the big blind is first to act on every later street.
    """
    order = ["BB", "BTN"] if table_size == 2 else _POSITIONS.get(table_size, [])
    return {p: i for i, p in enumerate(order)}


# -- serialisation -------------------------------------------------------------
# Hands are stored, not just the statistics derived from them. Stat definitions
# change -- a c-bet gets redefined, a new leak rule needs a counter nobody was
# recording -- and when they do, every profile can be rebuilt from source
# instead of being wrong until the player is seen again.

def hand_to_dict(hand: Hand) -> dict:
    return {
        "hand_id": hand.hand_id, "site": hand.site, "table_id": hand.table_id,
        "started_at": hand.started_at, "big_blind": hand.big_blind,
        "small_blind": hand.small_blind, "ante": hand.ante, "game": hand.game,
        "unit": hand.unit, "board": hand.board, "pot": hand.pot, "rake": hand.rake,
        "winners": hand.winners, "run_count": hand.run_count,
        "flags": sorted(hand.flags), "hero_seat": hand.hero_seat,
        "seats": [
            {"seat": s.seat, "player_id": s.player_id, "name": s.name, "stack": s.stack,
             "position": s.position, "hole_cards": list(s.hole_cards),
             "revealed": list(s.revealed),
             "invested": s.invested, "won": s.won, "showed": s.showed}
            for s in hand.seats
        ],
        "actions": [
            {"street": int(a.street), "seat": a.seat, "act": int(a.act),
             "amount": a.amount, "to_amount": a.to_amount, "all_in": a.all_in,
             "at": a.at, "think_ms": a.think_ms, "pot_before": a.pot_before,
             "to_call": a.to_call}
            for a in hand.actions
        ],
    }


def hand_from_dict(data: dict) -> Hand:
    hand = Hand(
        hand_id=data["hand_id"], site=data["site"], table_id=data["table_id"],
        started_at=data["started_at"], big_blind=data["big_blind"],
        small_blind=data["small_blind"], ante=data.get("ante", 0),
        game=data.get("game", "nlhe"), unit=data.get("unit", "chips"),
        board=list(data.get("board", [])), pot=data.get("pot", 0),
        rake=data.get("rake", 0), winners=list(data.get("winners", [])),
        run_count=data.get("run_count", 1), flags=set(data.get("flags", [])),
        hero_seat=data.get("hero_seat"),
    )
    hand.seats = [
        Seat(seat=s["seat"], player_id=s["player_id"], name=s["name"], stack=s["stack"],
             position=s.get("position", "?"), hole_cards=tuple(s.get("hole_cards", ())),
             revealed=tuple(s.get("revealed", ())),
             invested=s.get("invested", 0), won=s.get("won", 0),
             showed=s.get("showed", False))
        for s in data.get("seats", [])
    ]
    hand.actions = [
        Action(street=Street(a["street"]), seat=a["seat"], act=Act(a["act"]),
               amount=a.get("amount", 0), to_amount=a.get("to_amount", 0),
               all_in=a.get("all_in", False), at=a.get("at"),
               think_ms=a.get("think_ms"), pot_before=a.get("pot_before", 0),
               to_call=a.get("to_call", 0))
        for a in data.get("actions", [])
    ]
    return hand
