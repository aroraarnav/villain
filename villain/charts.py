"""Reference preflop ranges, and your own preflop graded against them.

A frequency says *how often* you played a spot; it cannot say *which* hands
were wrong. "You opened 52% of your buttons heads-up" is true and useless next
to "you folded K5o, Q7o and Q3s on the button", because the second is a list
of decisions you can change. Only your own export can support that list --
every one of your hole cards is in it -- so this module reads your first
decision in each preflop spot and checks the hand against a reference range.

The references are hand-written charts, not generated. Ranking all 169 hands
by one formula and cutting it at the solver frequency put A5s and 55 outside a
16% UTG open and every king below K8o outside a 50% three-handed button --
the opposite of what solvers do. Each chart is written to land on the solver
frequency :mod:`villain.gto` already cites for the spot, and a test holds it
there. They are simplified 100bb solver outlines, not solver output: good
enough to name a clear misfold, never precise enough to argue about a hand at
the edge of the range.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .hero import hand_class
from .model import _POSITIONS, Act, Hand, Street
from .priors import HEADS_UP, THREE, regime

RANKS = "23456789TJQKA"

#: Every one of the 169 starting-hand classes.
ALL_CLASSES = frozenset(
    {r + r for r in RANKS}
    | {hi + lo + kind for i, hi in enumerate(RANKS) for lo in RANKS[:i] for kind in "so"})


def combos(cls: str) -> int:
    """Dealt combinations of a class: a pair is 6, suited 4, offsuit 12. A
    range's share is a share of the 1,326 combinations, not of the 169 names."""
    if len(cls) == 2:
        return 6
    return 4 if cls.endswith("s") else 12


def expand(spec: str) -> frozenset[str]:
    """Range shorthand to hand classes.

    ``pairs``, ``suited``, ``offsuit``; ``55+`` for pairs from 55 up; ``K5o+``
    for K5o through KQo; ``Kxo`` for every offsuit king; ``AKs`` for one hand;
    and a leading ``-`` to remove what follows. Read left to right."""
    out: set[str] = set()
    for token in spec.split():
        remove = token.startswith("-")
        token = token.lstrip("-")
        if token == "pairs":
            part = {r + r for r in RANKS}
        elif token == "suited":
            part = {c for c in ALL_CLASSES if c.endswith("s")}
        elif token == "offsuit":
            part = {c for c in ALL_CLASSES if c.endswith("o")}
        elif len(token) == 2 and token[0] == token[1]:
            part = {token}
        elif len(token) == 3 and token[0] == token[1] and token[2] == "+":
            part = {r + r for r in RANKS[RANKS.index(token[0]):]}
        elif len(token) in (3, 4) and token[2] in "so":
            hi, lo, kind = token[0], token[1], token[2]
            if lo == "x":
                lows = RANKS[:RANKS.index(hi)]
            elif token.endswith("+"):
                lows = RANKS[RANKS.index(lo):RANKS.index(hi)]
            else:
                lows = lo
            part = {hi + low + kind for low in lows}
        else:
            raise ValueError(f"unreadable range token {token!r}")
        out = out - part if remove else out | part
    return frozenset(out)


def share(hands: frozenset[str]) -> float:
    return sum(combos(c) for c in hands) / 1326


@dataclass(frozen=True)
class Chart:
    """One spot's reference range.

    ``target`` is the solver frequency the chart was written to hit -- the
    number a test checks the chart against, and the one shown beside your own
    rate. ``plays`` says what counts as playing the hand: raising when first
    in, or calling-or-raising when facing a raise."""

    key: str
    label: str
    plays: str               # "raise" or "continue"
    target: float
    spec: str
    hands: frozenset[str] = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, "hands", expand(self.spec))

    @property
    def share(self) -> float:
        return share(self.hands)


_RING = "4+ players"

