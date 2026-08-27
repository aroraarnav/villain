"""Realized frequency ≈ measured frequency, the design premise of botplay.

Every statistic is hits/opportunities conditioned on reaching the node. The
policy applies that rate as a cut inside the range that got there. If the two
diverge, the sim is not playing the profile — the 4-bet/5-bet ranking bug
sat here for years with nothing to catch it. This file is that guard.
"""


import numpy as np
from helpers import Prof as _Prof
from helpers import seats as _seats

from villain.botplay import decide
from villain.holdem import Hand


def _play_preflop(opener, defender, n, seed=0):
    """Deal HU hands; yield (level, opener_kind) at each opener decision after
    the first, and the first-in kind. ``level`` is ``hand.raises`` before they
    act."""
    rng = np.random.default_rng(seed)
    for i in range(n):
        h = Hand(_seats(400, 400), button=0, sb=1, bb=2,
                 rng=np.random.default_rng(i + seed * 10_003))
        k, a, _ = decide(h, 0, opener, rng)
        yield "rfi", k
        if k not in ("raise", "call"):
            continue
        h.act(k, a)
        if h.over or h.to_act is None:
            continue
        k2, a2, _ = decide(h, 1, defender, rng)
        if k2 != "raise":
            continue
        h.act(k2, a2)
        if h.over or h.to_act != 0:
            continue
        k3, a3, _ = decide(h, 0, opener, rng)
        yield "four_bet", k3
        if k3 != "raise":
            continue
        h.act(k3, a3)
        if h.over or h.to_act != 1:
            continue
        k4, _, _ = decide(h, 1, defender, rng)
        yield "five_bet", k4


def _rate(events, spot, hit):
    n = hits = 0
    for name, kind in events:
        if name != spot:
            continue
        n += 1
        hits += kind == hit
    return (hits / n if n else None), n


def test_rfi_realized_matches_measured():
    opener = _Prof(rfi=0.30)
    defender = _Prof(three_bet=0.01, bb_defend=0.40)
    events = list(_play_preflop(opener, defender, 1200, seed=1))
    rate, n = _rate(events, "rfi", "raise")
    assert n >= 800
    assert abs(rate - 0.30) < 0.05, f"rfi realized {rate:.3f} over {n} vs 0.30"


def test_four_bet_realized_matches_measured():
    """The bug this harness exists for: 16.7% realized against a measured 4%."""
    opener = _Prof(rfi=0.55, four_bet=0.04, fold_to_three_bet=0.70)
    defender = _Prof(three_bet=0.99, bb_defend=0.99, five_bet=0.02)
    events = list(_play_preflop(opener, defender, 2000, seed=2))
    rate, n = _rate(events, "four_bet", "raise")
    assert n >= 400, f"only {n} 4-bet spots"
    assert abs(rate - 0.04) < 0.04, f"four_bet realized {rate:.3f} over {n} vs 0.04"


def test_five_bet_realized_matches_measured():
    """The sibling: 27% jammed against a measured 2%."""
    opener = _Prof(rfi=0.70, four_bet=0.99, fold_to_three_bet=0.01)
    defender = _Prof(three_bet=0.99, bb_defend=0.99, five_bet=0.02,
                     fold_to_four_bet=0.80)
    events = list(_play_preflop(opener, defender, 1600, seed=3))
    rate, n = _rate(events, "five_bet", "raise")
    assert n >= 200, f"only {n} 5-bet spots"
    assert abs(rate - 0.02) < 0.04, f"five_bet realized {rate:.3f} over {n} vs 0.02"


def test_cbet_realized_matches_measured():
    """A polarized 70% c-bet still bets 70% of the hands that reach the flop."""
    opener = _Prof(rfi=0.99, **{"cbet:flop": 0.70, "probe:flop": 0.0, "donk:flop": 0.0})
    defender = _Prof(three_bet=0.01, bb_defend=0.99, **{"donk:flop": 0.0, "probe:flop": 0.0})
    rng = np.random.default_rng(4)
    n = hits = 0
    for i in range(500):
        h = Hand(_seats(400, 400), button=0, sb=1, bb=2,
                 rng=np.random.default_rng(i + 40))
        k, a, _ = decide(h, 0, opener, rng)
        if k != "raise":
            continue
        h.act(k, a)
        k2, a2, _ = decide(h, 1, defender, rng)
        if k2 != "call":
            continue
        h.act(k2, a2)
        if h.street != 1 or h.to_act is None:
            continue
        # BB first postflop; they should check (no donk). Then the opener c-bets.
        if h.to_act == 1:
            k3, a3, _ = decide(h, 1, defender, rng)
            h.act(k3, a3)
        if h.over or h.to_act != 0:
            continue
        n += 1
        hits += decide(h, 0, opener, rng)[0] == "raise"
    assert n >= 200, f"only {n} flop c-bet spots"
    assert abs(hits / n - 0.70) < 0.08, f"cbet realized {hits / n:.3f} over {n} vs 0.70"


#: The bet size this calibration runs at. A pooled fold frequency is an
#: average across the sizes that player actually faced, so "realized ==
#: measured" only holds at that pivot -- at any other size the rate is
#: deliberately shifted by the change in breakeven, which is what
#: ``test_a_bigger_bet_is_continued_against_tighter`` covers. Pinning
#: ``faced_size`` to the size we bet makes the pivot explicit instead of
#: leaving it to whatever the pool default happens to be.
PIVOT_FRAC = 0.30


