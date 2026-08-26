"""Cards and a vectorised 7-card evaluator.

``evaluate`` maps an ``(N, 7)`` array of card ids to ``(N,)`` scores where
higher is better and equal scores are genuine chops. Scores pack as
``category << 20`` plus five 4-bit ranks, best kicker first.

The 7-card traps this handles explicitly, both caused by ordering ranks by
(count, rank) and then reading kickers off that order -- correct for 5 cards,
wrong for 7:

* quads with a pair (7777 22 A): the kicker is the ace, not the deuce;
* three pair (99 55 33 A): the fifth card is the ace, not the three.

Both are fixed by taking kickers from a mask with the counted ranks cleared.
"""

from __future__ import annotations

import numpy as np

RANKS = "23456789TJQKA"
SUITS = "cdhs"
RANK_INDEX = {r: i for i, r in enumerate(RANKS)}
SUIT_INDEX = {s: i for i, s in enumerate(SUITS)}

(HIGH_CARD, PAIR, TWO_PAIR, TRIPS, STRAIGHT, FLUSH, FULL_HOUSE, QUADS, STRAIGHT_FLUSH) = range(9)
CATEGORY_NAMES = ["high card", "pair", "two pair", "trips", "straight",
                  "flush", "full house", "quads", "straight flush"]

# Every five-card straight as a rank bitmask, strongest first. The wheel
# (A2345) is last and scores as a five-high straight.
_STRAIGHTS = [(sum(1 << (top - i) for i in range(5)), top) for top in range(12, 3, -1)]
_STRAIGHTS.append((1 << 12 | 0b1111, 3))   # A5432, ranked by the five


def card_id(text: str) -> int:
    """``"As"`` -> 51. Rank first, suit second, either case."""
    text = text.strip()
    if len(text) != 2:
        raise ValueError(f"bad card {text!r}")
    return RANK_INDEX[text[0].upper()] * 4 + SUIT_INDEX[text[1].lower()]


def card_ids(cards) -> np.ndarray:
    return np.array([card_id(c) for c in cards], dtype=np.int8)