CHARTS: dict[str, Chart] = {c.key: c for c in (
    # -- heads-up ------------------------------------------------------------
    Chart("hu:open", "Heads-up button, first in", "raise", 0.82,
          "pairs suited Axo Kxo Qxo J3o+ T4o+ 95o+ 85o+ 75o+ 64o+ 54o"),
    Chart("hu:defend", "Heads-up big blind vs a raise", "continue", 0.68,
          "pairs suited -32s -42s -52s -62s -72s -82s -92s "
          "Axo Kxo Q4o+ J6o+ T6o+ 96o+ 86o+ 75o+ 65o 54o"),
    # -- three-handed --------------------------------------------------------
    Chart("3max:open:BTN", "3-handed button, first in", "raise", 0.50,
          "pairs A2s+ K2s+ Q4s+ J6s+ T6s+ 96s+ 85s+ 75s+ 64s+ 54s Axo K5o+ Q8o+ J8o+ T8o+ 98o"),
    Chart("3max:open:SB", "3-handed small blind, folded to you", "raise", 0.46,
          "pairs A2s+ K2s+ Q5s+ J6s+ T6s+ 96s+ 85s+ 75s+ 64s+ 54s Axo K7o+ Q9o+ J9o+ T9o"),
    Chart("3max:defend:BTN", "3-handed big blind vs a button raise", "continue", 0.50,
          "pairs A2s+ K2s+ Q4s+ J6s+ T6s+ 96s+ 85s+ 74s+ 64s+ 53s+ "
          "A2o+ K5o+ Q8o+ J8o+ T7o+ 97o+ 87o 76o"),
    Chart("3max:defend:SB", "3-handed big blind vs a small-blind raise", "continue", 0.65,
          "pairs suited -32s -42s -52s -62s -72s Axo Kxo Q6o+ J7o+ T7o+ 97o+ 87o 76o 65o"),
    Chart("3max:sb_vs:BTN", "3-handed small blind vs a button raise", "continue", 0.22,
          "pairs A2s+ K9s+ Q9s+ J9s+ T9s 98s 87s 76s A8o+ KTo+ QJo"),
    # -- four or more: short-handed and full ring share the charts ------------
    # Solver openers at the same number of players left to act differ by a
    # point or two between six-max and full ring; one chart per seat-from-
    # the-button keeps the list short without moving any verdict.
    Chart("ring:open:SB", f"Small blind, folded to you ({_RING})", "raise", 0.42,
          "pairs A2s+ K2s+ Q4s+ J6s+ T6s+ 96s+ 85s+ 75s+ 64s+ 54s A2o+ K6o+ Q9o+ J9o+ T9o"),
    Chart("ring:open:BTN", f"Button, first in ({_RING})", "raise", 0.46,
          "pairs A2s+ K2s+ Q4s+ J6s+ T6s+ 96s+ 85s+ 75s+ 64s+ 54s A2o+ K8o+ Q9o+ J9o+ T9o 98o"),
    Chart("ring:open:CO", f"Cutoff, first in ({_RING})", "raise", 0.27,
          "22+ A2s+ K5s+ Q8s+ J8s+ T8s+ 97s+ 86s+ 75s+ 65s 54s A8o+ KTo+ QTo+ JTo"),
    Chart("ring:open:HJ", f"Hijack, first in ({_RING})", "raise", 0.21,
          "22+ A2s+ K8s+ Q9s+ J9s+ T9s 98s 87s 76s 65s ATo+ KTo+ QJo"),
    Chart("ring:open:MP", f"Five seats from the blinds, first in ({_RING})", "raise", 0.16,
          "22+ A2s+ K9s+ QTs+ JTs T9s 98s 87s ATo+ KJo+"),
    Chart("ring:open:EP", f"Early position, first in ({_RING})", "raise", 0.12,
          "55+ A9s+ A5s KTs+ QTs+ JTs T9s ATo+ KQo"),
    Chart("ring:defend:late", f"Big blind vs a late-position raise ({_RING})", "continue", 0.55,
          "pairs A2s+ K2s+ Q4s+ J6s+ T6s+ 96s+ 85s+ 74s+ 64s+ 53s+ "
          "A2o+ K2o+ Q8o+ J8o+ T7o+ 97o+ 87o 76o"),
    Chart("ring:defend:early", f"Big blind vs an early raise ({_RING})", "continue", 0.30,
          "pairs A2s+ K6s+ Q8s+ J8s+ T7s+ 96s+ 86s+ 75s+ 64s+ 54s A8o+ KTo+ QTo+ JTo"),
    Chart("ring:sb_vs", f"Small blind vs a raise ({_RING})", "continue", 0.15,
          "pairs A2s+ KTs+ QTs+ JTs T9s 98s AJo+ KQo"),
    Chart("ring:cold", f"Facing a raise, not in the blinds ({_RING})", "continue", 0.12,
          "pairs A9s+ A5s A4s KTs+ QTs+ JTs T9s 98s AJo+ KQo"),
    # -- after you opened and were re-raised ----------------------------------
    # Read against the whole deck like the rest, so its share is small; what
    # matters is which of *your opening hands* it keeps.
    Chart("vs3bet", "You opened and were 3-bet", "continue", 0.17,
          "pairs A2s+ K9s+ QTs+ JTs T9s 98s 87s 76s 65s AJo+ KQo"),
)}

