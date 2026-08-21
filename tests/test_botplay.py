"""The villain policy: measured frequencies actually shape how a bot plays."""

from dataclasses import dataclass

import numpy as np
import pytest

from villain.botplay import decide, preflop_strength
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


def _preflop_play_rate(profile, n=400, seed=0):
    """How often the bot voluntarily puts chips in from the button, unopened."""
    rng = np.random.default_rng(seed)
    plays = 0
    for _ in range(n):
        h = Hand(_seats(200, 200), button=0, sb=1, bb=2, rng=rng)
        kind = decide(h, 0, profile, rng)[0]     # button acts first preflop, unopened
        plays += kind in ("call", "raise")
    return plays / n


def test_loose_plays_far_more_hands_than_tight():
    tight = _Prof(rfi=0.12)
    loose = _Prof(rfi=0.60)
    assert _preflop_play_rate(loose) > _preflop_play_rate(tight) + 0.25


class _Sized:
    """A profile with a measured open size (means) as well as frequencies."""
    def __init__(self, rfi, open_bb):
        self.stats = {"rfi": _Est(rfi)}
        self.means = {"open_bb": open_bb, "open_bb#n": 100.0}


def test_open_size_follows_the_players_own_sizing():
    def first_open(open_bb, seed=0):
        rng = np.random.default_rng(seed)
        prof = _Sized(0.95, open_bb)          # opens almost everything
        for _ in range(60):
            h = Hand(_seats(400, 400), button=0, sb=1, bb=2, rng=rng)
            kind, amt, _ = decide(h, 0, prof, rng)
            if kind == "raise":
                return amt
        return None
    assert first_open(4.5) > first_open(2.2)   # the big opener opens bigger


def test_a_station_folds_less_to_bets_than_a_nit():
    # Facing a pot bet on the flop with a middling hand, the high-fold profile
    # should fold and the low-fold profile should not, more often than not.
    rng = np.random.default_rng(3)
    nit = _Prof(**{"fold_vs_bet:flop": 0.75})
    station = _Prof(**{"fold_vs_bet:flop": 0.15})
    nit_folds = station_folds = 0
    for k in range(200):
        h = Hand(_seats(200, 200), button=0, sb=1, bb=2, rng=np.random.default_rng(k))
        # get to the flop cheaply, then have the button face a bet
        h.act("call")
        h.act("check")                           # to the flop
        h.act("raise", h.legal().min_raise_to + int(0.9 * h.pot))  # BB-first bets ~pot
        nit_folds += decide(h, h.to_act, nit, rng)[0] == "fold"
        station_folds += decide(h, h.to_act, station, rng)[0] == "fold"
    assert nit_folds > station_folds


def test_full_bot_hand_stays_legal_and_conserves_chips():
    rng = np.random.default_rng(9)
    profs = [_Prof(vpip=0.4, pfr=0.25, **{"cbet:flop": 0.6, "fold_vs_bet:flop": 0.4})
             for _ in range(3)]
    h = Hand(_seats(200, 200, 200), button=0, sb=1, bb=2, rng=rng)
    total = sum(s.stack for s in h.seats) + h.pot
    guard = 0
    while not h.over:
        kind, amt, _ = decide(h, h.to_act, profs[h.to_act], rng)
        h.act(kind, amt)
        guard += 1
        assert guard < 300
    assert sum(s.stack for s in h.seats) == total


def test_preflop_strength_orders_hands():
    from villain.cards import card_id
    def hole(a, b):
        return (int(card_id(a)), int(card_id(b)))
    assert preflop_strength(hole("Ac", "Ad")) > preflop_strength(hole("Kc", "Kd"))
    assert preflop_strength(hole("Ac", "Kc")) > preflop_strength(hole("Ac", "Kd"))  # suited > offsuit
    assert preflop_strength(hole("Ac", "Ad")) > preflop_strength(hole("7c", "2d"))


