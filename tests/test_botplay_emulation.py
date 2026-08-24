"""The rest of the book: stats the sim counted and then never played."""

from dataclasses import dataclass

import numpy as np

from villain.botplay import decide, think_ms
from villain.holdem import Hand, Seat


@dataclass
class _Est:
    value: float
    opps: float = 500.0


class _Prof:
    def __init__(self, **freqs):
        self.stats = {k: _Est(v) for k, v in freqs.items()}


def _seats(*stacks):
    return [Seat(chr(65 + i), s) for i, s in enumerate(stacks)]


def test_limp_raise_beats_the_generic_three_bet():
    """After a limp, isolation is limp_raise, not a 3-bet of an open."""
    overlay = _Prof(limp_raise=0.90, limp_fold=0.05, three_bet=0.04,
                    bb_defend=0.40, cold_call=0.05)
    pooled = _Prof(three_bet=0.04, bb_defend=0.40, cold_call=0.05)
    rng = np.random.default_rng(0)

    def rate(profile, n=160):
        hits = tot = 0
        for k in range(n):
            h = Hand(_seats(400, 400), button=0, sb=1, bb=2,
                     rng=np.random.default_rng(k + 11))
            if h.to_act != 0:
                continue
            h.act("call")
            if h.to_act != 1:
                continue
            h.act("raise", 8)
            if h.to_act != 0 or 0 not in h.limped:
                continue
            tot += 1
            hits += decide(h, 0, profile, rng)[0] == "raise"
        return hits / tot if tot else 0.0, tot

    hi, n_hi = rate(overlay)
    lo, n_lo = rate(pooled)
    assert n_hi >= 80 and n_lo >= 80
    assert hi > lo + 0.40, f"limp-raise {hi:.2f} vs 3-bet {lo:.2f}"


def test_ip_float_uses_after_call_stab():
    """Called the flop, checked to on the turn: after_call stab, not probe."""
    profile = _Prof(**{
        "after_call:turn:stab:ip": 0.99, "after_call:turn:stab": 0.99,
        "probe:turn": 0.01, "probe:turn:ip": 0.01, "donk:turn": 0.0,
        "cbet:turn": 0.01, "delayed_cbet:turn": 0.01,
    })
    rng = np.random.default_rng(2)
    fires = tot = 0
    for k in range(100):
        h = Hand(_seats(400, 400), button=0, sb=1, bb=2,
                 rng=np.random.default_rng(k + 21))
        h.act("raise", 6)
        h.act("call")
        if h.street != 1:
            continue
        h.act("raise", max(4, int(0.5 * h.pot)))
        h.act("call")
        if h.street != 2 or 0 not in h.called_prev:
            continue
        h.act("check")
        if h.to_act != 0:
            continue
        tot += 1
        kind, _, why = decide(h, 0, profile, rng)
        fires += kind == "raise"
    assert tot >= 40, f"only {tot} IP floats"
    assert fires > tot * 0.70, f"float fired {fires}/{tot}"


def test_after_call_raise_is_preferred():
    """A check-raise number used to cover the seat that called last street."""
    profile = _Prof(**{
        "after_call:turn:raise": 0.80, "raise_vs_bet:turn": 0.02,
        "fold_vs_bet:turn": 0.40, "check_raise:turn": 0.02,
    })
    rng = np.random.default_rng(3)
    raises = tot = 0
    for k in range(120):
        h = Hand(_seats(400, 400), button=0, sb=1, bb=2,
                 rng=np.random.default_rng(k + 31))
        h.act("raise", 6)
        h.act("call")
        if h.street != 1:
            continue
        h.act("raise", max(4, int(0.5 * h.pot)))
        h.act("call")
        if h.street != 2 or 0 not in h.called_prev:
            continue
        h.act("raise", max(8, int(0.5 * h.pot)))
        if h.to_act != 0:
            continue
        tot += 1
        raises += decide(h, 0, profile, rng)[0] == "raise"
    assert tot >= 50
    assert raises / tot > 0.40, f"after_call raise {raises / tot:.2f} over {tot}"


