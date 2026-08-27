"""All-in equity, used to separate results from decisions.

A player's chip graph is mostly noise over a few hundred hands: getting it in
as an 80% favorite and losing costs the same money as a punt, and a skill
rating that cannot tell those apart is a rating of luck. So every hand that
gets all-in with cards known is scored twice -- what happened, and what the
pot was worth at the moment the last chip went in.

Runouts are enumerated exactly when the remaining board is small enough
(anything from the flop on) and sampled otherwise, which keeps a full session
well under a second.
"""

from __future__ import annotations

import numpy as np

from .cards import card_ids, evaluate

DECK = np.arange(52, dtype=np.int64)
MAX_EXACT = 200_000


def equities(hole_cards: list[list[str]], board: list[str], samples: int = 20_000,
             rng: np.random.Generator | None = None) -> list[float]:
    """Fractional pot share for each player, ties split.

    ``hole_cards`` is one two-card list per player; ``board`` is 0-5 cards."""
    if len(hole_cards) < 2:
        return [1.0] * len(hole_cards)
    holes = np.array([card_ids(h) for h in hole_cards], dtype=np.int64)   # (P, 2)
    known_board = card_ids(board).astype(np.int64) if board else np.empty(0, dtype=np.int64)
    dead = np.concatenate([holes.ravel(), known_board])
    if len(set(dead.tolist())) != len(dead):
        raise ValueError("duplicate card between hole cards and board")

    need = 5 - len(known_board)
    deck = np.setdiff1d(DECK, dead)
    runouts = _runouts(deck, need, samples, rng)          # (R, need)
    r = runouts.shape[0]

    boards = np.concatenate(
        [np.repeat(known_board[None, :], r, axis=0), runouts], axis=1
    ) if len(known_board) else runouts                                   # (R, 5)

    scores = np.empty((len(hole_cards), r), dtype=np.int64)
    for p in range(len(hole_cards)):
        seven = np.concatenate([np.repeat(holes[p][None, :], r, axis=0), boards], axis=1)
        scores[p] = evaluate(seven)

    best = scores.max(axis=0)
    winners = scores == best
    share = winners / winners.sum(axis=0)
    return [float(share[p].mean()) for p in range(len(hole_cards))]


def _runouts(deck: np.ndarray, need: int, samples: int,
             rng: np.random.Generator | None) -> np.ndarray:
    if need == 0:
        return np.empty((1, 0), dtype=np.int64)
    from math import comb
    total = comb(len(deck), need)
    if total <= MAX_EXACT:
        from itertools import combinations
        return np.array(list(combinations(deck.tolist(), need)), dtype=np.int64)
    rng = rng or np.random.default_rng(0)
    # Sample without replacement per row by drawing a random key per card and
    # taking the lowest `need` of them.
    #
    # argpartition, not argsort: the order of the chosen cards does not matter,
    # only which ones they are, and a full sort of every row was the single
    # most expensive line in an import -- 13.5s of a 71,000-hand rebuild, to
    # order 45 keys per row and then throw all but the first two away.
    keys = rng.random((samples, len(deck)))
    picks = np.argpartition(keys, need - 1, axis=1)[:, :need]
    return deck[picks]
