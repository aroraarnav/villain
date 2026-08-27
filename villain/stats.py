"""Per-hand statistic extraction.

Everything here produces *sufficient statistics*: counts and running moments
that add. Two sessions of the same player combine by adding their books
together, which is what makes the database in :mod:`villain.db` a one-line
merge and what lets a profile keep sharpening every time a villain sits down
again.

The vocabulary is standard tracker vocabulary (VPIP, PFR, 3-bet, fold-to-cbet)
because those are the numbers whose exploitative meaning is already understood.
Each definition states its *denominator*, since that is where trackers quietly
disagree with each other -- a 3-bet percentage means nothing without knowing
whether it was measured over hands dealt or over hands facing a raise.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field

from .model import Act, Action, Hand, Street, postflop_rank

# Bet size buckets as a fraction of the pot before the bet. Fold frequencies
# split by these are the single most exploitable postflop tendency.
SIZE_BUCKETS = (("small", 0.0, 0.42), ("mid", 0.42, 0.70), ("big", 0.70, 1.01), ("over", 1.01, math.inf))

# Starting-stack buckets in big blinds. 20bb and 100bb are different games;
# a single pooled rfi describes neither, and the sim's stack knob is a no-op
# until frequencies (and the push/fold policy) can see which one this is.
STACK_BUCKETS = (("short", 0.0, 25.0), ("mid", 25.0, 70.0), ("deep", 70.0, math.inf))

#: Counters recorded a second time under this prefix when the player on the
#: other side of the decision was you. ``vs:fold_vs_bet:river`` is the slice of
#: ``fold_vs_bet:river`` where the bet was yours.
#:
#: A namespace rather than a table: they add, merge and rebuild identically,
#: and the pooled key stays the baseline. What they do not share is a
#: population -- there is no field frequency for "folds to that guy" -- so
#: anything measuring against the field must skip this namespace.
VS_HERO = "vs:"


def size_bucket(fraction: float) -> str:
    for name, lo, hi in SIZE_BUCKETS:
        if lo <= fraction < hi:
            return name
    return "over"


def stack_bucket(bb: float) -> str:
    """Which depth this starting stack plays as: short / mid / deep."""
    for name, lo, hi in STACK_BUCKETS:
        if lo <= bb < hi:
            return name
    return "deep"


@dataclass
class Ratio:
    """A count with its opportunity count."""

    hits: float = 0.0
    opps: float = 0.0

    def add(self, hit: bool, weight: float = 1.0) -> None:
        self.opps += weight
        if hit:
            self.hits += weight

    def merge(self, other: Ratio) -> None:
        self.hits += other.hits
        self.opps += other.opps

    @property
    def rate(self) -> float | None:
        return self.hits / self.opps if self.opps else None


@dataclass
class Meter:
    """Running mean/variance of a continuous quantity (Welford-free, moments)."""

    n: float = 0.0
    total: float = 0.0
    sumsq: float = 0.0

    def add(self, value: float, weight: float = 1.0) -> None:
        self.n += weight
        self.total += value * weight
        self.sumsq += value * value * weight

    def merge(self, other: Meter) -> None:
        self.n += other.n
        self.total += other.total
        self.sumsq += other.sumsq

    @property
    def mean(self) -> float | None:
        return self.total / self.n if self.n else None

    @property
    def sd(self) -> float | None:
        if self.n < 2:
            return None
        var = self.sumsq / self.n - (self.total / self.n) ** 2
        return math.sqrt(max(var, 0.0))


@dataclass
class StatBook:
    """Everything known about one player *in one table-size regime*.

    Regime is part of the identity of a book, not a label on it. The same
    person three-handed and heads-up produces two books, because pooling them
    would average two genuinely different strategies into one that describes
    neither."""

    player_id: str = ""
    name: str = ""
    regime: str = ""
    hands: int = 0
    ratios: dict[str, Ratio] = field(default_factory=lambda: defaultdict(Ratio))
    meters: dict[str, Meter] = field(default_factory=lambda: defaultdict(Meter))
    first_seen: int | None = None
    last_seen: int | None = None

    def count(self, stat: str, hit: bool) -> None:
        self.ratios[stat].add(hit)

    def measure(self, stat: str, value: float) -> None:
        self.meters[stat].add(value)

    def merge(self, other: StatBook) -> StatBook:
        """Add another book of the same player and regime into this one."""
        self.hands += other.hands
        self.player_id = self.player_id or other.player_id
        self.name = self.name or other.name
        self.regime = self.regime or other.regime
        for k, v in other.ratios.items():
            self.ratios[k].merge(v)
        for k, v in other.meters.items():
            self.meters[k].merge(v)
        for attr, pick in (("first_seen", min), ("last_seen", max)):
            a, b = getattr(self, attr), getattr(other, attr)
            setattr(self, attr, pick(x for x in (a, b) if x is not None) if (a or b) else None)
        return self

    def rate(self, stat: str) -> float | None:
        r = self.ratios.get(stat)
        return r.rate if r else None

    def opps(self, stat: str) -> float:
        r = self.ratios.get(stat)
        return r.opps if r else 0.0

    def mean(self, stat: str) -> float | None:
        m = self.meters.get(stat)
        return m.mean if m else None


# -- Hand view: the derived context every statistic needs ----------------------


@dataclass
class Decision:
    """One action plus the context that makes it interpretable."""

    action: Action
    street: Street
    seat: int
    facing_bet: bool
    aggression_level: int      # raises already made on this street (0 = unopened)
    has_initiative: bool       # was the last aggressor on the previous street
    in_position: bool          # acts last among players still in on this street
    bet_fraction: float        # size of the bet faced (or made) relative to pot
    players_in: int


class HandView:
    """Precomputed per-hand context: who saw what, who had initiative, who won.

    Built once per hand and shared by every statistic, because most of the cost
    of a stat engine is recomputing this state over and over."""

    def __init__(self, hand: Hand):
        self.hand = hand
        self.seats = {s.seat: s for s in hand.seats}
        self.folded_on: dict[int, Street] = {}
        self.saw: dict[Street, set[int]] = {s: set() for s in Street}
        self.aggressor: dict[Street, int | None] = dict.fromkeys(Street)
        self.last_street = Street.PREFLOP
        self._build()

    def _build(self) -> None:
        h = self.hand
        live = set(self.seats)
        self.saw[Street.PREFLOP] = set(live)
        for a in h.actions:
            self.last_street = max(self.last_street, a.street)
            if a.act is Act.FOLD:
                self.folded_on[a.seat] = a.street
                live.discard(a.seat)
            if a.act.is_aggressive:
                self.aggressor[a.street] = a.seat
            self.saw[a.street].add(a.seat)
        # A player "saw" a street if they were still live when it was dealt,
        # which the action log only shows when they acted -- reconstruct it.
        live = set(self.seats)
        for street in Street:
            if not h.reached(street):
                break
            self.saw[street] = set(live)
            for seat, folded in self.folded_on.items():
                if folded == street:
                    live.discard(seat)

    def initiative_at(self, street: Street) -> int | None:
        """Who was the last aggressor on any earlier street."""
        for earlier in range(street - 1, -1, -1):
            who = self.aggressor[Street(earlier)]
            if who is not None:
                return who
        return None

    def showdown(self) -> set[int]:
        """Seats that were still live when the last street finished."""
        if not self.hand.board and len(self.hand.winners) <= 1:
            return set()
        live = set(self.seats) - set(self.folded_on)
        return live if len(live) >= 2 else set()

    def decisions(self) -> Iterator[Decision]:
        h = self.hand
        street_wager: dict[int, int] = {}
        level = 0
        current_street = Street.PREFLOP
        for a in h.actions:
            if a.street is not current_street:
                current_street, street_wager, level = a.street, {}, 0
            if a.act.is_post:
                street_wager[a.seat] = a.to_amount
                continue
            live_now = self._live_before(a)
            faced = a.to_call > 0
            frac = (a.to_call / a.pot_before) if (faced and a.pot_before) else 0.0
            if a.act.is_aggressive and a.pot_before:
                frac = a.amount / a.pot_before
            yield Decision(
                action=a,
                street=a.street,
                seat=a.seat,
                facing_bet=faced,
                aggression_level=level,
                has_initiative=self.initiative_at(a.street) == a.seat,
                in_position=self._in_position(a, live_now),
                bet_fraction=frac,
                players_in=len(live_now),
            )
            if a.act.is_aggressive:
                level += 1
            street_wager[a.seat] = a.to_amount

    def _live_before(self, action: Action) -> set[int]:
        live = set(self.seats)
        for seat, street in self.folded_on.items():
            if street < action.street:
                live.discard(seat)
        return live

    def _in_position(self, action: Action, live: set[int]) -> bool:
        """True if nobody left to act after them postflop, by seat order."""
        if action.street is Street.PREFLOP:
            return self.seats[action.seat].position in ("BTN", "CO")
        order = self._postflop_order(live)
        return bool(order) and order[-1] == action.seat

    def _postflop_order(self, live: set[int]) -> list[int]:
        """Seats in postflop acting order."""
        rank = postflop_rank(len(self.seats))
        return sorted(live, key=lambda s: rank.get(self.seats[s].position, 99))