def test_fold_vs_bet_realized_matches_measured():
    """Small bets so pot-odds do not add a second gate on top of the cut."""
    caller = _Prof(rfi=0.01, bb_defend=0.99, three_bet=0.01,
                   **{"fold_vs_bet:flop": 0.55, "raise_vs_bet:flop": 0.02,
                      "donk:flop": 0.0, "check_raise:flop": 0.02})
    caller.means = {"faced_size:flop": PIVOT_FRAC, "faced_size:flop#n": 200.0}
    bettor = _Prof(rfi=0.99, three_bet=0.01, **{"cbet:flop": 0.99, "donk:flop": 0.0})
    rng = np.random.default_rng(5)
    n = folds = 0
    for i in range(500):
        h = Hand(_seats(400, 400), button=0, sb=1, bb=2,
                 rng=np.random.default_rng(i + 90))
        k, a, _ = decide(h, 0, bettor, rng)
        if k != "raise":
            continue
        h.act(k, a)
        k2, a2, _ = decide(h, 1, caller, rng)
        if k2 != "call":
            continue
        h.act(k2, a2)
        if h.street != 1:
            continue
        h.act("check")                                  # BB checks
        k3, a3, _ = decide(h, 0, bettor, rng)
        if k3 != "raise":
            continue
        # A third-pot bet keeps req_eq low so the frequency cut is the gate,
        # and it is the size this caller's `faced_size` pivots on.
        size = max(h.legal().min_raise_to, int(PIVOT_FRAC * h.pot))
        h.act("raise", size)
        if h.to_act != 1:
            continue
        n += 1
        folds += decide(h, 1, caller, rng)[0] == "fold"
    assert n >= 150, f"only {n} fold-vs-bet spots"
    assert abs(folds / n - 0.55) < 0.10, f"fold_vs_bet realized {folds / n:.3f} over {n} vs 0.55"


def test_three_bet_defense_follows_the_measured_fold():
    """The 0.16 continue cap used to make a 30% folder defend like a nit."""
    opener = _Prof(rfi=0.99, four_bet=0.02, fold_to_three_bet=0.30)
    defender = _Prof(three_bet=0.99, bb_defend=0.99, five_bet=0.01)
    events = list(_play_preflop(opener, defender, 1200, seed=6))
    n = folds = 0
    for name, kind in events:
        if name != "four_bet":
            continue
        n += 1
        folds += kind == "fold"
    assert n >= 400, f"only {n} 3-bet defense spots"
    assert abs(folds / n - 0.30) < 0.10, f"fold_to_3bet realized {folds / n:.3f} over {n} vs 0.30"


def test_a_calling_station_3bet_defense_is_not_capped():
    """fold_to_three_bet 10% used to continue only 70% because of a clamp."""
    opener = _Prof(rfi=0.99, four_bet=0.02, fold_to_three_bet=0.10)
    defender = _Prof(three_bet=0.99, bb_defend=0.99, five_bet=0.01)
    events = list(_play_preflop(opener, defender, 1000, seed=7))
    n = folds = 0
    for name, kind in events:
        if name != "four_bet":
            continue
        n += 1
        folds += kind == "fold"
    assert n >= 300, f"only {n} 3-bet defense spots"
    assert folds / n < 0.22, f"fold_to_3bet realized {folds / n:.3f} over {n} vs 0.10"


# -- a statistic belongs to the seat it was counted on ------------------------

def _cold_caller_facing_a_three_bet(seed):
    """Seat 1 cold-calls seat 0's open, seat 2 3-bets, action back on seat 1.

    Three-handed the button acts first preflop, so the order is 0 (BTN),
    1 (SB), 2 (BB) and the 3-bet lands while seat 1 still owes action -- which
    is the whole point: seat 1 never raised anything."""
    h = Hand(_seats(400, 400, 400), button=0, sb=1, bb=2,
             rng=np.random.default_rng(seed))
    h.act("raise", 6)                       # seat 0 opens
    if h.to_act != 1:
        return None
    h.act("call")                           # seat 1 cold-calls, never raised
    if h.to_act != 2:
        return None
    h.act("raise", 24)                      # seat 2 3-bets
    if h.over or h.to_act != 0:             # the opener answers first
        return None
    h.act("fold")
    return h if (not h.over and h.to_act == 1) else None


def test_a_cold_caller_does_not_inherit_the_openers_fold_to_three_bet():
    """`fold_to_three_bet` is counted only on the seat that opened.

    features.py gates it on ``d.seat == opener``. Lending it to a player who
    merely called the open turns a 30%-folding opener into a blind that
    cold-calls a 3-bet with a third of its range -- a spot the statistic never
    described and nobody plays that way."""
    station = _Prof(rfi=0.4, three_bet=0.02, cold_call=0.35,
                    fold_to_three_bet=0.10)        # continues 90% *as the opener*
    rng = np.random.default_rng(11)
    n = calls = 0
    for seed in range(400):
        h = _cold_caller_facing_a_three_bet(seed)
        if h is None:
            continue
        assert h.opener == 0 and h.three_bettor == 2
        n += 1
        calls += decide(h, 1, station, rng)[0] in ("call", "raise")
    assert n >= 100, f"only {n} cold-call-vs-3-bet spots"
    assert calls / n < 0.20, (
        f"a cold caller continued {calls / n:.0%} facing a 3-bet on the "
        "opener's fold_to_three_bet")


def test_the_opener_still_follows_its_own_fold_to_three_bet():
    """The other half: the seat the number belongs to still gets it."""
    station = _Prof(rfi=0.99, three_bet=0.02, four_bet=0.02,
                    fold_to_three_bet=0.10)
    defender = _Prof(three_bet=0.99, bb_defend=0.99, five_bet=0.01)
    events = list(_play_preflop(station, defender, 900, seed=12))
    n = folds = 0
    for name, kind in events:
        if name != "four_bet":
            continue
        n += 1
        folds += kind == "fold"
    assert n >= 200, f"only {n} spots"
    assert folds / n < 0.25, f"the opener folded {folds / n:.0%} against a measured 10%"