def test_small_pairs_outscore_offsuit_broadway_for_defense():
    """Chen ranked 33 with KTo. Blind defense is the other way around."""
    from villain.cards import card_id
    def hole(a, b):
        return (int(card_id(a)), int(card_id(b)))
    pair = preflop_strength(hole("3c", "3d"), "defend")
    kto = preflop_strength(hole("Kc", "Td"), "defend")
    junk = preflop_strength(hole("7c", "2d"), "defend")
    assert pair > kto
    assert pair > 0.85
    assert junk < 0.20


def test_opening_is_position_dependent_in_frequency_and_size():
    # A profile that opens tight/small UTG and wide/big on the button. The bot
    # must reproduce both the frequency and the size, per position.
    class _Pos:
        stats = {"rfi:UTG": _Est(0.10), "rfi:BTN": _Est(0.60), "rfi": _Est(0.3)}
        means = {"open_bb:UTG": 2.2, "open_bb:UTG#n": 100.0,
                 "open_bb:BTN": 3.5, "open_bb:BTN#n": 100.0, "open_bb": 2.8, "open_bb#n": 100.0}
    from villain.botplay import _position
    def open_at(pos, n=1200):
        rng = np.random.default_rng(1)
        opens = tot = 0
        sizes = []
        for _ in range(n):
            h = Hand([Seat(str(i), 1000) for i in range(6)], button=0, sb=1, bb=2, rng=rng)
            g = 0
            while not h.over and h.raises == 0 and _position(h, h.to_act) != pos and g < 6:
                h.act("fold")
                g += 1
            if h.over or h.raises > 0 or _position(h, h.to_act) != pos:
                continue
            tot += 1
            k, amt, _ = decide(h, h.to_act, _Pos(), rng)
            if k == "raise":
                opens += 1
                sizes.append(amt)
        return (opens / tot if tot else 0), (np.mean(sizes) if sizes else 0)
    uf, us = open_at("UTG")
    bf, bs = open_at("BTN")
    assert bf > uf + 0.25          # opens far wider from the button
    assert bs > us                 # ...and to a bigger size


# -- isolation, squeezes, and the position split ------------------------------

def _limped_to(button_stack=200, limpers=1, seed=1):
    """A 3-handed pot where ``limpers`` players have limped to the button.

    ``seed`` varies the deal: a fixed one hands the button the same holding
    every trial, so a rate measured over it is one hand's decision repeated
    rather than a frequency.
    """
    h = Hand(_seats(200, 200, button_stack), button=2, sb=1, bb=2,
             rng=np.random.default_rng(seed))
    for _ in range(limpers):
        h.act("call")
    return h


def test_limpers_are_counted_and_reset_by_street():
    h = _limped_to(limpers=2)
    assert h.limpers == 2 and h.callers == 0


def test_a_cold_call_after_a_raise_is_a_caller_not_a_limper():
    h = Hand(_seats(200, 200, 200), button=2, sb=1, bb=2, rng=np.random.default_rng(1))
    h.act("raise", 6)          # SB opens
    h.act("call")              # BB cold-calls
    assert h.limpers == 0 and h.callers == 1


def test_isolates_limpers_wider_than_it_opens_a_folded_pot():
    """The gap the counters exist for: attacking limps is not opening first-in."""
    profile = _Prof(**{"rfi:BTN": 0.40, "iso:BTN": 0.75})
    rng = np.random.default_rng(3)
    isos = sum(decide(_limped_to(seed=k), 2, profile, rng)[0] == "raise"
               for k in range(300))
    tight_iso = _Prof(**{"rfi:BTN": 0.40, "iso:BTN": 0.10})
    rng = np.random.default_rng(3)
    few = sum(decide(_limped_to(seed=k), 2, tight_iso, rng)[0] == "raise"
              for k in range(300))
    assert isos > few + 100, f"iso 75% raised {isos}/300, iso 10% raised {few}/300"


