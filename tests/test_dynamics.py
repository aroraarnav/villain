"""Adjustments: the against-you slice read against the player's own baseline.

Built from constructed books rather than the fixture. Twenty sample hands
cannot clear the sample floors, and the point of these tests is the floors and
the arithmetic behind them.
"""

import pytest

from villain.dynamics import ADJUSTMENT_PRIOR, MIN_GAP, MIN_OPPS, Adjustment, adjustments
from villain.stats import VS_HERO, StatBook

TABLE_SIZE = {"hu": 2, "3max": 3, "6max": 6, "full": 9}
STAT = "fold_vs_bet:flop"


def book(regime: str, counts: dict[str, tuple[float, float]],
         hands: int = 200) -> StatBook:
    b = StatBook(player_id="p", name="villain", regime=regime, hands=hands)
    for stat, (hits, opps) in counts.items():
        b.ratios[stat].hits = float(hits)
        b.ratios[stat].opps = float(opps)
    b.meters["table_size"].add(TABLE_SIZE[regime])
    return b


def one(by_regime) -> Adjustment | None:
    found = adjustments(by_regime)
    return found[0] if found else None


def test_a_clear_shift_is_found():
    """Folds to half the pool's bets, but almost never to yours."""
    found = one({"6max": book("6max", {
        STAT: (45, 100),
        VS_HERO + STAT: (4, 40),
    })})
    assert found is not None
    assert found.stat == STAT
    assert found.direction == "less"
    assert found.versus < found.baseline
    assert found.confidence >= 0.85
    assert found.opps == 40


def test_playing_you_the_same_reports_nothing():
    assert adjustments({"6max": book("6max", {
        STAT: (45, 100),
        VS_HERO + STAT: (18, 40),
    })}) == []


def test_the_baseline_excludes_you():
    """The slice sits inside the pooled counter, so it has to come out.

    Pooled is 45/100 and the slice is 8/40, which leaves 37/60 -- about 62% --
    against everybody else. Reading the slice against the 45% total instead
    would compare a number with something that contains it, and understate
    every difference exactly when the sample is big enough to be worth having."""
    found = one({"6max": book("6max", {
        STAT: (45, 100),
        VS_HERO + STAT: (8, 40),
    })})
    assert found is not None
    assert found.baseline == pytest.approx(0.60, abs=0.05)
    assert found.baseline_opps == 60


def test_no_baseline_when_you_are_the_only_opponent():
    """A heads-up database has nothing to compare against, and says so.

    Every decision he made was against you, so "against you" and "in general"
    are the same hands and their difference is a number subtracted from
    itself."""
    assert adjustments({"hu": book("hu", {
        STAT: (40, 80),
        VS_HERO + STAT: (40, 80),
    })}) == []


def test_a_thin_slice_reports_nothing():
    assert adjustments({"6max": book("6max", {
        STAT: (45, 100),
        VS_HERO + STAT: (0, MIN_OPPS - 1),
    })}) == []


def test_a_certain_but_tiny_shift_reports_nothing():
    """Enough hands makes any difference certain. It still has to matter."""
    found = one({"6max": book("6max", {
        STAT: (450, 1000),
        VS_HERO + STAT: (188, 400),      # ~2 points below the baseline
    })})
    assert found is None


def test_direction_and_gap_agree():
    found = one({"6max": book("6max", {
        STAT: (30, 100),
        VS_HERO + STAT: (30, 40),
    })})
    assert found is not None
    assert found.direction == "more"
    assert found.gap > MIN_GAP


def test_the_biggest_shift_comes_first():
    found = adjustments({"6max": book("6max", {
        STAT: (45, 100), VS_HERO + STAT: (2, 40),
        "fold_to_cbet:turn": (45, 100), VS_HERO + "fold_to_cbet:turn": (26, 40),
    })})
    assert len(found) == 2
    assert [a.stat for a in found] == sorted(
        [a.stat for a in found], key=lambda s: -abs(
            next(x for x in found if x.stat == s).gap))
    assert abs(found[0].gap) >= abs(found[1].gap)


