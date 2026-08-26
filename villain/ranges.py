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


def _above_frac(w: np.ndarray, order: np.ndarray) -> np.ndarray:
    """Share of ``w`` that strictly outranks each combo.

    This is the cut "top X%" actually means when a made hand is a pile:
    every ten on T-T-9-9-x is the same boat, and the midpoint of that pile
    is the middle of the range. Weight *above* the pile is ~0, so the whole
    pile is in the top of the range.
    """
    total = w.sum()
    if total <= 0:
        return np.zeros(N_COMBOS)
    uniq, inv = np.unique(order, return_inverse=True)
    group = np.bincount(inv, weights=w, minlength=len(uniq))
    above = np.cumsum(group[::-1])[::-1] - group
    return above[inv] / total


def _draw_bonus(rows: np.ndarray, board: np.ndarray) -> np.ndarray:
    """Playability bump so a flush draw is not ranked with 72o.

    Made-hand evaluation cannot see four-to-a-flush or an open-ender, so every
    polarised bluff off that ranking is a naked ace-high. Bonuses are in
    percentile units so :class:`_BoardCache` folds them into one.

    A draw is something the hand does not have yet: both sides stop counting
    once the hand is made, or a straight outranks quads in the playability
    order the postflop cuts use.
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

    # Four in a row is only open-ended when both ends are live, which is every
    # window up to T-J-Q-K (a nine or an ace) and stops there. J-Q-K-A takes a
    # ten and nothing else -- four outs, not eight -- and scoring it as an
    # open-ender paid it the same 0.18 a genuine wrap gets. Left to the
    # gutshot pass below, whose T-to-A window already finds it.
    oesd = np.zeros(n, dtype=bool)
    for start in range(9):
        mask = np.uint16((0b1111 << start) & 0x1FFF)
        oesd |= (bits & mask) == mask
    made_straight = np.zeros(n, dtype=bool)
    gut = np.zeros(n, dtype=bool)
    for start in range(9):
        mask = np.uint16((0b11111 << start) & 0x1FFF)
        m = bits & mask
        cnt = np.zeros(n, dtype=np.int8)
        for b in range(5):
            cnt += ((m >> (start + b)) & 1).astype(np.int8)
        made_straight |= cnt == 5
        gut |= cnt == 4
    wheel = bits & np.uint16(0x100F)          # A2345
    wcnt = np.zeros(n, dtype=np.int8)
    for b in (0, 1, 2, 3, 12):
        wcnt += ((wheel >> b) & 1).astype(np.int8)
    made_straight |= wcnt == 5
    gut |= wcnt == 4
    gut &= ~oesd
    # Nothing left to draw to on this axis.
    oesd &= ~made_straight
    gut &= ~made_straight

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
        #
        # Folded into the headroom above the made-hand percentile rather than
        # added flat. A flat add lifted hands that were already near the top
        # past hands that genuinely beat them -- on one paired board a made
        # straight scored 1.149 against quads at 0.999, and seventy combos
        # came out above 1.0 -- and the ordering *is* the frequency cut, so
        # that reordering reached every postflop decision the policy makes.
        # Scaling leaves a weak draw with almost the whole bonus, which is the
        # entire point of the term, and a premium with almost none, which is
        # correct: it has nothing left to draw to.
        self.play = self.pct.copy()
        bonus = _draw_bonus(rows, board_ids)
        self.play[live] = self.pct[live] + bonus * (1.0 - self.pct[live])


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

    def better_frac(self, seat: int, hole, order: np.ndarray,
                    board: list[int] | None = None) -> float:
        """Share of this seat's range that strictly outranks ``hole``.

        Percentile-within-range puts a tie at its midpoint, which is right
        for not slicing a 6-combo pair in half and wrong for a made hand
        that half the range shares. Every ten on a double-paired ten board
        is the same boat; the midpoint of that pile is ~0.6, and a 21%
        continue cut folds it. The weight *above* the hand does not: if
        nothing in the range beats it, the whole pile continues.
        """
        w = self.live_weights(seat, board)
        total = float(w.sum())
        if total <= 0:
            return 0.0
        return float(_above_frac(w, order)[index_of(hole)])

    def narrow(self, seat: int, order: np.ndarray, bands,
               board: list[int] | None = None, keep_hole=None) -> None:
        """Keep only the percentile ``bands`` of this seat's range.

        ``bands`` is a list of ``(lo, hi)`` pairs in within-range percentile
        terms -- a value bet keeps ``[(1 - f, 1.0)]``, a polarised bet keeps
        the value slice and the bluff slice together, and a call keeps the
        band between folding and raising.

        High bands (``hi >= 1``) use weight-above rather than the midpoint, so
        a made-hand tie at the top of the range stays there; low bands keep the
        midpoint, so a 70% air pile is not all spent as a bluff. ``keep_hole``
        is the holding that just acted and narrowing cannot delete it.
        """
        w = self.live_weights(seat, board)
        if w.sum() <= 0:
            return
        pct = _within_pct(w, order)
        above = _above_frac(w, order)
        keep = np.zeros(N_COMBOS, dtype=bool)
        for lo, hi in bands:
            if hi >= 1.0:
                keep |= above <= (1.0 - lo) + 1e-12
            elif lo <= 0.0:
                keep |= pct < hi
            else:
                keep |= (above <= (1.0 - lo) + 1e-12) & (pct < hi)
        if keep_hole is not None:
            keep[index_of(keep_hole)] = True
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

    def top_made(self, seat: int, board: list[int], n: int = 8) -> list[tuple[str, float]]:
        """The heaviest *made* hands still in this seat's range.

        Preflop class names on a paired board are how QTo showed up as the
        thing they folded -- it was tens full. The review has to say that.
        """
        from .cards import describe
        w = self.live_weights(seat, board)
        total = float(w.sum())
        if total <= 0:
            return []
        scores = self.board_cache(board).score
        shares: dict[str, float] = defaultdict(float)
        for i in np.nonzero(w > 0)[0]:
            shares[describe(int(scores[i]))] += float(w[i])
        ranked = sorted(shares.items(), key=lambda kv: -kv[1])[:n]
        return [(name, wt / total) for name, wt in ranked]

    def reset(self, seat: int) -> None:
        self.w[seat] = 1.0