def test_the_iso_raise_grows_with_the_number_of_limpers():
    profile = _Prof(**{"iso:BTN": 0.99})
    rng = np.random.default_rng(5)
    one = decide(_limped_to(limpers=1), 2, profile, rng)
    two = decide(_limped_to(limpers=2), 2, profile, rng)
    assert one[0] == two[0] == "raise"
    assert two[1] > one[1]


def test_a_thin_iso_sample_falls_back_instead_of_reading_one_hand():
    """One iso out of two must not become a 50% isolation frequency."""
    thin = _Prof(**{"rfi:BTN": 0.40})
    thin.stats["iso:BTN"] = _Est(0.5, opps=2)
    rng = np.random.default_rng(7)
    reasons = {decide(_limped_to(), 2, thin, rng)[2] for _ in range(40)}
    assert not any("~50%" in r for r in reasons)


# -- raised pots: MDF-grounded, and the regressions that must hold ------------

# Card ids are rank * 4 + suit, ranks 0..12 = 2..A.
_7c, _7d, _7h, _Kd, _Ac, _Ad = 20, 21, 22, 47, 48, 49
_FLOP = [0, 5, 22]                        # 2c 3d 7h -- one pair for a seven


def _raised_flop(hole, bet=20, raise_to=60, level=2):
    """A flop that has been bet and raised, with the action back on seat 0.

    Postflop the engine carries no separate "bet" verb -- the first aggressive
    action into an unbet pot is a raise from zero, which is what the policy
    itself emits.
    """
    h = Hand(_seats(400, 400), button=0, sb=1, bb=2, rng=np.random.default_rng(2))
    h.act("call")                          # limped preflop
    h.act("check")
    h.street = 1
    h.board = list(_FLOP)
    h.bet = 0
    h.act("raise", bet)                    # seat 0 bets
    h.act("raise", raise_to)               # seat 1 raises
    if hole is not None:
        h.seats[0].hole = tuple(hole)
    h.raises = level
    return h


def test_one_pair_still_folds_a_reraised_pot():
    """Top pair, no kicker help, four bets deep: the guard the handoff names."""
    h = _raised_flop([_7d, _Kd], level=3)
    kind, _, why = decide(h, 0, _Prof(**{"raise_vs_bet:flop": 0.06}),
                          np.random.default_rng(0))
    assert kind == "fold"
    assert "re-raised pot" in why


def test_the_nuts_still_get_it_in_at_reraise_depth():
    h = _raised_flop([_Ac, _Ad], level=3)
    # A set of sevens on 2c 3d 7h. It has to be 7c7d: the board already holds
    # the 7h, and this line used to say (7d, 7h) -- an impossible holding that
    # the old strength measure scored 0.999 anyway, because it masked the
    # duplicate out twice and evaluated a seven-card hand with two 7h in it.
    h.seats[0].hole = (_7c, _7d)
    kind, _, _ = decide(h, 0, _Prof(**{"raise_vs_bet:flop": 0.06}),
                        np.random.default_rng(0))
    assert kind == "raise"


def test_a_pot_sized_raise_reproduces_the_cutoffs_it_replaced():
    """The calibration claim in the comment, asserted rather than trusted."""
    from villain.botplay import JAM_SHARE, RAISE_VALUE_WEIGHT, RERAISE_SHARE, RERAISE_VALUE_WEIGHT
    mdf = 0.5                              # a pot-sized raise
    raised = mdf * RAISE_VALUE_WEIGHT
    reraised = mdf * RERAISE_VALUE_WEIGHT
    assert round(1 - raised, 3) == 0.80
    assert round(1 - raised * RERAISE_SHARE, 3) == 0.956
    assert round(1 - reraised, 3) == 0.96
    assert round(1 - reraised * JAM_SHARE, 3) == 0.985


