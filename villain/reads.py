"""What they actually had: a hand-strength model trained on revealed cards.

Frequencies tell you how often somebody bets. They do not tell you what they
bet *with*, and that is the question that decides whether to call. This module
learns the mapping from a line -- street, action, sizing, position, board
texture, time taken -- to the strength of the hand behind it, using the hands
where cards were revealed as labels. A player's own residual against that model
is the read: "when this player bets the river they average 20 percentile points
weaker than the field does" is directly actionable in a way that "they bet the
river 55% of the time" is not.

**The bias, stated plainly.** Villains' cards are only revealed at showdown,
and hands that reach showdown are not a random sample of hands played -- they
skew toward calling lines and away from the bluffs that took the pot down
uncontested. So a model trained purely on villain showdowns *underestimates*
how weak the betting ranges are. Two things reduce it and neither eliminates
it:

* The exporting player's own cards are visible on every hand, including hands
  they folded, so their rows are an unbiased sample and are marked as such.
* Rows are labeled with strength *at the street the action was taken*, not at
  the end, so a flop bet is scored against the flop board rather than against
  a river that had not arrived yet.

Treat the population model as a baseline and the per-player residual as the
signal. Both come with sample counts, and neither is worth anything under a few
hundred rows -- ``fit`` refuses rather than returning a model that looks
authoritative and is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .cards import card_ids, evaluate
from .model import Act, Hand, Street
from .stats import HandView

#: Below this many labeled rows, a fitted model is decoration.
MIN_ROWS = 300

#: Pseudo-rows of prior for a per-player residual, shrunk toward zero.
RESIDUAL_PRIOR = 12.0

FEATURES = [
    # Whether the row's cards were visible regardless of showdown. Without it
    # the baseline blends two differently-selected samples -- the exporting
    # player's hands are seen whatever happens, everyone else's only when they
    # got to showdown -- and treats them as one population.
    "unbiased",
    "street", "is_bet", "is_raise", "is_call", "is_check",
    "bet_fraction", "aggression_level", "has_initiative", "in_position",
    "pot_bb", "think_s", "players_in",
    "board_paired", "board_suited", "board_connected", "board_high",
]


@dataclass
class Row:
    player_id: str
    features: list[float]
    strength: float          # percentile of their hand on that street, 0-1
    unbiased: bool           # cards known regardless of showdown
    street: int
    action: str


#: (player_id, street, action) -- a residual pooled across every line a
#: player takes averages away the signal along with the noise: someone who
#: over-bluffs the river and under-bluffs the flop pools to roughly zero.
#: Split this way, on a real database 19 street x action cells across 11
#: players clear the reporting bar where the pooled version found 2 of 30.
Line = tuple[str, int, str]


@dataclass
class StrengthModel:
    """Population model plus residuals split by player and by line."""

    rows: int = 0
    unbiased_rows: int = 0
    mae: float | None = None
    residuals: dict[Line, tuple[float, float]] = field(default_factory=dict)
    _model: object | None = None

    def predict(self, features: list[float]) -> float:
        if self._model is None:
            return 0.5
        return float(np.clip(self._model.predict(np.array(features)[None, :])[0], 0.0, 1.0))

    def offset(self, player_id: str, street: int, action: str) -> tuple[float, float]:
        """(shrunk residual, rows) for one player on one street x action cell.

        Negative means weaker than the field.
        """
        return self.residuals.get((player_id, street, action), (0.0, 0.0))

    def lines(self, player_id: str) -> list[tuple[int, str, float, float]]:
        """Every (street, action, offset, rows) cell recorded for one player."""
        return [(street, action, offset, n)
                for (pid, street, action), (offset, n) in self.residuals.items()
                if pid == player_id]

    def read(self, player_id: str) -> str | None:
        """The single most telling line for this player, if any clears the bar."""
        candidates = [c for c in self.lines(player_id) if c[3] >= 6 and abs(c[2]) >= 0.06]
        if not candidates:
            return None
        street, action, offset, n = max(candidates, key=lambda c: abs(c[2]))
        direction = "weaker" if offset < 0 else "stronger"
        advice = ("call them down wider" if offset < 0
                  else "give their bets more credit")
        return (f"on {Street(street).name.lower()} {action}s, shows up "
                f"{abs(offset) * 100:.0f} percentile points {direction} than the "
                f"field ({n:.0f} revealed hands) -- {advice}")


class NotEnoughData(ValueError):
    pass


def build_dataset(hands: list[Hand]) -> list[Row]:
    """Every action whose player's cards are known, labeled by strength."""
    rows: list[Row] = []
    for hand in hands:
        if not hand.board:
            continue
        view = HandView(hand)
        showdown = view.showdown()
        known = {s.seat: s for s in hand.seats if len(s.hole_cards) == 2}
        if not known:
            continue
        strengths = strength_by_street(hand, known)
        for decision in view.decisions():
            seat = known.get(decision.seat)
            if seat is None or decision.street is Street.PREFLOP:
                continue
            if decision.action.act is Act.FOLD:
                continue     # a folded hand has no strength worth predicting
            strength = strengths.get((decision.seat, decision.street))
            if strength is None:
                continue
            act = decision.action.act
            # Cards visible even when the hand did not go to showdown means
            # this row is not selected on the outcome. Fed to the model as a
            # feature, not just recorded on the row -- otherwise the baseline
            # blends the exporting player's unbiased rows with everyone
            # else's showdown-selected ones as if they were one population.
            unbiased = seat.seat not in showdown
            rows.append(Row(
                player_id=seat.player_id,
                features=[
                    float(unbiased),
                    float(decision.street),
                    float(act is Act.BET), float(act is Act.RAISE),
                    float(act is Act.CALL), float(act is Act.CHECK),
                    decision.bet_fraction,
                    float(decision.aggression_level),
                    float(decision.has_initiative), float(decision.in_position),
                    decision.action.pot_before / hand.big_blind,
                    min((decision.action.think_ms or 0) / 1000.0, 60.0),
                    float(decision.players_in),
                    *texture(hand.board_at(decision.street)),
                ],
                strength=strength,
                unbiased=unbiased,
                street=int(decision.street),
                action=act.name.lower(),
            ))
    return rows