def test_one_decision_is_reported_once():
    """Fold, call and raise against a bet add to one, so a shift in one is a
    shift in the others. Reporting all three says the same thing three times
    and then wins the ordering by weight of numbers."""
    found = adjustments({"6max": book("6max", {
        "fold_vs_bet:flop": (45, 100), VS_HERO + "fold_vs_bet:flop": (2, 40),
        "call_vs_bet:flop": (40, 100), VS_HERO + "call_vs_bet:flop": (30, 40),
        "raise_vs_bet:flop": (15, 100), VS_HERO + "raise_vs_bet:flop": (8, 40),
    })})
    assert len(found) == 1
    assert found[0].stat == "fold_vs_bet:flop"       # the widest of the three


def test_the_same_decision_on_another_street_is_its_own_read():
    found = adjustments({"6max": book("6max", {
        "fold_vs_bet:flop": (45, 100), VS_HERO + "fold_vs_bet:flop": (2, 40),
        "fold_vs_bet:turn": (45, 100), VS_HERO + "fold_vs_bet:turn": (38, 40),
    })})
    assert {a.stat for a in found} == {"fold_vs_bet:flop", "fold_vs_bet:turn"}


# -- table size -------------------------------------------------------------


def test_a_table_size_difference_is_not_an_adjustment():
    """The confound this feature would otherwise invent.

    He folds 40% at six-handed and 70% heads-up -- normal, they are different
    games -- and you have only played him heads-up. Pooling the raw counts
    would report that he folds 30 points more against you. Measuring each
    regime's slice against *that regime's* baseline leaves nothing, which is
    correct: he is not doing anything to you, he is playing a shorter table."""
    found = adjustments({
        "6max": book("6max", {STAT: (40, 100)}, hands=300),
        "hu": book("hu", {STAT: (42, 60), VS_HERO + STAT: (21, 30)}, hands=100),
    })
    assert found == [], f"invented an adjustment from table size: {found}"


def test_a_real_shift_still_carries_across_table_sizes():
    """Same setup, except heads-up he folds to you far less than to others."""
    found = one({
        "6max": book("6max", {STAT: (40, 100)}, hands=300),
        "hu": book("hu", {STAT: (44, 90), VS_HERO + STAT: (2, 30)}, hands=100),
    })
    assert found is not None
    assert found.direction == "less"
    assert found.regime == "6max"        # expressed on the table he plays most
    assert found.opps == 30              # observed, not the discounted weight
    assert found.borrowed_opps < found.opps


def test_borrowed_counts_alone_cannot_clear_the_sample_floor():
    """They arrive already shrunk; letting them count twice is the bug."""
    assert adjustments({
        "6max": book("6max", {STAT: (40, 100)}, hands=300),
        "hu": book("hu", {STAT: (44, 90),
                          VS_HERO + STAT: (0, MIN_OPPS - 1)}, hands=100),
    }) == []


# -- shape ------------------------------------------------------------------


def test_empty_input():
    assert adjustments({}) == []
    assert adjustments({"6max": StatBook(regime="6max")}) == []


def test_a_slice_with_no_pooled_counter_is_skipped():
    assert adjustments({"6max": book("6max", {VS_HERO + STAT: (10, 40)})}) == []


def test_the_prior_is_what_decides_how_much_evidence_is_needed():
    """Documented as a judgment call, so the knob has to actually be one."""
    counts = {STAT: (45, 100), VS_HERO + STAT: (2, 20)}
    assert adjustments({"6max": book("6max", counts)})
    # Believe much harder that he plays you like everyone, and 20 decisions
    # stop being enough to say otherwise.
    import villain.dynamics as dyn
    original = dyn.ADJUSTMENT_PRIOR
    dyn.ADJUSTMENT_PRIOR = 500.0
    try:
        assert adjustments({"6max": book("6max", counts)}) == []
    finally:
        dyn.ADJUSTMENT_PRIOR = original
    assert ADJUSTMENT_PRIOR == original