def test_delayed_cbet_uses_its_own_size():
    from villain.cards import card_id
    profile = _Prof(rfi=0.99, three_bet=0.01, bb_defend=0.99,
                    **{"cbet:flop": 0.01, "cbet:turn": 0.01, "delayed_cbet:turn": 0.99,
                       "donk:flop": 0.0, "probe:flop": 0.0, "probe:turn": 0.0})
    profile.means = {
        "delayed_cbet_size:turn": 0.25, "delayed_cbet_size:turn#n": 40.0,
        "cbet_size:turn": 1.50, "cbet_size:turn#n": 40.0,
        "bet_size:turn": 1.50, "bet_size:turn#n": 40.0,
    }
    rng = np.random.default_rng(4)
    sizes = []
    for k in range(60):
        h = Hand(_seats(400, 400), button=0, sb=1, bb=2,
                 rng=np.random.default_rng(k + 41))
        k0, a0, _ = decide(h, 0, profile, rng)
        if k0 != "raise":
            continue
        h.act(k0, a0)
        k1, a1, _ = decide(h, 1, profile, rng)
        if k1 != "call":
            continue
        h.act(k1, a1)
        if h.street != 1:
            continue
        h.act("check")
        k2, a2, _ = decide(h, 0, profile, rng)
        h.act(k2, a2)
        if h.street != 2:
            continue
        if h.to_act == 1:
            k3, a3, _ = decide(h, 1, profile, rng)
            h.act(k3, a3)
        if h.to_act != 0:
            continue
        h.board = [card_id("Kc"), card_id("9d"), card_id("2s"), card_id("7h")]
        h.seats[0].hole = (card_id("As"), card_id("Ad"))
        kind, amt, why = decide(h, 0, profile, rng)
        if kind == "raise" and "delayed" in why:
            sizes.append(amt / max(h.pot, 1))
    assert sizes
    assert max(sizes) < 0.55, sizes


def test_polar_mix_fades_the_edge_when_rng_is_passed():
    from villain.botplay import _polar_bet
    assert _polar_bet(0.60, 0.40, 0.40) == "value"
    rng = np.random.default_rng(6)
    n = 400
    hits = sum(_polar_bet(0.60, 0.40, 0.40, rng) == "value" for _ in range(n))
    assert 80 < hits < 320, f"edge mixed {hits}/{n}, not a hard cut"
    rng2 = np.random.default_rng(7)
    assert all(_polar_bet(0.02, 0.30, 0.55, rng2) is None for _ in range(80))


def test_effective_stack_is_the_aggressor_not_the_whale():
    """A 20bb jam with a 200bb fish still in is not a 100bb SPR."""
    from villain.botplay import _effective
    h = Hand(_seats(200, 40, 400), button=0, sb=1, bb=2,
             rng=np.random.default_rng(0))
    h.last_raiser = 1
    assert _effective(h, 0) == min(h.seats[0].stack, h.seats[1].stack)
    h.last_raiser = None
    assert _effective(h, 0) == min(s.stack for i, s in enumerate(h.seats) if i)
    h.last_raiser = 2
    assert _effective(h, 0) == min(h.seats[0].stack, h.seats[2].stack)


def test_short_spr_cuts_air_not_value():
    from villain.botplay import _polar_bet, _polar_split
    vf, bf = _polar_split(0.70, 0.45, spr=2)
    vf2, bf2 = _polar_split(0.70, 0.45, spr=16)
    assert vf == vf2
    assert bf < bf2
    assert _polar_bet(0.20, 0.70, 0.45, spr=16) == "bluff"
    assert _polar_bet(0.20, 0.70, 0.45, spr=2) is None
    assert _polar_bet(0.90, 0.70, 0.45, spr=2) == "value"


def test_think_ms_uses_the_fold_meter():
    profile = _Prof()
    profile.means = {
        "think:fold": 6000.0, "think:fold#n": 80.0,
        "think:all": 1800.0, "think:all#n": 200.0,
        "think:aggro": 1200.0, "think:aggro#n": 80.0,
    }
    rng = np.random.default_rng(8)
    samples = [think_ms(profile, "fold", 1, "folds — outside", rng)
               for _ in range(40)]
    assert all(400 <= m <= 8000 for m in samples)
    assert sum(samples) / len(samples) > 4000


def test_step_returns_think_ms():
    from villain.sim import Game
    g = Game(["You", "Arav", "nuj"],
             [None, _Prof(rfi=0.99), _Prof(rfi=0.99)],
             0, 200, 1, 2, seed=1)
    g.new_hand()
    ev = g.step()
    assert ev is not None
    assert 400 <= ev["think_ms"] <= 8000
    assert ev["seat"] != 0
