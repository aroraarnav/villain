"""What only your own hand history can tell you.

Every read elsewhere in this project is inference: a villain's range is a
guess built from how often they bet, weighted toward showdown because that is
the only time their cards are ever known -- one villain shows a hand in maybe
eight. Hero has no such gap. The exporting player's hole cards are visible on
essentially every hand, fold or not, showdown or not -- 98.8% of 6,433 hands
in the live database. Two things become possible that are structurally
impossible for anyone else at the table:

* **A real preflop range.** Villains' folded hands are never seen, so their
  range is reconstructed from aggregate frequencies -- VPIP, PFR, 3-bet -- and
  a prototype's guess at the shape behind them. Hero's is not reconstructed.
  It is counted, hand by hand, from what was actually held.
* **Fold quality, not just fold frequency.** The rest of the tool prices a
  fold *rule* from how often somebody folds against a bet size, because that
  is all it can measure of a villain -- and it deliberately measures against
  a fixed reference (see :data:`villain.profile.CORRECT_FOLD`) rather than
  claiming to know any individual fold was wrong. Hero's individual folds can
  be graded, because the hand is known: given what you actually had and what
  it cost to continue, was folding right, on that specific hand?
* **Missed value, the mirror of fold quality.** A check is the same kind of
  question asked the other way: was the hand behind it strong enough that
  betting would have made more money? Just as unanswerable for a villain, for
  the same reason -- their checks are never revealed either.
* **Whether hero's own bet sizing is a tell.** Does the size of the bet
  change with the strength of the hand behind it? Nobody else's hand
  strength is known widely enough to ask this about a villain, but hero's is
  known on every single bet, not just the ones that reached showdown.
* **Whether hero's own timing is a tell.** Same question, asked of think
  time instead of bet size: does hero take longer with one half of the
  strength range than the other?
* **Whether hero's range actually narrows.** A continuing range is supposed
  to get stronger street by street as the wide ones give up along the way --
  average hand strength among hands still live, by street, says whether
  hero's actually does.

All of these use the same building block :func:`villain.reads.strength_by_street`
already computes for the population model. This module points it at hero
specifically, including the folds, the checks, and the sizing the population
model has no way to see.

Everything above needs one fact first: which of the seats is hero's.
:func:`find_hero` answers it for a database, from the same visibility that
makes the rest of this module possible. :func:`hero_of` answers it for a list
of hands, which is what the statistics and the evidence behind them are
extracted from, before anything has been saved -- and prefers the export's own
word for it where a site gives one.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .model import STREET_LABELS, Act, Street
from .reads import StrengthModel, build_dataset, strength_by_street, texture
from .reads import fit as fit_strength
from .stats import Decision, HandView

#: Below this fraction of a player's own hands having known cards, they are
#: not hero -- they are a villain who happened to show a few.
HERO_VISIBILITY = 0.90

#: Below this many hands, visibility alone is too noisy to trust.
MIN_HERO_HANDS = 100

#: Same margin :mod:`villain.exploits` uses before calling a deviation a leak
#: rather than noise -- consistent standard, not a coincidence. Used by
#: FoldGrade specifically: folds have a strongly negative mean edge (hero's
#: folded hands run ~0.26 weaker than the bet they faced, on the live
#: database), so a loose margin rarely fires by chance. See
#: MISSED_VALUE_MARGIN for why checks need a different bar.
MARGIN = 0.05

RANK_ORDER = "23456789TJQKA"

POSITION_ORDER = {p: i for i, p in enumerate(
    ["UTG", "UTG1", "UTG2", "MP", "MP1", "MP2", "LJ", "HJ", "CO", "BTN", "SB", "BB"])}


def find_hero(store, min_hands: int = MIN_HERO_HANDS, progress=None,
              hands=None) -> int | None:
    """Which internal player id is hero, judged by whose cards are visible
    almost regardless of outcome rather than almost only at showdown.

    ``min_hands`` is overridable for testing against small fixtures; real
    callers should leave it at the default, which exists so a villain who
    happened to show a few hands in a short sample cannot look like hero.
    """
    seen: dict[int, int] = {}
    total: dict[int, int] = {}
    # `hands` lets a caller that has already loaded them hand them over. A cold
    # Hero build needs the same list twice -- once to work out whose seat is
    # whose, once to fit the model -- and loading it twice meant decompressing
    # and parsing the whole database twice for one page.
    #
    # Counted when it does load: on a cold build this is the first thing that
    # happens, so it is the first thing anybody waiting is waiting for.
    seq = hands
    if seq is None:
        seq = store.player_hands(progress=progress)
        progress = None          # the load already counted itself
    n = len(seq)
    for at, hand in enumerate(seq):
        if progress is not None and at % 200 == 0:
            progress(at, n)
        for seat in hand.seats:
            try:
                pid = int(seat.player_id)
            except (TypeError, ValueError):
                continue
            total[pid] = total.get(pid, 0) + 1
            if len(seat.hole_cards) == 2:
                seen[pid] = seen.get(pid, 0) + 1
    if progress is not None:
        progress(n, n)

    best, best_frac = None, 0.0
    for pid, count in total.items():
        if count < min_hands:
            continue
        frac = seen.get(pid, 0) / count
        if frac > best_frac:
            best, best_frac = pid, frac
    return best if best_frac >= HERO_VISIBILITY else None


#: Hands the leader must hold *unshown* cards in before :func:`hero_of` will
#: name them, and how far clear of the runner-up.
#:
#: Far below :data:`MIN_HERO_HANDS` on purpose, because it is a different
#: question asked of a different sample. :func:`find_hero` searches a whole
#: database, where a hundred hands is cheap and a villain who showed a few
#: must not win; this runs over one import, which is often exactly one
#: session. It can afford the smaller floor because it counts a stricter
#: signal -- cards visible *without having been shown*, rather than cards
#: visible at all -- so a villain who turned every hand face up at showdown
#: contributes nothing to it.
MIN_UNBIASED = 5
MARGIN_UNBIASED = 3.0


def hero_of(hands) -> str | None:
    """Which player id in ``hands`` is hero, or ``None`` if it is not clear.

    The hand-list counterpart of :func:`find_hero`, for the callers that have
    no store to search: statistics are extracted from a list of hands, and so
    is the evidence behind them, both before anything has been saved.

    Two signals, best first. Sites that name the exporter in the export are
    believed outright -- the parser records that as :attr:`Hand.hero_seat`,
    and PokerNow gives it. Failing that, and for hands stored before the field
    existed, it falls back to the same reasoning :func:`find_hero` uses: an
    export shows you your own cards on every hand you were dealt and shows you
    everybody else's only when they were turned face up at the end.

    Resolved from the seat rather than from a stored account id, so the answer
    follows a player through renames and merges: ``rebuild`` re-keys every
    seat onto an internal player id and this returns whatever that seat holds
    now.

    Returns ``None`` rather than guessing. A wrong answer here does not fail
    loudly -- it quietly relabels one opponent's decisions as your own.
    """
    hands = list(hands)
    stated = Counter(
        hand.seat(hand.hero_seat).player_id
        for hand in hands
        if hand.hero_seat is not None
        and any(s.seat == hand.hero_seat for s in hand.seats)
    )
    if stated:
        return stated.most_common(1)[0][0]

    unbiased: Counter[str] = Counter()
    for hand in hands:
        for seat in hand.seats:
            if len(seat.hole_cards) == 2 and not seat.showed:
                unbiased[seat.player_id] += 1
    if not unbiased:
        return None
    (leader, count), = unbiased.most_common(1)
    if count < MIN_UNBIASED:
        return None
    runner_up = max((n for p, n in unbiased.items() if p != leader), default=0)
    return leader if count >= MARGIN_UNBIASED * max(runner_up, 1) else None


def hand_class(hole_cards: tuple[str, ...]) -> str:
    """``("Ah", "Kd")`` -> ``"AKo"``; a pair -> ``"77"``, no suffix."""
    (r1, s1), (r2, s2) = [(c[0].upper(), c[1].lower()) for c in hole_cards]
    if RANK_ORDER.index(r1) < RANK_ORDER.index(r2):
        (r1, s1), (r2, s2) = (r2, s2), (r1, s1)
    if r1 == r2:
        return r1 + r2
    return r1 + r2 + ("s" if s1 == s2 else "o")


def texture_label(board: list[str]) -> str:
    """"wet" or "dry" -- a chosen two-way split of :func:`villain.reads.texture`,
    not a derived one. Suited or connected boards carry live draws and are
    called wet; everything else is dry. Coarse on purpose: a finer split
    would need more hands per bucket than most players have to spend."""
    _paired, suited, connected, _high = texture(board)
    return "wet" if (suited or connected) else "dry"


# ---------------------------------------------------------------------------
# preflop range: counted, not modeled
# ---------------------------------------------------------------------------

@dataclass
class PositionRange:
    position: str
    hands: int = 0
    raised: int = 0
    called: int = 0
    checked: int = 0
    folded: int = 0
    #: hand class -> count, for the chart. ``played`` is raised-or-called --
    #: the standard VPIP definition, which leaves out a BB checking a free
    #: option since no money went in voluntarily.
    raised_classes: dict[str, int] = field(default_factory=dict)
    played_classes: dict[str, int] = field(default_factory=dict)
    dealt_classes: dict[str, int] = field(default_factory=dict)


def hero_visibility(hands: list, hero_id: int) -> tuple[int, int]:
    """(hands with known cards, hands dealt into) for hero within ``hands``.

    Takes an already-fetched hand list rather than a store, so a caller who
    needs both this and :func:`preflop_range`/:func:`fold_grades` fetches
    ``store.player_hands(hero_id)`` once instead of three times -- each fetch
    re-reads and decompresses every hand in the database.
    """
    seen = total = 0
    for hand in hands:
        seat = next((s for s in hand.seats if s.player_id == str(hero_id)), None)
        if seat is None:
            continue
        total += 1
        seen += int(len(seat.hole_cards) == 2)
    return seen, total


def preflop_range(hands: list, hero_id: int) -> dict[str, PositionRange]:
    """What hero actually held and did preflop, by position.

    ``hands`` is hero's own hands, e.g. ``store.player_hands(hero_id)``.

    Simplified on purpose: this is "what did you do with this hand from this
    seat," not split by whether you were opening or responding to a raise --
    an open-raise chart and a vs-raise chart are genuinely different objects,
    and collapsing them here is a real limitation, not an oversight to fix
    blindly later without deciding it is worth the added complexity.
    """
    by_position: dict[str, PositionRange] = {}
    for hand in hands:
        seat = next((s for s in hand.seats if s.player_id == str(hero_id)), None)
        if seat is None or len(seat.hole_cards) != 2:
            continue
        pos = by_position.setdefault(seat.position, PositionRange(seat.position))
        pos.hands += 1
        cls = hand_class(seat.hole_cards)
        pos.dealt_classes[cls] = pos.dealt_classes.get(cls, 0) + 1

        preflop_acts = [a.act for a in hand.actions
                        if a.seat == seat.seat and a.street is Street.PREFLOP
                        and not a.act.is_post]
        if not preflop_acts:
            continue     # a walk: no decision was ever offered
        if any(a in (Act.BET, Act.RAISE) for a in preflop_acts):
            pos.raised += 1
            pos.raised_classes[cls] = pos.raised_classes.get(cls, 0) + 1
            pos.played_classes[cls] = pos.played_classes.get(cls, 0) + 1
        elif Act.CALL in preflop_acts:
            pos.called += 1
            pos.played_classes[cls] = pos.played_classes.get(cls, 0) + 1
        elif Act.CHECK in preflop_acts:
            pos.checked += 1
        else:
            pos.folded += 1
    return by_position


def combined_grid(ranges: dict[str, PositionRange]) -> dict[str, tuple[int, int]]:
    """Every hand class summed across position: (times played, times dealt).

    For the chart, which shows "how often do you play this hand" rather than
    a position-by-position breakdown -- a real range chart is 13 of these,
    one per position, and this is the one-chart summary of it.
    """
    dealt: dict[str, int] = {}
    played: dict[str, int] = {}
    for pos in ranges.values():
        for cls, n in pos.dealt_classes.items():
            dealt[cls] = dealt.get(cls, 0) + n
        for cls, n in pos.played_classes.items():
            played[cls] = played.get(cls, 0) + n
    return {cls: (played.get(cls, 0), n) for cls, n in dealt.items()}


# ---------------------------------------------------------------------------
# fold grades: was this specific fold right, given the hand you actually had
# ---------------------------------------------------------------------------
#
# The naive version of this compares hero's raw percentile-vs-every-possible-
# holding to the pot-odds breakeven equity, as if the bet came from a random
# hand. It does not: somebody chose to bet, and a range that bets is stronger
# than random by construction, more so the bigger the bet. Measured that way,
# hero's folds came back 68% "mistakes," including laying down top pair to a
# river shove into a stack-sized pot -- a fold that is very unlikely to
# actually be wrong. The naive number is not conservative, it is just wrong,
# in the direction that makes hero look worse than the data supports.
#
# The fix already exists in this project: :mod:`villain.reads` fits a
# population model of what hand strength a bet of a given size, street,
# position and board typically represents, trained on the database's own
# revealed hands. Grading a fold against *that* -- did hero's hand outrank
# what a bet like this one usually is -- is the same standard the rest of
# the tool holds itself to: measure against a fitted baseline, not a
# textbook assumption.


def fit_population_model(store, progress=None, hands=None) -> StrengthModel:
    """The same population hand-strength model :mod:`villain.reads` fits,
    for grading hero's folds against what the bet actually represents.

    ``progress(done, total, phase)`` is passed through so a caller with a
    progress bar has something true to put in it. The walk over hands can be
    counted; fitting the trees cannot, and says so by reporting no total.
    """
    if hands is None:
        loading = (lambda done, total: progress(done, total, "loading")) if progress else None
        hands = store.player_hands(progress=loading)
    step = (lambda done, total: progress(done, total, "reading")) if progress else None
    rows = build_dataset(hands, progress=step)
    if progress:
        progress(0, 0, "fitting")
    return fit_strength(rows)


@dataclass
class FoldGrade:
    hand_id: str
    street: int
    hole_cards: tuple[str, ...]
    board: list[str]
    strength: float           # hero's percentile on this street, 0-1
    faced_strength: float     # population model's estimate for a bet like this one
    required_equity: float    # pot-odds price faced -- context, see `mistake`
    pot_before_bb: float
    to_call_bb: float

    @property
    def texture(self) -> str:
        return texture_label(self.board)

    @property
    def edge(self) -> float:
        """Percentile points hero's hand outranked what this bet typically
        represents in this database. The comparison that matters -- see the
        module note above for why raw percentile-vs-random is not it."""
        return self.strength - self.faced_strength

    @property
    def mistake(self) -> bool:
        # Both bars, not either: outranking what the bet usually represents
        # is not the same as having the raw equity to profit from calling at
        # this specific price, and a hand can clear one without the other.
        return self.edge > MARGIN and self.strength > self.required_equity

    def worth(self) -> float:
        """Rank-only: how much edge, on how much money. Not a bb figure --
        turning `edge` into real chips needs an equity model this does not
        have, so this only orders `worst()`, it does not price anything."""
        return self.edge * (self.pot_before_bb + self.to_call_bb)

    @property
    def summary(self) -> str:
        """Row-length form of `in_words` -- the full sentence explains itself
        once (in a tooltip); a worst-folds list of five does not need it
        spelled out five times."""
        return (f"you {self.strength:.0%} · usual {self.faced_strength:.0%} "
                f"· needed {self.required_equity:.0%}")

    @property
    def in_words(self) -> str:
        """One sentence with the numbers in it, matching the precedent
        villain.exploits.Leak.in_words already sets for a villain leak --
        FoldGrade never had this, and three raw percentiles with no verb was
        exactly why it read as noise."""
        return (f"Beat {self.strength:.0%} of the hands you could have held there; "
                f"a bet like that one usually comes from a hand beating only "
                f"{self.faced_strength:.0%} -- and calling only needed "
                f"{self.required_equity:.0%} to show a profit.")


@dataclass
class FoldReport:
    grades: list[FoldGrade]

    @property
    def graded(self) -> int:
        return len(self.grades)

    @property
    def mistakes(self) -> list[FoldGrade]:
        return [g for g in self.grades if g.mistake]

    @property
    def mistake_rate(self) -> float | None:
        return len(self.mistakes) / self.graded if self.graded else None

    def by_street(self) -> dict[int, tuple[int, int]]:
        """street -> (mistakes, graded)."""
        out: dict[int, tuple[int, int]] = {}
        for g in self.grades:
            m, n = out.get(g.street, (0, 0))
            out[g.street] = (m + int(g.mistake), n + 1)
        return out

    def by_texture(self) -> dict[str, tuple[int, int]]:
        """"wet"/"dry" -> (mistakes, graded)."""
        out: dict[str, tuple[int, int]] = {}
        for g in self.grades:
            m, n = out.get(g.texture, (0, 0))
            out[g.texture] = (m + int(g.mistake), n + 1)
        return out

    def worst(self, limit: int = 5) -> list[FoldGrade]:
        return sorted(self.mistakes, key=lambda g: -g.worth())[:limit]


def fold_grades(hands: list, hero_id: int, model: StrengthModel) -> FoldReport:
    """Grade every postflop fold hero made against the hand hero actually held.

    ``hands`` is hero's own hands, e.g. ``store.player_hands(hero_id)``.
    ``model`` is a fitted :class:`~villain.reads.StrengthModel` -- required
    rather than fitted internally, both because fitting it needs *every*
    player's hands, not just hero's, and because fitting costs real time and
    a caller grading folds more than once (a web request per page load) has
    to be able to fit it once and reuse it.

    Preflop is deliberately excluded: :func:`villain.reads.strength_by_street`
    needs a board, and preflop hand strength is a different question (equity
    against a *range*, not against every possible holding) -- that belongs
    with :func:`preflop_range`, not here.
    """
    grades: list[FoldGrade] = []
    for hand in hands:
        if not hand.board:
            continue
        seat = next((s for s in hand.seats if s.player_id == str(hero_id)), None)
        if seat is None or len(seat.hole_cards) != 2:
            continue
        strengths = strength_by_street(hand, {seat.seat: seat})
        view = HandView(hand)
        current_street = Street.PREFLOP
        last_aggro: Decision | None = None
        for decision in view.decisions():
            if decision.street is not current_street:
                current_street, last_aggro = decision.street, None
            act = decision.action.act
            if (decision.seat == seat.seat and act is Act.FOLD
                    and decision.street is not Street.PREFLOP and last_aggro is not None):
                strength = strengths.get((seat.seat, decision.street))
                to_call = decision.action.to_call
                pot_before = decision.action.pot_before
                if strength is not None and to_call > 0 and hand.big_blind:
                    grades.append(FoldGrade(
                        hand_id=hand.hand_id, street=int(decision.street),
                        hole_cards=seat.hole_cards, board=hand.board_at(decision.street),
                        strength=strength,
                        faced_strength=_predict_strength(model, hand, last_aggro),
                        required_equity=to_call / (pot_before + to_call),
                        pot_before_bb=pot_before / hand.big_blind,
                        to_call_bb=to_call / hand.big_blind,
                    ))
            if act.is_aggressive:
                last_aggro = decision
    return FoldReport(grades=grades)


def _predict_strength(model: StrengthModel, hand, decision: Decision) -> float:
    """What the population model expects a line like this one to represent.

    Used both for a bet hero folded to (:func:`fold_grades`) and a check
    hero made (:func:`missed_value`) -- the feature vector states its own
    action type (``is_bet``/``is_raise``/``is_call``/``is_check``), so the
    same call answers both "what does a bet like this usually mean" and
    "what does a check like this usually mean."

    ``unbiased`` is fixed at 0: this player's identity and card visibility
    are unknown, so the honest question is what the *typical*, mostly
    showdown-selected training row looks like on this line -- and that
    sample skews toward calling ranges and away from bluffs that took the
    pot down uncontested (see :mod:`villain.reads`'s module note), so the
    model's estimate here runs a little strong if anything. That is the safe
    direction for it to be wrong in: it under-flags hero's folds and missed
    value rather than over-flags them.
    """
    act = decision.action.act
    features = [
        0.0, float(decision.street),
        float(act is Act.BET), float(act is Act.RAISE),
        float(act is Act.CALL), float(act is Act.CHECK),
        decision.bet_fraction, float(decision.aggression_level),
        float(decision.has_initiative), float(decision.in_position),
        decision.action.pot_before / hand.big_blind,
        min((decision.action.think_ms or 0) / 1000.0, 60.0),
        float(decision.players_in),
        *texture(hand.board_at(decision.street)),
    ]
    return model.predict(features)


# ---------------------------------------------------------------------------
# missed value: was this check strong enough that betting made more money
# ---------------------------------------------------------------------------
#
# The mirror of fold grading, not a new idea: the same population model, the
# same "outrank what this line usually represents" comparison, the same
# reason it is only answerable for hero -- a villain's checks are never
# revealed either. A check always faces no bet (that is what distinguishes
# it from a call), so there is no pot-odds bar here the way fold_grades has
# one; the only question is whether the hand was better than what a check on
# that line usually is, and the model's features (street, position,
# initiative, players in, board) already condition on the context that makes
# a checked-back monster in position different from a check-fold out of it.
#
# MARGIN is not reused here. Folds have a strongly negative mean edge, so a
# tight margin rarely fires on noise. Checks do not: measured on the live
# database, mean edge is -0.03 with a stdev of 0.24 -- close to zero and
# wide, because most hands checking back are only mildly weaker than a
# typical check, not dramatically so the way a folded hand is dramatically
# weaker than a bet. At MARGIN (0.05), 40% of checks cross it -- noise
# dominating signal, the same failure the naive percentile-vs-random version
# of fold_grades had before it was fixed. 0.20 -- roughly the 80th
# percentile of the real distribution -- brings that to 19%, a rate in the
# neighbourhood of what the rest of the tool calls a leak worth a look
# rather than "nearly everything."
MISSED_VALUE_MARGIN = 0.20


@dataclass
class MissedValue:
    hand_id: str
    street: int
    hole_cards: tuple[str, ...]
    board: list[str]
    strength: float           # hero's percentile on this street, 0-1
    faced_strength: float     # population model's estimate for a check on this line
    pot_before_bb: float

    @property
    def texture(self) -> str:
        return texture_label(self.board)

    @property
    def edge(self) -> float:
        """Percentile points hero's hand outranked what a check on this line
        typically represents in this database."""
        return self.strength - self.faced_strength

    @property
    def missed(self) -> bool:
        return self.edge > MISSED_VALUE_MARGIN

    def worth(self) -> float:
        """Rank-only, same caveat as FoldGrade.worth: how much edge, on how
        much money, not a real bb figure."""
        return self.edge * self.pot_before_bb

    @property
    def summary(self) -> str:
        """Row-length form of `in_words` -- see FoldGrade.summary."""
        return f"you {self.strength:.0%} · usual {self.faced_strength:.0%}"

    @property
    def in_words(self) -> str:
        return (f"Beat {self.strength:.0%} of the hands you could have held there; "
                f"a check on that line usually comes from a hand beating only "
                f"{self.faced_strength:.0%}.")


@dataclass
class MissedValueReport:
    grades: list[MissedValue]

    @property
    def graded(self) -> int:
        return len(self.grades)

    @property
    def missed(self) -> list[MissedValue]:
        return [g for g in self.grades if g.missed]

    @property
    def missed_rate(self) -> float | None:
        return len(self.missed) / self.graded if self.graded else None

    def by_street(self) -> dict[int, tuple[int, int]]:
        """street -> (missed, graded)."""
        out: dict[int, tuple[int, int]] = {}
        for g in self.grades:
            m, n = out.get(g.street, (0, 0))
            out[g.street] = (m + int(g.missed), n + 1)
        return out

    def by_texture(self) -> dict[str, tuple[int, int]]:
        out: dict[str, tuple[int, int]] = {}
        for g in self.grades:
            m, n = out.get(g.texture, (0, 0))
            out[g.texture] = (m + int(g.missed), n + 1)
        return out

    def worst(self, limit: int = 5) -> list[MissedValue]:
        return sorted(self.missed, key=lambda g: -g.worth())[:limit]


def missed_value(hands: list, hero_id: int, model: StrengthModel) -> MissedValueReport:
    """Grade every postflop check hero made against the hand hero actually held.

    ``hands`` is hero's own hands, ``model`` a fitted
    :class:`~villain.reads.StrengthModel` -- see :func:`fold_grades` for why
    it is required rather than fitted here. Preflop excluded for the same
    reason it is everywhere else in this module.
    """
    grades: list[MissedValue] = []
    for hand in hands:
        if not hand.board:
            continue
        seat = next((s for s in hand.seats if s.player_id == str(hero_id)), None)
        if seat is None or len(seat.hole_cards) != 2:
            continue
        strengths = strength_by_street(hand, {seat.seat: seat})
        for decision in HandView(hand).decisions():
            if (decision.seat != seat.seat or decision.street is Street.PREFLOP
                    or decision.action.act is not Act.CHECK):
                continue
            strength = strengths.get((seat.seat, decision.street))
            if strength is None or not hand.big_blind:
                continue
            grades.append(MissedValue(
                hand_id=hand.hand_id, street=int(decision.street),
                hole_cards=seat.hole_cards, board=hand.board_at(decision.street),
                strength=strength,
                faced_strength=_predict_strength(model, hand, decision),
                pot_before_bb=decision.action.pot_before / hand.big_blind,
            ))
    return MissedValueReport(grades=grades)


# ---------------------------------------------------------------------------
# sizing tell: does the size of the bet change with the hand behind it
# ---------------------------------------------------------------------------

#: Below this many bets on one side of the split, a size gap is sample noise,
#: not a tell -- the same shape of guard exploits.py uses (MIN_OPPS) before
#: pricing a leak, applied to a comparison instead of a single frequency.
MIN_SIZING_HANDS = 8

#: A gap smaller than this, in pot fractions, is not worth calling a tell --
#: a chosen bar, like several of exploits.py's, not a derived one. 15 points
#: of pot is roughly the difference between a half-pot and a two-thirds-pot
#: bet, which is the kind of gap an attentive opponent actually notices.
SIZING_TELL_GAP = 0.15


@dataclass
class SizeBucket:
    label: str
    hands: int = 0
    total_fraction: float = 0.0

    @property
    def avg_size(self) -> float | None:
        return self.total_fraction / self.hands if self.hands else None


@dataclass
class SizingTell:
    #: street -> (bucket for hands >= median strength, bucket for < median)
    by_street: dict[int, tuple[SizeBucket, SizeBucket]]

    def gap(self, street: int) -> float | None:
        """Strong-hand average size minus weak-hand average size, or None
        when either side is too thin to compare."""
        pair = self.by_street.get(street)
        if not pair:
            return None
        strong, weak = pair
        if (strong.hands < MIN_SIZING_HANDS or weak.hands < MIN_SIZING_HANDS
                or strong.avg_size is None or weak.avg_size is None):
            return None
        return strong.avg_size - weak.avg_size

    def tells(self) -> list[tuple[int, float]]:
        """(street, gap) for every street with enough data and a real gap,
        biggest gap first."""
        found = []
        for street in self.by_street:
            gap = self.gap(street)
            if gap is not None and abs(gap) >= SIZING_TELL_GAP:
                found.append((street, gap))
        return sorted(found, key=lambda sg: -abs(sg[1]))

    def describe(self, street: int, lead: bool = True) -> str | None:
        """One sentence for a street with enough data to compare, or None.

        ``lead=False`` drops the "On the {street}, " opener -- for a caller
        that already shows the street as its own label next to the sentence
        (the web dashboard does; the CLI, with no separate street column,
        wants the sentence to carry it).
        """
        pair = self.by_street.get(street)
        if not pair:
            return None
        strong, weak = pair
        if strong.avg_size is None or weak.avg_size is None:
            return None
        verb = (f"On the {STREET_LABELS.get(street, street)}, you bet" if lead
               else "You bet")
        sentence = (f"{verb} {strong.avg_size:.0%} pot with your "
                   f"strongest hands and {weak.avg_size:.0%} pot with your weakest")
        gap = self.gap(street)
        if gap is not None and abs(gap) >= SIZING_TELL_GAP:
            sentence += " -- a gap big enough that an observant opponent could read your size."
        else:
            sentence += "."
        return sentence


def sizing_tell(hands: list, hero_id: int, progress=None) -> SizingTell:
    """Does hero bet bigger with better hands? The mirror of fold grading,
    asked of hero's aggression instead of hero's folds -- and, like the fold
    grades, a question that is only answerable at all because hero's hand
    strength is known on every action, not just the ones that reached
    showdown.

    Split at 0.5 -- the median of every hand the board allows, not a value
    fit from data -- into "top half" and "bottom half," and compare average
    bet-or-raise size between them. A real gap is a sizing tell: an observant
    opponent could read hand strength off bet size alone, without ever
    seeing a card.

    ``hands`` is hero's own hands, e.g. ``store.player_hands(hero_id)``.
    Preflop is excluded for the same reason it is in :func:`fold_grades`:
    :func:`villain.reads.strength_by_street` needs a board.

    ``progress(done, total)`` is the same callback :func:`build_dataset`
    takes: this walk calls ``strength_by_street`` per hand, and on a cold
    cache that is a long silence after the histories have already been
    read.
    """
    by_street: dict[int, tuple[SizeBucket, SizeBucket]] = {
        int(s): (SizeBucket("top half"), SizeBucket("bottom half"))
        for s in (Street.FLOP, Street.TURN, Street.RIVER)
    }
    total = len(hands)
    for at, hand in enumerate(hands):
        if progress is not None and at % 200 == 0:
            progress(at, total)
        if not hand.board:
            continue
        seat = next((s for s in hand.seats if s.player_id == str(hero_id)), None)
        if seat is None or len(seat.hole_cards) != 2:
            continue
        strengths = strength_by_street(hand, {seat.seat: seat})
        for decision in HandView(hand).decisions():
            if (decision.seat != seat.seat or decision.street is Street.PREFLOP
                    or decision.action.act not in (Act.BET, Act.RAISE)):
                continue
            strength = strengths.get((seat.seat, decision.street))
            if strength is None:
                continue
            strong, weak = by_street[int(decision.street)]
            bucket = strong if strength >= 0.5 else weak
            bucket.hands += 1
            bucket.total_fraction += decision.bet_fraction
    if progress is not None:
        progress(total, total)
    return SizingTell(by_street=by_street)


# ---------------------------------------------------------------------------
# timing tell: does think time change with the hand behind it
# ---------------------------------------------------------------------------
# The same split as sizing_tell, same reason it only works for hero, applied
# to Action.think_ms instead of bet_fraction: a snap bet and a tanked one are
# different information if they correlate with hand strength, and nobody's
# strength is known well enough to ask a villain this either.

#: Same shape of guard as MIN_SIZING_HANDS, its own name because it gates a
#: different comparison.
MIN_TIMING_HANDS = 8

#: A chosen bar, not a derived one: two seconds is long enough to not be
#: click noise and short enough that a real habit produces it.
TIMING_TELL_GAP = 2.0

#: Same cap villain.reads applies before feeding think time to the population
#: model -- one disconnect or one multi-tabled hand should not own the average.
THINK_CAP_S = 60.0


@dataclass
class TimeBucket:
    label: str
    hands: int = 0
    total_think_s: float = 0.0

    @property
    def avg_think_s(self) -> float | None:
        return self.total_think_s / self.hands if self.hands else None


@dataclass
class TimingTell:
    #: street -> (bucket for hands >= median strength, bucket for < median)
    by_street: dict[int, tuple[TimeBucket, TimeBucket]]

    def gap(self, street: int) -> float | None:
        """Strong-hand average think time minus weak-hand average, or None
        when either side is too thin to compare. Positive means hero thinks
        longer with the better half; negative means hero thinks longer with
        the worse half -- the classic tank-as-bluff tell."""
        pair = self.by_street.get(street)
        if not pair:
            return None
        strong, weak = pair
        if (strong.hands < MIN_TIMING_HANDS or weak.hands < MIN_TIMING_HANDS
                or strong.avg_think_s is None or weak.avg_think_s is None):
            return None
        return strong.avg_think_s - weak.avg_think_s

    def tells(self) -> list[tuple[int, float]]:
        """(street, gap) for every street with enough data and a real gap,
        biggest gap first."""
        found = []
        for street in self.by_street:
            gap = self.gap(street)
            if gap is not None and abs(gap) >= TIMING_TELL_GAP:
                found.append((street, gap))
        return sorted(found, key=lambda sg: -abs(sg[1]))

    def describe(self, street: int, lead: bool = True) -> str | None:
        """One sentence for a street with enough data to compare, or None.
        ``lead=False`` -- see SizingTell.describe."""
        pair = self.by_street.get(street)
        if not pair:
            return None
        strong, weak = pair
        if strong.avg_think_s is None or weak.avg_think_s is None:
            return None
        verb = (f"On the {STREET_LABELS.get(street, street)}, you took" if lead
               else "You took")
        sentence = (f"{verb} {strong.avg_think_s:.1f}s to bet your "
                   f"strongest hands and {weak.avg_think_s:.1f}s with your weakest")
        gap = self.gap(street)
        if gap is not None and abs(gap) >= TIMING_TELL_GAP:
            tell_direction = "tank with your bluffs" if gap < 0 else "take longer with your strong hands"
            sentence += f" -- a gap big enough to suggest you {tell_direction}."
        else:
            sentence += "."
        return sentence


def timing_tell(hands: list, hero_id: int, progress=None) -> TimingTell:
    """Does hero take longer to act with one half of the hand-strength range
    than the other? Scoped to bets and raises, matching :func:`sizing_tell`
    exactly -- the two are read together, "does hero's aggression carry a
    tell," rather than trying to grade timing on every action type from day
    one.

    ``hands`` is hero's own hands. Preflop excluded for the same reason it is
    everywhere else in this module.
    """
    by_street: dict[int, tuple[TimeBucket, TimeBucket]] = {
        int(s): (TimeBucket("top half"), TimeBucket("bottom half"))
        for s in (Street.FLOP, Street.TURN, Street.RIVER)
    }
    total = len(hands)
    for at, hand in enumerate(hands):
        if progress is not None and at % 200 == 0:
            progress(at, total)
        if not hand.board:
            continue
        seat = next((s for s in hand.seats if s.player_id == str(hero_id)), None)
        if seat is None or len(seat.hole_cards) != 2:
            continue
        strengths = strength_by_street(hand, {seat.seat: seat})
        for decision in HandView(hand).decisions():
            if (decision.seat != seat.seat or decision.street is Street.PREFLOP
                    or decision.action.act not in (Act.BET, Act.RAISE)):
                continue
            strength = strengths.get((seat.seat, decision.street))
            if strength is None:
                continue
            strong, weak = by_street[int(decision.street)]
            bucket = strong if strength >= 0.5 else weak
            bucket.hands += 1
            bucket.total_think_s += min((decision.action.think_ms or 0) / 1000.0, THINK_CAP_S)
    if progress is not None:
        progress(total, total)
    return TimingTell(by_street=by_street)


# ---------------------------------------------------------------------------
# range narrowing: does hero's continuing range actually get stronger
# ---------------------------------------------------------------------------
# Purely descriptive -- no model, no comparison to a bar. A continuing range
# is supposed to narrow to the hands that held up, so average hand strength
# among hands still live should trend upward street by street. Whether it
# does is a sanity check on hero's own postflop discipline, not a priced leak.


@dataclass
class StreetStrength:
    street: int
    hands: int
    avg_strength: float


def range_narrowing(hands: list, hero_id: int, progress=None) -> list[StreetStrength]:
    """Average hand strength among hero's hands still live at each street,
    flop through river. ``hands`` is hero's own hands.

    "Still live" uses HandView.saw, which already answers exactly this
    (reconstructed from who folded when, not from who happened to act) --
    the same building block the rest of the stats engine uses to avoid
    double-counting a player who folded before a street was ever dealt.
    """
    totals: dict[int, list[float]] = {int(s): [] for s in (Street.FLOP, Street.TURN, Street.RIVER)}
    total = len(hands)
    for at, hand in enumerate(hands):
        if progress is not None and at % 200 == 0:
            progress(at, total)
        if not hand.board:
            continue
        seat = next((s for s in hand.seats if s.player_id == str(hero_id)), None)
        if seat is None or len(seat.hole_cards) != 2:
            continue
        strengths = strength_by_street(hand, {seat.seat: seat})
        view = HandView(hand)
        for street in (Street.FLOP, Street.TURN, Street.RIVER):
            if seat.seat not in view.saw.get(street, ()):
                continue
            strength = strengths.get((seat.seat, street))
            if strength is not None:
                totals[int(street)].append(strength)
    if progress is not None:
        progress(total, total)
    return [StreetStrength(street=s, hands=len(vals), avg_strength=sum(vals) / len(vals))
            for s, vals in totals.items() if vals]