#: Hands that belong in a 3-bet as the bluff half of it, where a 3-bet is part
#: of the plan: in the blinds against a late raise, and heads-up. Suited wheel
#: aces block the aces and ace-king that would 4-bet, and suited kings flop
#: well when called. Flatting them hands the opener a range with no bluffs in
#: its 3-bets, which they can fold to without cost.
THREE_BET_BLUFFS = expand("A5s A4s A3s A2s K9s K8s K7s K6s K5s")

#: Spots where a 3-bet belongs in the plan and flatting a bluff candidate is
#: worth pointing out. Early-position opens are too strong to bluff into.
BLUFF_SPOTS = frozenset({"hu:defend", "3max:defend:BTN", "3max:defend:SB",
                         "3max:sb_vs:BTN", "ring:defend:late"})


# -- which spot a decision was ---------------------------------------------------

def preflop_order(n: int) -> list[str]:
    """Positions in preflop acting order at a table of ``n``."""
    labels = _POSITIONS[n]
    return labels if n == 2 else labels[2:] + labels[:2]


def _open_chart(n: int, position: str) -> str | None:
    kind = regime(n)
    if kind == HEADS_UP:
        return "hu:open" if position == "BTN" else None
    if kind == THREE:
        return f"3max:open:{position}" if position in ("BTN", "SB") else None
    order = preflop_order(n)
    if position not in order or position == "BB":
        return None
    behind = len(order) - 1 - order.index(position)
    return {1: "ring:open:SB", 2: "ring:open:BTN", 3: "ring:open:CO",
            4: "ring:open:HJ", 5: "ring:open:MP"}.get(behind, "ring:open:EP")


def _facing_chart(n: int, position: str, opener: str) -> str | None:
    kind = regime(n)
    if kind == HEADS_UP:
        return "hu:defend" if position == "BB" else None
    if kind == THREE:
        if position == "BB":
            return f"3max:defend:{opener}" if opener in ("BTN", "SB") else None
        return "3max:sb_vs:BTN" if position == "SB" and opener == "BTN" else None
    if position == "BB":
        order = preflop_order(n)
        late = opener in order and len(order) - 1 - order.index(opener) <= 3
        return "ring:defend:late" if late else "ring:defend:early"
    return "ring:sb_vs" if position == "SB" else "ring:cold"


@dataclass(frozen=True)
class SpotDecision:
    """One of your preflop decisions, placed in a chart's spot."""

    chart: str
    hand_id: str
    cls: str
    action: str              # "raise", "call" or "fold"
    started_at: int
    big_blind: int

    @property
    def played(self) -> bool:
        return self.action != "fold"


def _label(act: Act) -> str:
    return {Act.RAISE: "raise", Act.BET: "raise", Act.CALL: "call"}.get(act, "fold")


