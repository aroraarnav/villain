"""The range engine: a frequency is a cut inside a range, not inside the deck."""

import numpy as np
import pytest

from villain.botplay import _OPEN_PCT
from villain.cards import card_id
from villain.ranges import COMBOS, N_COMBOS, Ranges, class_scores, index_of


def hole(a, b):
    return (int(card_id(a)), int(card_id(b)))


OPEN = class_scores(_OPEN_PCT)


def test_every_combo_is_indexable_both_ways():
    assert N_COMBOS == 1326
    for a, b in COMBOS[::97]:
        assert index_of((a, b)) == index_of((b, a))


def test_a_fresh_range_reproduces_the_class_table():
    """Uniform range, class ordering -> the percentile the table already says."""
    r = Ranges(2)
    for name, cards in [("AA", ("Ac", "Ad")), ("KQo", ("Kc", "Qd")),
                        ("72o", ("7c", "2d")), ("T9o", ("Tc", "9d"))]:
        got = r.percentile(0, hole(*cards), OPEN)
        assert abs(got - _OPEN_PCT[name]) < 0.005, f"{name}: {got} vs {_OPEN_PCT[name]}"


def test_narrowing_to_the_top_makes_that_slice_the_whole_range():
    """After keeping the top 10%, the median of what is left is the old 95th."""
    from villain.ranges import CLASS_NAMES
    r = Ranges(2)
    r.narrow(0, OPEN, [(0.90, 1.0)])
    kept = {n for n, weight in zip(CLASS_NAMES, r.w[0]) if weight > 0}
    assert "AA" in kept and "KK" in kept
    assert "72o" not in kept and "T9o" not in kept
    # AA was the top of the full range; inside the top decile it still is.
    assert r.percentile(0, hole("Ac", "Ad"), OPEN) > 0.9


def test_a_hand_that_cleared_a_gate_is_inside_the_band_that_gate_kept():
    """The invariant that makes the tracker self-consistent."""
    rng = np.random.default_rng(0)
    for _ in range(200):
        r = Ranges(2)
        f = float(rng.uniform(0.05, 0.6))
        # pick a hand that clears the top-f gate, then narrow to that band
        k = int(rng.integers(N_COMBOS))
        h = (int(COMBOS[k][0]), int(COMBOS[k][1]))
        before = r.percentile(0, h, OPEN)
        if before < 1 - f:
            continue
        r.narrow(0, OPEN, [(1 - f, 1.0)])
        assert r.w[0][k] > 0, "a hand that cleared the gate was narrowed away"


def test_the_conditional_gate_is_far_tighter_than_the_unconditional_one():
    """The bug this module exists for, at the two rates that exposed it.

    A player who 4-bets 16.3% of the range they opened with, opening ~40%.
    Read as a cut on the whole deck that gate is 0.837 and T9o (0.804) clears
    it on noise. Read inside the opening range -- which is what the statistic
    was counted over -- T9o sits at 0.51 and is nowhere near the cut."""
    r = Ranges(2)
    r.narrow(0, OPEN, [(1 - 0.40, 1.0)])          # they opened: top 40%
    t9o = r.percentile(0, hole("Tc", "9d"), OPEN)
    gate = 1 - 0.163                              # a real measured four_bet
    assert _OPEN_PCT["T9o"] > gate - 0.05, "the old unconditional gate was reachable"
    assert t9o < gate - 0.30, f"T9o sits at {t9o:.3f} inside a 40% opening range"

    # And one who 5-bets 11.6% of the range they 3-bet with, 3-betting ~11.8%.
    r2 = Ranges(2)
    r2.narrow(0, OPEN, [(1 - 0.118, 1.0)])        # they 3-bet: top ~12%
    kqo = r2.percentile(0, hole("Kc", "Qd"), OPEN)
    assert _OPEN_PCT["KQo"] > 1 - 0.116, "KQo cleared the old gate outright"
    assert kqo < 1 - 0.116, f"KQo sits at {kqo:.3f} inside a 12% 3-betting range"


def test_board_percentile_matches_the_old_hand_strength():
    """The absolute measure is preserved for the places that want it."""
    from villain.botplay import hand_strength
    board = [int(card_id(c)) for c in ("Kd", "7c", "2h")]
    r = Ranges(2)
    for cards in [("Ks", "4c"), ("Ah", "Qh"), ("7d", "7s")]:
        h = hole(*cards)
        assert abs(r.board_percentile(h, board) - hand_strength(h, board)) < 1e-9


def test_board_blockers_drop_impossible_combos():
    board = [int(card_id(c)) for c in ("Kd", "7c", "2h")]
    r = Ranges(2)
    w = r.live_weights(0, board)
    assert w[index_of(hole("Kd", "Qs"))] == 0      # uses a board card
    assert w[index_of(hole("Ks", "Qs"))] > 0


def test_nothing_in_range_beats_a_ten_on_a_double_paired_ten_board():
    """The cut the river fold was using: weight above QT, not its midpoint.

    After a line that leaves mostly broadway tens, QT is tied for the nuts
    with every other ten -- and the midpoint of that pile is the middle
    of the range, not the top."""
    from villain.ranges import CLASS_NAMES
    board = [int(card_id(c)) for c in ("9s", "3h", "Tc", "9d", "Th")]
    r = Ranges(2)
    keep = {"JTo", "QTo", "KTo", "ATo", "JJ", "QQ", "JTs", "QTs"}
    r.w[0] = np.array([1.0 if n in keep else 0.0 for n in CLASS_NAMES])
    cache = r.board_cache(board)
    qt = hole("Qc", "Td")
    assert r.better_frac(0, qt, cache.score, board) == 0.0
    assert r.percentile(0, qt, cache.play, board) < 0.75


