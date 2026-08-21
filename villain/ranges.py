"""What a villain can actually hold here, and where this hand sits inside it.

A measured frequency is a threshold *within a range*, never within the deck.
Every statistic in a profile is ``hits / opportunities`` where the
opportunities are conditioned on that player's own earlier actions:
``four_bet`` is counted only over the hands they opened *and* then faced a
3-bet with, ``check_raise:flop`` only over the flops they saw. Reading such a
number as a cut on the full dealt distribution is a category error, and it is
not a small one -- a player who 4-bets 16% of the range they opened with is
4-betting about 6% of all hands, so gating on "the top 16% of everything"
opens the 4-bet to T9o.

This module carries the missing half: each seat's own range, narrowed by each
action they take, so the policy can ask *where does this hand rank among the
hands I could hold right now* and get an answer the frequency is actually
measured against.

Two orderings, because the question changes with the street. Preflop a range
is ranked by the static hand-class tables; postflop by *playability* -- made
hand plus draws -- so a combo draw outranks 72o the way it does at a table.
A second, made-hand-only measure is kept for prices (pot odds are a claim
about equity, not about which slice of a range a frequency cut). Both are
computed once per street and cached -- the expensive part is the seven-card
evaluation, and it does not depend on which seat is asking.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np

from .cards import evaluate

#: Every two-card combination, as an ``(1326, 2)`` array of card ids.
_I, _J = np.triu_indices(52, k=1)
COMBOS = np.stack([_I, _J], axis=1).astype(np.int64)
N_COMBOS = len(COMBOS)

#: ``(52, 1326)`` -- whether each card appears in each combo. Used to knock
#: out the combos a known card makes impossible.
_USES = np.zeros((52, N_COMBOS), dtype=bool)
_USES[COMBOS[:, 0], np.arange(N_COMBOS)] = True
_USES[COMBOS[:, 1], np.arange(N_COMBOS)] = True

#: Index of each combo in ``COMBOS``, for looking a specific holding up.
_INDEX = {}
for _k, (_a, _b) in enumerate(COMBOS):
    _INDEX[(int(_a), int(_b))] = _k
    _INDEX[(int(_b), int(_a))] = _k


def index_of(hole) -> int:
    """Row in :data:`COMBOS` for a two-card holding, either order."""
    a, b = int(hole[0]), int(hole[1])
    return _INDEX[(a, b)]


def _combo_class_names() -> list[str]:
    """``(1326,)`` -- the 169-class name ("AKs", "77") of every combo."""
    from .cards import RANKS
    ranks = COMBOS // 4
    suited = (COMBOS[:, 0] % 4) == (COMBOS[:, 1] % 4)
    hi, lo = ranks.max(axis=1), ranks.min(axis=1)
    names = []
    for k in range(N_COMBOS):
        h, l_ = int(hi[k]), int(lo[k])
        names.append(RANKS[h] * 2 if h == l_
                     else RANKS[h] + RANKS[l_] + ("s" if suited[k] else "o"))
    return names


CLASS_NAMES = _combo_class_names()


def class_scores(score_by_class: dict[str, float]) -> np.ndarray:
    """Spread a per-class table (``{"AKs": 0.97, ...}``) over all 1,326 combos."""
    return np.array([score_by_class.get(n, 0.5) for n in CLASS_NAMES])


def _within_pct(w: np.ndarray, order: np.ndarray) -> np.ndarray:
    """Percentile of every combo *within* the weighted range ``w``.

    Ties share their group's midpoint, which is what makes the measure usable
    as a frequency cut: all six combos of a pocket pair have to sit at the same
    place, or "the top 4%" would slice a hand class in half.
    """
    total = w.sum()
    if total <= 0:
        return np.full(N_COMBOS, 0.5)
    uniq, inv = np.unique(order, return_inverse=True)
    group = np.bincount(inv, weights=w, minlength=len(uniq))
    below = np.concatenate([[0.0], np.cumsum(group)[:-1]])
    return (below[inv] + group[inv] / 2.0) / total


def _draw_bonus(rows: np.ndarray, board: np.ndarray) -> np.ndarray:
    """Playability bump so a flush draw is not ranked with 72o.

    Made-hand evaluation cannot see four-to-a-flush or an open-ender, so
    every polarised bluff that used that ranking was a naked ace-high --
    never a draw. The bonuses are in the same 0-1 units as a percentile
    so they can be added to one.
    """
    n = len(rows)
    if n == 0 or len(board) >= 5:
        return np.zeros(n)
    cards = np.concatenate(
        [rows, np.repeat(board[None, :], n, axis=0)], axis=1)
    suits = cards % 4
    ranks = cards // 4
    suit_counts = (suits[:, :, None] == np.arange(4)).sum(axis=1)
    flush_draw = suit_counts.max(axis=1) == 4

    bits = np.zeros(n, dtype=np.uint16)
    for i in range(cards.shape[1]):
        bits |= (np.uint16(1) << ranks[:, i].astype(np.uint16))

    oesd = np.zeros(n, dtype=bool)
    for start in range(10):
        mask = np.uint16((0b1111 << start) & 0x1FFF)
        oesd |= (bits & mask) == mask
    gut = np.zeros(n, dtype=bool)
    for start in range(9):
        mask = np.uint16((0b11111 << start) & 0x1FFF)
        m = bits & mask
        cnt = np.zeros(n, dtype=np.int8)
        for b in range(5):
            cnt += ((m >> (start + b)) & 1).astype(np.int8)
        gut |= cnt == 4
    wheel = bits & np.uint16(0x100F)          # A2345
    wcnt = np.zeros(n, dtype=np.int8)
    for b in (0, 1, 2, 3, 12):
        wcnt += ((wheel >> b) & 1).astype(np.int8)
    gut |= wcnt == 4
    gut &= ~oesd

    board_max = int(board.max() // 4)
    overcards = (ranks[:, 0] > board_max) & (ranks[:, 1] > board_max)

    bonus = np.zeros(n)
    combo = flush_draw & (oesd | gut)
    bonus = np.where(combo, 0.35, bonus)
    bonus = np.where(~combo & flush_draw, 0.22, bonus)
    bonus = np.where(~combo & ~flush_draw & oesd, 0.18, bonus)
    bonus = np.where(~combo & ~flush_draw & ~oesd & gut, 0.08, bonus)
    bonus = np.where((bonus == 0.0) & overcards, 0.05, bonus)
    return bonus


class _BoardCache:
    """Made-hand strength and playability of every live combo on one board.

    The seven-card evaluation is the expensive step and it is the same for
    every seat, so it is done once per board and shared.
    """

    def __init__(self, board: list[int]):
        self.board = tuple(board)
        board_ids = np.array(board, dtype=np.int64)
        live = ~_USES[board_ids].any(axis=0)          # combos not using a board card
        self.live = live
        rows = COMBOS[live]
        seven = np.concatenate(
            [rows, np.repeat(board_ids[None, :], len(rows), axis=0)], axis=1)
        scores = evaluate(seven)
        self.score = np.full(N_COMBOS, -1, dtype=np.int64)
        self.score[live] = scores
        # Percentile of each live combo against every other live combo, the
        # "vs a random holding" measure. Kept because a few decisions (pot
        # odds, raw equity floors) genuinely want it.
        order = np.sort(scores)
        self.pct = np.zeros(N_COMBOS)
        self.pct[live] = np.searchsorted(order, scores, side="left") / max(len(order), 1)
        # Frequency cuts use playability: made hand plus draws. Without the
        # draw term a polarised bluff is always the weakest made hand, never
        # a combo draw, and the bot cannot check-raise a flush draw.
        self.play = self.pct.copy()
        self.play[live] = self.pct[live] + _draw_bonus(rows, board_ids)


class Ranges:
    """Every seat's own range for one hand, narrowed as they act.

    The weights are per-combo and start uniform. A seat's range is narrowed
    only by that seat's own actions -- this is what *they* could hold, which is
    the denominator their own measured frequencies were counted over.
    """

    def __init__(self, n_seats: int):
        self.n = n_seats
        self.w = np.ones((n_seats, N_COMBOS))
        self._cache: _BoardCache | None = None

    # -- board ---------------------------------------------------------------

    def board_cache(self, board: list[int]) -> _BoardCache:
        if self._cache is None or self._cache.board != tuple(board):
            self._cache = _BoardCache(board)
        return self._cache

    def board_percentile(self, hole, board: list[int]) -> float:
        """Percentile against every holding an *opponent* could have.

        Card removal applies here and nowhere else in this module: an opponent
        cannot hold the cards in your hand, so they come out of the universe.
        A range percentile is the opposite case -- your own hand is a member of
        your own range -- which is why the two measures are separate calls.
        """
        c = self.board_cache(board)
        others = c.live & ~_USES[list(hole)].any(axis=0)
        mine = c.score[index_of(hole)]
        n = int(others.sum())
        return float((c.score[others] < mine).sum() / n) if n else 0.5

    # -- the range itself ----------------------------------------------------

    def live_weights(self, seat: int, board: list[int] | None) -> np.ndarray:
        w = self.w[seat]
        if board:
            w = w * self.board_cache(board).live
        return w

    def percentile(self, seat: int, hole, order: np.ndarray,
                   board: list[int] | None = None) -> float:
        """Where ``hole`` ranks inside this seat's current range.

        ``order`` is a per-combo sort key -- the preflop class table, or the
        board strength. This is the number a measured frequency is a cut on.
        """
        w = self.live_weights(seat, board)
        return float(_within_pct(w, order)[index_of(hole)])

    def narrow(self, seat: int, order: np.ndarray, bands,
               board: list[int] | None = None) -> None:
        """Keep only the percentile ``bands`` of this seat's range.

        ``bands`` is a list of ``(lo, hi)`` pairs in within-range percentile
        terms -- a value bet keeps ``[(1 - f, 1.0)]``, a polarised bet keeps
        the value slice and the bluff slice together, and a call keeps the
        band between folding and raising. Uses the same percentile convention
        as :meth:`percentile`, so a hand that cleared a gate is always inside
        the band that gate kept.
        """
        w = self.live_weights(seat, board)
        if w.sum() <= 0:
            return
        pct = _within_pct(w, order)
        keep = np.zeros(N_COMBOS, dtype=bool)
        for lo, hi in bands:
            keep |= (pct >= lo) & (pct <= hi if hi >= 1.0 else pct < hi)
        narrowed = np.where(keep, w, 0.0)
        if narrowed.sum() <= 0:                 # never leave a seat with nothing
            return
        self.w[seat] = narrowed

    def top_classes(self, seat: int, n: int = 10,
                    board: list[int] | None = None) -> list[tuple[str, float]]:
        """The heaviest hand classes still in this seat's range, for the review."""
        w = self.live_weights(seat, board)
        total = float(w.sum())
        if total <= 0:
            return []
        shares: dict[str, float] = defaultdict(float)
        for i, name in enumerate(CLASS_NAMES):
            if w[i] > 0:
                shares[name] += float(w[i])
        ranked = sorted(shares.items(), key=lambda kv: -kv[1])[:n]
        return [(name, wt / total) for name, wt in ranked]

    def reset(self, seat: int) -> None:
        self.w[seat] = 1.0