def card_text(cid: int) -> str:
    return RANKS[cid // 4] + SUITS[cid % 4]


def _rank_masks(ranks: np.ndarray) -> np.ndarray:
    """(N, 7) ranks -> (N, 13) counts. A one-hot over the 13 ranks summed
    across the cards -- vectorised, where ``np.add.at`` was an unbuffered
    scatter and, after the evaluator's sort was fixed, the slowest call here."""
    return (ranks[:, :, None] == np.arange(13)).sum(axis=1).astype(np.int8)


def _best_straight(masks: np.ndarray) -> np.ndarray:
    """Highest straight top-rank per row, or -1."""
    out = np.full(masks.shape[0], -1, dtype=np.int8)
    for pattern, top in _STRAIGHTS:
        hit = (masks & pattern) == pattern
        out = np.where((out < 0) & hit, top, out)
    return out


def _pack(category: np.ndarray, kickers: np.ndarray) -> np.ndarray:
    score = category.astype(np.int64) << 20
    for i in range(kickers.shape[1]):
        score |= kickers[:, i].astype(np.int64) << (16 - 4 * i)
    return score


def _build_top_table() -> np.ndarray:
    """For every 13-bit rank mask, its five highest set bits as rank indices,
    descending and zero-padded. 8192 x 5 int8 (40 KB), built once at import."""
    tbl = np.zeros((1 << 13, 5), dtype=np.int8)
    for m in range(1 << 13):
        top = [r for r in range(12, -1, -1) if (m >> r) & 1][:5]
        tbl[m, :len(top)] = top
    return tbl


_TOP_TABLE = _build_top_table()


def _top_bits(mask: np.ndarray, count: int) -> np.ndarray:
    """The ``count`` highest set bits of each mask, as rank indices.

    A rank mask is 13 bits, so every answer is precomputed in
    :data:`_TOP_TABLE` and this is a single gather -- no sort or per-row work,
    where an argsort or a cumsum scatter was the biggest cost in a hero build.
    Masked to 13 bits because callers pass complements, which set high bits.
    Unset slots and a deuce both read 0, which callers rely on.
    """
    return _TOP_TABLE[np.asarray(mask, dtype=np.int64) & 0x1FFF][:, :count]


def evaluate(hands: np.ndarray) -> np.ndarray:
    """Score ``(N, K)`` card ids (K >= 5, typically 7)."""
    hands = np.asarray(hands, dtype=np.int64)
    if hands.ndim == 1:
        hands = hands[None, :]
    n = hands.shape[0]
    ranks = hands // 4
    suits = hands % 4

    counts = _rank_masks(ranks)
    rank_mask = ((counts > 0).astype(np.int32) * (1 << np.arange(13))).sum(axis=1)

    suit_counts = (suits[:, :, None] == np.arange(4)).sum(axis=1).astype(np.int8)
    flush_suit = np.argmax(suit_counts, axis=1)
    has_flush = suit_counts.max(axis=1) >= 5

    # Flush ranks, as a mask over the flushing suit only.
    flush_mask = np.zeros(n, dtype=np.int32)
    in_flush = suits == flush_suit[:, None]
    for col in range(hands.shape[1]):
        contrib = np.where(in_flush[:, col], 1 << ranks[:, col], 0)
        flush_mask |= contrib.astype(np.int32)

    straight_top = _best_straight(rank_mask)
    sf_top = _best_straight(flush_mask)

    quad_mask = ((counts == 4).astype(np.int32) * (1 << np.arange(13))).sum(axis=1)
    trip_mask = ((counts == 3).astype(np.int32) * (1 << np.arange(13))).sum(axis=1)
    pair_mask = ((counts == 2).astype(np.int32) * (1 << np.arange(13))).sum(axis=1)

    has_quads = quad_mask > 0
    trips_count = (counts == 3).sum(axis=1)
    pair_count = (counts == 2).sum(axis=1)
    has_boat = (trips_count >= 1) & ((trips_count >= 2) | (pair_count >= 1))

    category = np.full(n, HIGH_CARD, dtype=np.int8)
    kickers = np.zeros((n, 5), dtype=np.int8)

    def assign(where, cat, ranks_for):
        nonlocal category, kickers
        if not np.any(where):
            return
        category = np.where(where, cat, category)
        kickers[where] = ranks_for[where]

    # Weakest first; each stronger category overwrites.
    high = _top_bits(rank_mask, 5)
    assign(np.ones(n, bool), HIGH_CARD, high)

    one_pair = (pair_count == 1) & (trips_count == 0) & ~has_quads
    top_pair = _top_bits(pair_mask, 1)
    pair_kick = _top_bits(rank_mask & ~pair_mask, 3)
    assign(one_pair, PAIR, np.concatenate([top_pair, pair_kick, np.zeros((n, 1), np.int8)], axis=1))

    two_pair = (pair_count >= 2) & (trips_count == 0) & ~has_quads
    top_two = _top_bits(pair_mask, 2)
    # Third pair is not a kicker: pull the kicker from every rank not in the top two.
    used = np.zeros(n, dtype=np.int32)
    for i in range(2):
        used |= np.left_shift(np.int32(1), top_two[:, i].astype(np.int32))
    tp_kick = _top_bits(rank_mask & ~used, 1)
    assign(two_pair, TWO_PAIR,
           np.concatenate([top_two, tp_kick, np.zeros((n, 2), np.int8)], axis=1))

    set_only = (trips_count == 1) & (pair_count == 0) & ~has_quads
    top_trip = _top_bits(trip_mask, 1)
    trip_kick = _top_bits(rank_mask & ~trip_mask, 2)
    assign(set_only, TRIPS,
           np.concatenate([top_trip, trip_kick, np.zeros((n, 2), np.int8)], axis=1))

    is_straight = straight_top >= 0
    assign(is_straight & ~has_flush & ~has_boat & ~has_quads, STRAIGHT,
           np.concatenate([straight_top[:, None].astype(np.int8), np.zeros((n, 4), np.int8)], axis=1))

    assign(has_flush & ~has_boat & ~has_quads, FLUSH, _top_bits(flush_mask, 5))

    boat_trip = _top_bits(trip_mask, 1)
    # The pair half of a boat can be a second set played as a pair.
    boat_pair_mask = pair_mask | (
        trip_mask & ~np.left_shift(np.int32(1), boat_trip[:, 0].astype(np.int32)))
    boat_pair = _top_bits(boat_pair_mask, 1)
    assign(has_boat & ~has_quads, FULL_HOUSE,
           np.concatenate([boat_trip, boat_pair, np.zeros((n, 3), np.int8)], axis=1))

    quad_rank = _top_bits(quad_mask, 1)
    quad_kick = _top_bits(rank_mask & ~quad_mask, 1)
    assign(has_quads, QUADS,
           np.concatenate([quad_rank, quad_kick, np.zeros((n, 3), np.int8)], axis=1))

    assign(sf_top >= 0, STRAIGHT_FLUSH,
           np.concatenate([sf_top[:, None].astype(np.int8), np.zeros((n, 4), np.int8)], axis=1))

    return _pack(category, kickers)


def category_of(score: int) -> int:
    return int(score) >> 20


def describe(score: int) -> str:
    return CATEGORY_NAMES[category_of(score)]


def evaluate_cards(cards) -> int:
    """Convenience wrapper for a single hand given as ``["As", "Kd", ...]``."""
    return int(evaluate(card_ids(cards).astype(np.int64)[None, :])[0])