def _defended(mdf, raise_f):
    from villain.botplay import POOL_RAISE_VS_BET, RAISE_VALUE_WEIGHT, _clamp
    polarity = _clamp(raise_f / POOL_RAISE_VS_BET, 0.5, 2.0)
    return _clamp(mdf * RAISE_VALUE_WEIGHT * polarity, 0.08, 0.45)


def test_a_bigger_raise_is_defended_tighter_than_a_small_one():
    """What a flat cutoff could never do: the bar moves with the size faced."""
    half_pot = 1 / (1 + 0.5)
    double_pot = 1 / (1 + 2.0)
    assert _defended(half_pot, 0.06) > _defended(double_pot, 0.06)


def test_a_player_who_raises_more_is_defended_wider():
    assert _defended(0.5, 0.12) > _defended(0.5, 0.03)


# -- the price has to bind, on every street ----------------------------------

def _facing(street_idx, bet_frac, profile, seed=3, trials=140):
    """Lowest hand strength the bot continues with, facing bet_frac x pot."""
    from villain.botplay import hand_strength
    rng = np.random.default_rng(seed)
    kept = []
    for _ in range(trials):
        h = Hand(_seats(4000, 4000), button=0, sb=1, bb=2,
                 rng=np.random.default_rng(int(rng.integers(1e9))))
        h.act("call")
        h.act("check")
        h.street = street_idx
        size = 3 if street_idx == 1 else 4 if street_idx == 2 else 5
        h.board = list(rng.choice(52, size=size, replace=False))
        if set(h.seats[0].hole) & set(h.board) or set(h.seats[1].hole) & set(h.board):
            continue
        h.pot_settled, h.bet = 100, 0
        h.act("raise", max(1, int(bet_frac * 100)))
        who = h.to_act
        if who is None:
            continue
        kind, _, _ = decide(h, who, profile, np.random.default_rng(0))
        if kind in ("call", "raise"):
            kept.append(hand_strength(h.seats[who].hole, h.board))
    return min(kept) if kept else None


def _sized_profile():
    p = _Prof(**{f"fold_vs_bet:{s}": 0.45 for s in ("flop", "turn", "river")})
    p.means = {}
    for s in ("flop", "turn", "river"):
        p.means[f"faced_size:{s}"] = 0.66
        p.means[f"faced_size:{s}#n"] = 200.0
    return p


@pytest.mark.parametrize("street_idx,name", [(1, "flop"), (2, "turn"), (3, "river")])
def test_a_bigger_bet_is_continued_against_tighter(street_idx, name):
    """A pooled fold frequency is measured at the sizes they usually face.

    Applied unchanged it made the bot's threshold identical at every size --
    MDF and pot odds were computed and then never bound.
    """
    prof = _sized_profile()
    small = _facing(street_idx, 0.33, prof)
    big = _facing(street_idx, 1.75, prof)
    assert small is not None and big is not None
    assert big > small + 0.15, f"{name}: {small:.3f} -> {big:.3f} is not price-sensitive"


def test_the_threshold_pivots_on_the_size_they_usually_face():
    """At their average faced size the bot uses their measured rate untouched."""
    prof = _sized_profile()
    at_usual = _facing(1, 0.66, prof)
    assert at_usual is not None
    assert abs(at_usual - 0.45) < 0.12


def test_the_bet_depth_in_the_reason_matches_the_action_named():
    """"3-bets ... at 2-bet depth" contradicted itself in one sentence.

    The blind is the 1-bet and an open is the 2-bet, so raising over an open
    makes a 3-bet. The label said so; the explanation said 2-bet depth.
    """
    import re
    profile = _Prof(three_bet=0.99, four_bet=0.99, five_bet=0.99)
    rng = np.random.default_rng(0)
    seen = 0
    for level, opener_to in ((1, 6), (2, 20), (3, 60)):
        for _ in range(60):
            h = Hand(_seats(4000, 4000, 4000), button=2, sb=1, bb=2,
                     rng=np.random.default_rng(int(rng.integers(1e9))))
            h.act("raise", opener_to)
            while h.raises < level and h.to_act is not None:
                h.act("raise", h.bet * 3)
            if h.to_act is None or h.raises != level:
                continue
            kind, _, why = decide(h, h.to_act, profile, rng)
            if kind != "raise" or "bet depth" not in why:
                continue
            named = re.search(r"(\d+)-bets", why)
            depth = re.search(r"at (\d+)-bet depth", why)
            if not named or not depth:
                continue
            assert named.group(1) == depth.group(1), why
            seen += 1
    assert seen, "no raise reasons were produced to check"