def test_a_top_continue_cut_keeps_the_whole_boat_pile():
    """The range tracker used the midpoint too, so a call/raise dropped QT."""
    from villain.ranges import CLASS_NAMES, index_of
    board = [int(card_id(c)) for c in ("9s", "3h", "Tc", "9d", "Th")]
    r = Ranges(2)
    keep = {"JTo", "QTo", "KTo", "ATo", "JJ", "QQ", "JTs", "QTs"}
    r.w[0] = np.array([1.0 if n in keep else 0.0 for n in CLASS_NAMES])
    cache = r.board_cache(board)
    qt = hole("Qc", "Td")
    r.narrow(0, cache.score, [(0.79, 1.0)], board)
    assert r.w[0][index_of(qt)] > 0


def test_narrowing_cannot_delete_the_hand_they_hold():
    from villain.ranges import CLASS_NAMES, index_of
    board = [int(card_id(c)) for c in ("9s", "3h", "Tc", "9d", "Th")]
    r = Ranges(2)
    keep = {"JTo", "QTo", "KTo", "ATo", "JJ", "QQ"}
    r.w[0] = np.array([1.0 if n in keep else 0.0 for n in CLASS_NAMES])
    cache = r.board_cache(board)
    qt = hole("Qc", "Td")
    r.narrow(0, cache.play, [(0.0, 0.05)], board, keep_hole=qt)
    assert r.w[0][index_of(qt)] > 0


def test_top_made_names_the_boat_not_the_preflop_class():
    from villain.ranges import CLASS_NAMES
    board = [int(card_id(c)) for c in ("9s", "3h", "Tc", "9d", "Th")]
    r = Ranges(2)
    keep = {"JTo", "QTo", "KTo", "ATo", "JJ", "QQ"}
    r.w[0] = np.array([1.0 if n in keep else 0.0 for n in CLASS_NAMES])
    names = [n for n, _ in r.top_made(0, board, 8)]
    assert "full house" in names
    assert "QTo" not in names


def test_a_flush_draw_outranks_junk_on_the_flop():
    """Made-hand ranking scored a combo draw with 72o. Playability does not."""
    board = [int(card_id(c)) for c in ("Ks", "9s", "2c")]
    r = Ranges(2)
    play = r.board_cache(board).play
    draw = play[index_of(hole("8s", "7s"))]
    junk = play[index_of(hole("7h", "3d"))]
    made = play[index_of(hole("9c", "9d"))]
    assert draw > junk + 0.10
    assert made > draw


def test_top_classes_lists_the_weight_left_in_a_range():
    r = Ranges(2)
    r.narrow(0, OPEN, [(0.90, 1.0)])
    names = [n for n, _ in r.top_classes(0, 20)]
    assert "AA" in names
    assert "72o" not in names


def test_a_range_is_never_narrowed_to_nothing():
    r = Ranges(2)
    r.narrow(0, OPEN, [(0.999999, 1.0)])
    r.narrow(0, OPEN, [(0.0, 0.0000001)])
    assert r.w[0].sum() > 0


def test_ties_inside_a_class_share_one_percentile():
    """All six combos of a pair must sit together, or a cut splits the class."""
    r = Ranges(2)
    pairs = [hole("Ac", "Ad"), hole("Ah", "As"), hole("Ac", "As")]
    got = {round(r.percentile(0, h, OPEN), 9) for h in pairs}
    assert len(got) == 1


def test_playability_never_reorders_the_made_hands():
    """The draw bonus must not lift a hand past one that beats it.

    Playability is made hand *plus* what it can still become, and the ordering
    it produces is the frequency cut every postflop decision is taken on. A
    flat bonus on top of a percentile already near the ceiling inverted it: on
    a paired board a made straight scored 1.149 against quads at 0.999, so the
    top of a c-betting range was a straight and the quads were in the bluffs."""
    board = [int(card_id(c)) for c in ("5c", "6d", "7h", "7s")]
    play = Ranges(2).board_cache(board).play
    straight = play[index_of(hole("8h", "9d"))]
    quads = play[index_of(hole("7d", "7c"))]
    boat = play[index_of(hole("5s", "5d"))]
    assert quads > boat > straight
    assert play.max() <= 1.0


def test_a_made_straight_is_not_also_drawing_to_one():
    """Four to a flush already stops counting at five suited; so must this."""
    from villain.ranges import _draw_bonus
    board = np.array([int(card_id(c)) for c in ("5c", "6d", "7h", "2s")], dtype=np.int64)
    made = np.array([[int(card_id("8h")), int(card_id("9d"))]], dtype=np.int64)
    drawing = np.array([[int(card_id("8h")), int(card_id("Kd"))]], dtype=np.int64)
    assert _draw_bonus(drawing, board)[0] > 0.15      # 5-6-7-8: a nine or a four
    assert _draw_bonus(made, board)[0] < 0.15         # already there


def test_jqka_is_a_gutshot_not_an_open_ender():
    """It takes a ten and nothing else -- four outs, not eight."""
    from villain.ranges import _draw_bonus
    board = np.array([int(card_id(c)) for c in ("Kh", "As", "2c")], dtype=np.int64)
    one_way = np.array([[int(card_id("Jd")), int(card_id("Qc"))]], dtype=np.int64)
    board_low = np.array([int(card_id(c)) for c in ("6h", "7s", "2c")], dtype=np.int64)
    two_way = np.array([[int(card_id("5d")), int(card_id("4c"))]], dtype=np.int64)
    assert _draw_bonus(one_way, board)[0] == pytest.approx(0.08)
    assert _draw_bonus(two_way, board_low)[0] == pytest.approx(0.18)