def fit(rows: list[Row], random_state: int = 0) -> StrengthModel:
    """Fit the population model and each player's residual against it."""
    if len(rows) < MIN_ROWS:
        raise NotEnoughData(
            f"need {MIN_ROWS} labeled rows to fit a strength model, have {len(rows)}; "
            "keep importing sessions")

    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.model_selection import cross_val_predict

    x = np.array([r.features for r in rows], dtype=float)
    y = np.array([r.strength for r in rows], dtype=float)

    model = GradientBoostingRegressor(
        n_estimators=180, max_depth=3, learning_rate=0.06,
        subsample=0.85, random_state=random_state)
    # Residuals come from out-of-fold predictions: a player's read must not be
    # measured against a model that already memorised their hands.
    out_of_fold = cross_val_predict(model, x, y, cv=min(5, max(2, len(rows) // 60)))
    model.fit(x, y)

    residuals: dict[Line, list[float]] = {}
    for row, predicted in zip(rows, out_of_fold):
        key = (row.player_id, row.street, row.action)
        residuals.setdefault(key, []).append(row.strength - predicted)

    fitted = StrengthModel(
        rows=len(rows),
        unbiased_rows=sum(1 for r in rows if r.unbiased),
        mae=float(np.mean(np.abs(y - out_of_fold))),
        residuals={
            key: (float(np.sum(values) / (len(values) + RESIDUAL_PRIOR)), float(len(values)))
            for key, values in residuals.items()
        },
    )
    fitted._model = model
    return fitted


#: Content-keyed memo for :func:`strength_by_street` (a pure function of the
#: board, the known holes and which streets were reached). Bounded and cleared
#: wholesale rather than evicted per entry -- one hero build refills it from
#: cold and it is never read across databases within a request.
_STRENGTH_CACHE: dict = {}
_STRENGTH_CACHE_MAX = 200_000


#: Sorted scores for every holding a board allows, keyed by the board alone.
#: The universe depends on nothing else -- not the players, not the street's
#: action -- but the result cache below is keyed by the hole cards too, so
#: across a database of distinct hands it never hit and every hand rebuilt the
#: ~1,100-holding universe from scratch. Flops repeat often enough over tens of
#: thousands of hands to make this the difference.
_BOARD_CACHE: dict[tuple[str, ...], tuple] = {}
#: Entries, not bytes -- but each one is ~15 KB, so this is the memory budget
#: in disguise: about 45 MB.
#:
#: It was 40,000, which is 1.2 GB. Measured over a 71,456-hand database that
#: bought a 2.1% hit rate: boards barely repeat (72,289 distinct across 102,184
#: lookups, ~1.4x reuse), so no reachable cache size makes this cheap. Paying
#: over a gigabyte for it is what a browser cannot survive, and the tool runs
#: in one.
_BOARD_CACHE_MAX = 3_000


def _board_universe(board: tuple[str, ...]):
    """``(sorted scores, score by card pair)`` for every holding this board allows."""
    hit = _BOARD_CACHE.get(board)
    if hit is not None:
        return hit
    board_ids = card_ids(list(board)).astype(np.int64)
    # Vectorised, not a Python double loop over ~45-49 cards: that loop ran
    # once per street per hand -- tens of thousands of times over a real
    # database -- and was most of build_dataset's cost.
    mask = np.ones(52, dtype=bool)
    mask[board_ids] = False
    live = np.nonzero(mask)[0]
    i, j = np.triu_indices(len(live), k=1)
    combos = np.stack([live[i], live[j]], axis=1).astype(np.int64)
    seven = np.concatenate(
        [combos, np.repeat(board_ids[None, :], len(combos), axis=0)], axis=1)
    scores = evaluate(seven)
    # A flat 52x52 table so a pair of card ids indexes its own score directly.
    # int32, not int64. A hand score is at most ~9.2 million, so half of every
    # cached entry was leading zeros -- and this cache is measured in hundreds
    # of megabytes, not kilobytes.
    lookup = np.zeros(52 * 52, dtype=np.int32)
    lo = np.minimum(combos[:, 0], combos[:, 1])
    hi = np.maximum(combos[:, 0], combos[:, 1])
    lookup[lo * 52 + hi] = scores
    result = (np.sort(scores).astype(np.int32), lookup)
    if len(_BOARD_CACHE) >= _BOARD_CACHE_MAX:
        # Drop the oldest half rather than everything. Clearing outright threw
        # away the entries most likely to be asked for next, so a database with
        # more distinct boards than the cap kept paying full price for a cache
        # it also paid full memory for.
        for old_key in list(_BOARD_CACHE)[:_BOARD_CACHE_MAX // 2]:
            del _BOARD_CACHE[old_key]
    _BOARD_CACHE[board] = result
    return result


def strength_by_street(hand: Hand, known: dict) -> dict[tuple[int, Street], float]:
    """Percentile of each known hand on each street it was live for.

    Measured against every holding the board allows, because "top pair" means
    something different on a dry board than on a four-flush one.

    Public because :mod:`villain.hero` needs the same calculation for hero's
    folds, which this module's own dataset deliberately excludes ("a folded
    hand has no strength worth predicting" is true when the strength has to
    be inferred from betting patterns; it is false when the hand is already
    known).
    """
    # Pure in (board, known holes, which streets were reached): the same board
    # is scored once, not once per hero feature. Five features each walk hero's
    # hands calling this, so without the cache the ~1,100-holding universe for
    # every board is rebuilt five times over. Keyed by content, so any genuine
    # repeat -- and a repeat is all a cross-hand "collision" can be, since the
    # answer depends on nothing else -- is a correct hit.
    ckey = (
        tuple(hand.board),
        tuple(sorted((seat, tuple(p.hole_cards)) for seat, p in known.items())),
        (hand.reached(Street.FLOP), hand.reached(Street.TURN), hand.reached(Street.RIVER)),
    )
    cached = _STRENGTH_CACHE.get(ckey)
    if cached is not None:
        return cached

    out: dict[tuple[int, Street], float] = {}
    for street in (Street.FLOP, Street.TURN, Street.RIVER):
        board = hand.board_at(street)
        if len(board) < 3 or not hand.reached(street):
            continue
        board_ids = card_ids(board).astype(np.int64)
        universe, lookup = _board_universe(tuple(board))
        for seat, player in known.items():
            hole = card_ids(player.hole_cards).astype(np.int64)
            if set(hole.tolist()) & set(board_ids.tolist()):
                continue
            # The player's two cards are one of the combos the universe was
            # built from, so their score is already in it -- look it up rather
            # than paying a second evaluate for a single row.
            a, b = int(hole[0]), int(hole[1])
            score = int(lookup[min(a, b) * 52 + max(a, b)])
            out[(seat, street)] = float(
                np.searchsorted(universe, score, side="left") / len(universe))
    if len(_STRENGTH_CACHE) >= _STRENGTH_CACHE_MAX:
        _STRENGTH_CACHE.clear()          # bound the memory; the next build refills
    _STRENGTH_CACHE[ckey] = out
    return out


def texture(board: list[str]) -> tuple[float, float, float, float]:
    """Paired, suited, connected, high -- the four things that change ranges."""
    if len(board) < 3:
        return (0.0, 0.0, 0.0, 0.0)
    ranks = [card_ids([c])[0] // 4 for c in board]
    suits = [card_ids([c])[0] % 4 for c in board]
    paired = float(len(set(ranks)) < len(ranks))
    suited = float(max(suits.count(s) for s in set(suits)) >= 3)
    spread = max(ranks) - min(ranks)
    connected = float(spread <= 4)
    high = float(max(ranks) >= 10)      # queen or better
    return (paired, suited, connected, high)