# -- the sim plays the table it is actually at --------------------------------

def test_a_villain_uses_the_book_for_the_table_size():
    """Heads-up and six-handed are two strategies, not one with a label."""
    from villain.sim import MIN_REGIME_HANDS, Villain

    class Book:
        def __init__(self, regime, hands):
            self.regime, self.hands = regime, hands

    pooled = Book("6max", 9999)
    hu = Book("hu", MIN_REGIME_HANDS + 1)
    six = Book("6max", MIN_REGIME_HANDS + 1)
    v = Villain(pooled, {"hu": hu, "6max": six})
    assert v.at(2) is hu
    assert v.at(6) is six


def test_a_thin_regime_book_falls_back_to_the_pooled_one():
    """A book on forty hands describes the right game and nothing else."""
    from villain.sim import MIN_REGIME_HANDS, Villain

    class Book:
        def __init__(self, regime, hands):
            self.regime, self.hands = regime, hands

    pooled = Book("6max", 9999)
    thin = Book("hu", MIN_REGIME_HANDS - 1)
    v = Villain(pooled, {"hu": thin})
    assert v.at(2) is pooled


def test_a_villain_without_regime_books_still_plays():
    from villain.sim import Villain

    class Book:
        regime, hands = "6max", 500
    pooled = Book()
    assert Villain(pooled).at(6) is pooled


def _bb_faces_btn_open_3max(hole):
    """Three-handed: button opens, SB folds, action on the BB with ``hole``."""
    from villain.botplay import _position
    from villain.cards import card_id
    h = Hand(_seats(400, 400, 400), button=0, sb=1, bb=2,
             rng=np.random.default_rng(1))
    assert _position(h, h.to_act) == "BTN"
    h.act("raise", 6)
    h.act("fold")
    assert _position(h, h.to_act) == "BB"
    a, b = hole
    h.seats[h.to_act].hole = (int(card_id(a)), int(card_id(b)))
    return h


def test_small_pairs_defend_the_bb_against_a_button_open():
    """33 from the BB 3-handed vs a BTN open is a call even for a tight player.

    Chen ranking put 22-55 below KTo, so a monotonic top-X% defend folded them.
    Even a 35% defender still set-mines small pairs. 72o still folds.
    """
    tight = _Prof(bb_defend=0.35, three_bet=0.05, three_bet_vs_steal=0.05)
    pair_calls = junk_calls = 0
    n = 40
    for i in range(n):
        rng = np.random.default_rng(i + 1)
        pair = _bb_faces_btn_open_3max(("3c", "3d"))
        junk = _bb_faces_btn_open_3max(("7c", "2d"))
        pair_calls += decide(pair, pair.to_act, tight, rng)[0] != "fold"
        junk_calls += decide(junk, junk.to_act, tight, rng)[0] != "fold"
    assert pair_calls >= n - 2, f"33 folded {n - pair_calls}/{n} times from the BB"
    assert junk_calls <= 2


def test_a_high_cbet_includes_the_bottom_of_the_range():
    """A maniac who c-bets 80% was betting the best 80%, so the betting range
    was too strong and practice taught folding to aggression."""
    from villain.botplay import _polar_bet
    assert _polar_bet(0.10, 0.80, 0.55) == "bluff"
    assert _polar_bet(0.90, 0.80, 0.55) == "value"
    assert _polar_bet(0.35, 0.80, 0.55) is None
    assert _polar_bet(0.10, 0.30, 0.55) is None
    assert _polar_bet(0.80, 0.30, 0.55) == "value"