def spot_decisions(hand: Hand, player_key: str) -> list[SpotDecision]:
    """Your first preflop decision in a charted spot, plus your answer to a
    3-bet if you opened and got one.

    Limped pots and pots already raised and called are left out on purpose:
    each is its own spot with its own range, and folding one into "facing a
    raise" would grade an overlimp against an open-raise chart."""
    seat = next((s for s in hand.seats if s.player_id == player_key), None)
    if seat is None or len(seat.hole_cards) != 2:
        return []
    n = len(hand.seats)
    if n not in _POSITIONS:
        return []
    cls = hand_class(seat.hole_cards)
    by_seat = {s.seat: s for s in hand.seats}
    out: list[SpotDecision] = []
    raises = callers = 0
    opener: str | None = None
    opened = False
    for a in hand.actions:
        if a.street is not Street.PREFLOP:
            break
        if not a.act.is_voluntary:
            continue
        if a.seat == seat.seat:
            if not out and not opened:
                if raises == 0 and callers == 0:
                    key = _open_chart(n, seat.position)
                    if key is not None and a.act is not Act.CHECK:
                        out.append(SpotDecision(key, hand.hand_id, cls, _label(a.act),
                                                hand.started_at, hand.big_blind))
                        opened = a.act.is_aggressive
                        raises += opened
                elif raises == 1 and callers == 0 and opener is not None:
                    key = _facing_chart(n, seat.position, opener)
                    if key is not None:
                        out.append(SpotDecision(key, hand.hand_id, cls, _label(a.act),
                                                hand.started_at, hand.big_blind))
                if not out:
                    return []
                continue
            if opened and raises == 2:
                out.append(SpotDecision("vs3bet", hand.hand_id, cls, _label(a.act),
                                        hand.started_at, hand.big_blind))
            return out
        if a.act.is_aggressive:
            raises += 1
            if raises == 1:
                opener = by_seat[a.seat].position if a.seat in by_seat else None
        elif a.act is Act.CALL and not opened:
            callers += 1
        if opened and raises > 2:
            return out            # a 4-bet behind the 3-bet is another spot
    return out


# -- the audit ------------------------------------------------------------------

@dataclass
class SpotReport:
    """Every decision you made in one chart's spot, graded against it."""

    chart: Chart
    decisions: list[SpotDecision] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.decisions)

    @property
    def played(self) -> int:
        return sum(d.played for d in self.decisions)

    @property
    def raised(self) -> int:
        return sum(d.action == "raise" for d in self.decisions)

    @property
    def called(self) -> int:
        return sum(d.action == "call" for d in self.decisions)

    @property
    def rate(self) -> float:
        return self.played / self.n if self.n else 0.0

    @property
    def misfolds(self) -> list[SpotDecision]:
        """Folds of a hand the chart plays."""
        return [d for d in self.decisions if not d.played and d.cls in self.chart.hands]

    @property
    def loose(self) -> list[SpotDecision]:
        """Hands played that the chart folds."""
        return [d for d in self.decisions if d.played and d.cls not in self.chart.hands]

    @property
    def gap(self) -> float:
        """Signed: negative means tighter than the reference."""
        return self.rate - self.chart.target


def audit(hands: list[Hand], player_key: str) -> list[SpotReport]:
    """Your preflop, spot by spot, in chart order."""
    reports = {key: SpotReport(chart) for key, chart in CHARTS.items()}
    for hand in hands:
        for d in spot_decisions(hand, player_key):
            reports[d.chart].decisions.append(d)
    return [r for r in reports.values() if r.n]


def flatted_bluffs(reports: list[SpotReport]) -> list[SpotDecision]:
    """Calls with a standard 3-bet bluff, in the spots where 3-betting is the
    plan -- the flats that would turn an all-value 3-bet range into a balanced
    one."""
    return [d for r in reports if r.chart.key in BLUFF_SPOTS
            for d in r.decisions if d.action == "call" and d.cls in THREE_BET_BLUFFS]