# -- position keys the policy used to ignore ----------------------------------

def test_three_bet_follows_the_position_key():
    """BTN and BB facing the same open are different 3-bet frequencies."""
    from villain.botplay import _position
    profile = _Prof(**{"three_bet:BB": 0.50, "three_bet:BTN": 0.06, "three_bet": 0.10,
                       "cold_call:BB": 0.05, "cold_call:BTN": 0.05, "bb_defend": 0.55})
    def rate_from(pos, n=400):
        rng = np.random.default_rng(2)
        hits = tot = 0
        for k in range(n):
            h = Hand([Seat(str(i), 1000) for i in range(6)], button=0, sb=1, bb=2,
                     rng=np.random.default_rng(k + 1))
            # Fold to UTG and have them open, then fold to ``pos``.
            while h.to_act is not None and _position(h, h.to_act) != "UTG" and h.raises == 0:
                h.act("fold")
            if h.to_act is None or _position(h, h.to_act) != "UTG":
                continue
            h.act("raise", 6)
            g = 0
            while h.to_act is not None and _position(h, h.to_act) != pos and g < 8:
                h.act("fold")
                g += 1
            if h.to_act is None or _position(h, h.to_act) != pos or h.raises != 1:
                continue
            tot += 1
            hits += decide(h, h.to_act, profile, rng)[0] == "raise"
        return hits / tot if tot else 0
    bb = rate_from("BB")
    btn = rate_from("BTN")
    assert bb > btn + 0.20, f"BB 3-bet {bb:.2f} vs BTN {btn:.2f}"


def test_fold_vs_bet_reads_ip_and_oop_separately():
    """fold_vs_bet:{street}:{ip|oop} was measured and then never consulted."""
    profile = _Prof(**{"fold_vs_bet:flop:ip": 0.15, "fold_vs_bet:flop:oop": 0.80,
                       "fold_vs_bet:flop": 0.45, "raise_vs_bet:flop": 0.02})
    rng = np.random.default_rng(4)
    ip_folds = oop_folds = 0
    n = 180
    for k in range(n):
        h = Hand(_seats(400, 400), button=0, sb=1, bb=2,
                 rng=np.random.default_rng(k + 10))
        h.act("call")
        h.act("check")
        # Flop: BB first (OOP). Bet, BTN faces it IP.
        h.act("raise", max(4, int(0.5 * h.pot)))
        ip_folds += decide(h, h.to_act, profile, rng)[0] == "fold"
        h2 = Hand(_seats(400, 400), button=0, sb=1, bb=2,
                  rng=np.random.default_rng(k + 10_000))
        h2.act("call")
        h2.act("check")
        h2.act("check")                                 # BB checks
        h2.act("raise", max(4, int(0.5 * h2.pot)))      # BTN bets, BB faces OOP
        oop_folds += decide(h2, h2.to_act, profile, rng)[0] == "fold"
    assert oop_folds > ip_folds + 40, f"IP folded {ip_folds}/{n}, OOP {oop_folds}/{n}"


def test_a_short_stack_shoves_instead_of_minraising():
    """The UI stack knob was a chip count the policy never read."""
    profile = _Prof(rfi=0.99)
    rng = np.random.default_rng(0)
    short = Hand(_seats(40, 40), button=0, sb=1, bb=2, rng=np.random.default_rng(1))
    deep = Hand(_seats(400, 400), button=0, sb=1, bb=2, rng=np.random.default_rng(1))
    sk, sa, swhy = decide(short, 0, profile, rng)
    dk, da, dwhy = decide(deep, 0, profile, rng)
    assert sk == dk == "raise"
    assert sa == short.legal().max_raise_to, swhy
    assert da < 12, dwhy                      # a 2–5bb open, not a 200bb shove
    assert "shoves" in swhy
    assert "opens to" in dwhy


def test_a_low_spr_raise_is_a_shove():
    """Leaving a stub behind is not a sizing. SPR ≤ 2 gets the stack in."""
    from villain.botplay import _raise_or_jam
    h = Hand(_seats(30, 30), button=0, sb=1, bb=2, rng=np.random.default_rng(1))
    h.pot_settled = 80                          # ~0.3 SPR; anything ≤ 2 shoves
    lg = h.legal()
    _, to = _raise_or_jam(h, 0, lg, 8)
    assert to == lg.max_raise_to



def test_delayed_cbet_fires_after_a_checked_flop():
    """Initiative used to reset on a check-through, so this number was dead."""
    profile = _Prof(rfi=0.99, three_bet=0.01, bb_defend=0.99,
                    **{"cbet:flop": 0.01, "cbet:turn": 0.01, "delayed_cbet:turn": 0.99,
                       "donk:flop": 0.0, "probe:flop": 0.0, "probe:turn": 0.0})
    rng = np.random.default_rng(8)
    fires = 0
    seen = 0
    for k in range(80):
        h = Hand(_seats(400, 400), button=0, sb=1, bb=2,
                 rng=np.random.default_rng(k + 3))
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
        # Flop: BB checks, BTN checks (c-bet 1%).
        h.act("check")
        k2, a2, _ = decide(h, 0, profile, rng)
        h.act(k2, a2)
        if h.street != 2:
            continue
        seen += 1
        k3, a3, why = decide(h, 1 if h.to_act == 1 else 0, profile, rng)
        # BB first on the turn; they should check (no donk). Then BTN delayed-c-bets.
        if h.to_act == 1:
            h.act(k3, a3)
            k4, a4, why = decide(h, 0, profile, rng)
            fires += k4 == "raise" and "delayed" in why
        else:
            fires += k3 == "raise" and "delayed" in why
    assert seen >= 20, f"only {seen} checked flops reached the turn"
    assert fires > seen * 0.7, f"delayed c-bet fired {fires}/{seen}"


def test_three_bet_vs_ep_is_not_the_steal_number():
    """A 3-bet vs UTG used to play the pooled (or steal) rate."""
    from villain.botplay import _position
    profile = _Prof(**{"three_bet:BB:vs:UTG": 0.04, "three_bet:BB:vs:BTN": 0.70,
                       "three_bet:BB": 0.20, "three_bet": 0.12, "bb_defend": 0.40,
                       "cold_call:BB": 0.05})
    rng = np.random.default_rng(3)
    hits = tot = 0
    for k in range(350):
        h = Hand([Seat(str(i), 1000) for i in range(6)], button=0, sb=1, bb=2,
                 rng=np.random.default_rng(k + 4))
        while h.to_act is not None and _position(h, h.to_act) != "UTG" and h.raises == 0:
            h.act("fold")
        if h.to_act is None or _position(h, h.to_act) != "UTG":
            continue
        h.act("raise", 6)
        g = 0
        while h.to_act is not None and _position(h, h.to_act) != "BB" and g < 8:
            h.act("fold")
            g += 1
        if h.to_act is None or _position(h, h.to_act) != "BB" or h.raises != 1:
            continue
        tot += 1
        hits += decide(h, h.to_act, profile, rng)[0] == "raise"
    assert tot >= 80
    assert hits / tot < 0.20, f"3-bet vs UTG realized {hits / tot:.2f} over {tot}"


def test_vs_hero_three_bet_is_preferred_when_sampled():
    profile = _Prof(**{"three_bet": 0.05, "vs:three_bet": 0.80, "bb_defend": 0.50,
                       "cold_call": 0.05})
    rng = np.random.default_rng(0)
    hits = tot = 0
    for k in range(180):
        h = Hand(_seats(400, 400), button=0, sb=1, bb=2,
                 rng=np.random.default_rng(k + 1))
        h.hero_seat = 0
        h.act("raise", 6)
        tot += 1
        hits += decide(h, 1, profile, rng)[0] == "raise"
    assert hits / tot > 0.50, f"vs-hero 3-bet {hits / tot:.2f}"


def test_a_28bb_stack_flats_a_three_bet_when_that_is_their_number():
    """The 30bb shove-or-fold gate used to override a sampled continue rate."""
    profile = _Prof(rfi=0.99, four_bet=0.04, fold_to_three_bet=0.35)
    rng = np.random.default_rng(1)
    calls = 0
    seen = 0
    for k in range(80):
        h = Hand(_seats(56, 400), button=0, sb=1, bb=2,
                 rng=np.random.default_rng(k + 2))
        k0, a0, _ = decide(h, 0, profile, rng)
        if k0 != "raise":
            continue
        h.act(k0, a0)
        h.act("raise", 18)
        if h.to_act != 0:
            continue
        seen += 1
        kind, _, _ = decide(h, 0, profile, rng)
        if kind == "call":
            calls += 1
    assert seen >= 20
    assert calls > 0, f"never flatted {seen} 3-bets at 28bb with fold_to_three_bet 35%"


def test_cbet_reads_wet_and_dry_separately():
    from villain.botplay import _board_ctx
    from villain.cards import card_id
    h = Hand(_seats(400, 400), button=0, sb=1, bb=2, rng=np.random.default_rng(1))
    h.street = 1
    h.board = [int(card_id(c)) for c in ("9s", "8s", "7c")]
    tex, _, _, _ = _board_ctx(h)
    assert tex == "wet"
    h.board = [int(card_id(c)) for c in ("Kc", "9d", "2s")]
    tex, _, _, _ = _board_ctx(h)
    assert tex == "dry"


def test_a_polar_check_raise_includes_the_bottom():
    from villain.botplay import RAISE_VALUE_CAP, _polar_bet
    cap = RAISE_VALUE_CAP["flop"]
    assert _polar_bet(0.02, 0.15, cap) == "bluff"
    assert _polar_bet(0.98, 0.15, cap) == "value"
    assert _polar_bet(0.50, 0.15, cap) is None


def test_shown_river_bluffs_set_the_polar_split():
    """VALUE_CAP used to polarize everyone the same; their showdowns decide."""
    from villain.botplay import VALUE_CAP, _polar_bet, _street_value_cap
    bluffer = _Prof(**{"river_bet_bluff": 0.50})
    cap = _street_value_cap(bluffer, "river", 0.70, VALUE_CAP["river"])
    assert _polar_bet(0.05, 0.70, cap) == "bluff"
    merged = _Prof(**{"river_bet_bluff": 0.08})
    cap2 = _street_value_cap(merged, "river", 0.70, VALUE_CAP["river"])
    assert _polar_bet(0.10, 0.70, cap2) is None
    assert _polar_bet(0.90, 0.70, cap2) == "value"


def test_fold_vs_raise_beats_mdf_when_sampled():
    """A station who calls 80% of flop raises was still folding at the theory cut."""
    profile = _Prof(**{"fold_vs_raise:flop": 0.20, "raise_vs_bet:flop": 0.02})
    rng = np.random.default_rng(0)
    n = folds = 0
    for k in range(150):
        h = Hand(_seats(400, 400), button=0, sb=1, bb=2,
                 rng=np.random.default_rng(k + 20))
        h.act("call")
        h.act("check")
        if h.street != 1:
            continue
        h.act("check")                                 # BB checks
        h.act("raise", 20)                             # BTN bets
        h.act("raise", 60)                             # BB raises
        if h.to_act != 0:
            continue
        n += 1
        folds += decide(h, 0, profile, rng)[0] == "fold"
    assert n >= 80
    assert folds / n < 0.40, f"fold_vs_raise realized {folds / n:.2f} vs 0.20"


