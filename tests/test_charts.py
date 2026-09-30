"""Reference ranges, and your own preflop graded against them.

The charts are hand-written, so the tests hold them to the numbers they claim:
each lands on its stated frequency, each claims the frequency the solver table
in :mod:`villain.gto` gives where one exists, and the openers nest the way
seats do -- a hand opened early is opened later too.
"""

import pytest

from tests.helpers import ev, pokernow_hand
from villain.charts import (
    ALL_CLASSES,
    CHARTS,
    THREE_BET_BLUFFS,
    audit,
    expand,
    flatted_bluffs,
    share,
    spot_decisions,
)
from villain.gto import GTO
from villain.priors import HEADS_UP, SHORT, THREE

# -- the charts ------------------------------------------------------------------

def test_there_are_169_starting_hands_covering_the_deck():
    assert len(ALL_CLASSES) == 169
    assert share(ALL_CLASSES) == pytest.approx(1.0)


@pytest.mark.parametrize("key", sorted(CHARTS))
def test_each_chart_lands_on_its_stated_frequency(key):
    chart = CHARTS[key]
    assert chart.share == pytest.approx(chart.target, abs=0.04), key


@pytest.mark.parametrize("key,regime,stat", [
    ("hu:open", HEADS_UP, "rfi:BTN"),
    ("3max:open:BTN", THREE, "rfi:BTN"),
    ("3max:open:SB", THREE, "rfi:SB"),
    ("ring:open:SB", SHORT, "rfi:SB"),
    ("ring:open:BTN", SHORT, "rfi:BTN"),
    ("ring:open:CO", SHORT, "rfi:CO"),
    ("ring:open:HJ", SHORT, "rfi:HJ"),
    ("ring:open:MP", SHORT, "rfi:UTG"),
])
def test_opening_charts_claim_the_solver_frequency(key, regime, stat):
    """One source of truth for "how often": the charts may not drift from the
    table the rest of the tool already cites."""
    assert CHARTS[key].target == GTO[regime][stat]


def test_openers_nest_from_early_to_late():
    order = ["ring:open:EP", "ring:open:MP", "ring:open:HJ", "ring:open:CO", "ring:open:BTN"]
    for tight, loose in zip(order, order[1:]):
        extra = CHARTS[tight].hands - CHARTS[loose].hands
        assert not extra, f"{tight} opens {sorted(extra)} that {loose} folds"


def test_every_three_bet_bluff_is_a_real_hand():
    assert THREE_BET_BLUFFS <= ALL_CLASSES


@pytest.mark.parametrize("spec,expected", [
    ("K5o+", {"K5o", "K6o", "K7o", "K8o", "K9o", "KTo", "KJo", "KQo"}),
    ("55+", {"55", "66", "77", "88", "99", "TT", "JJ", "QQ", "KK", "AA"}),
    ("A5s", {"A5s"}),
    ("pairs -22 -33", {r + r for r in "456789TJQKA"}),
])
def test_range_shorthand(spec, expected):
    assert expand(spec) == expected


def test_an_unreadable_token_is_refused_rather_than_ignored():
    """A typo that silently matched nothing would shrink a chart and pass."""
    with pytest.raises(ValueError):
        expand("K5x")


# -- which spot a decision was -------------------------------------------------------

def _hu(button_cards, button_act, bb_cards=("2c", "7d"), bb_act=None):
    """Heads-up, seat 1 on the button (small blind), seat 2 in the big blind."""
    events = [ev(3, 1, 5), ev(2, 2, 10)]
    if button_act == "fold":
        events += [ev(11, 1), ev(16, 2, 5), ev(10, 2, 15, pot=15)]
    elif button_act == "raise":
        events.append(ev(8, 1, 30))
        if bb_act == "fold":
            events += [ev(11, 2), ev(16, 1, 20), ev(10, 1, 20, pot=20)]
        elif bb_act == "3bet":
            events += [ev(8, 2, 120), ev(11, 1), ev(16, 2, 90), ev(10, 2, 60, pot=60)]
    return pokernow_hand([(1, 1000, list(button_cards)), (2, 1000, list(bb_cards))],
                         dealer=1, events=events)


def test_a_heads_up_button_fold_is_an_open_spot():
    hand = _hu(("Kh", "5c"), "fold")
    [d] = spot_decisions(hand, "id1")
    assert (d.chart, d.cls, d.action) == ("hu:open", "K5o", "fold")


def test_the_big_blind_facing_the_open_is_a_defend_spot():
    hand = _hu(("Ah", "Kd"), "raise", bb_cards=("7s", "6h"), bb_act="fold")
    [d] = spot_decisions(hand, "id2")
    assert (d.chart, d.cls, d.action) == ("hu:defend", "76o", "fold")


def test_a_three_bet_after_opening_is_its_own_decision():
    hand = _hu(("Qc", "Jd"), "raise", bb_cards=("As", "Ks"), bb_act="3bet")
    decisions = spot_decisions(hand, "id1")
    assert [d.chart for d in decisions] == ["hu:open", "vs3bet"]
    assert decisions[1].action == "fold"


def test_the_audit_names_the_misfold_and_not_the_right_fold():
    hands = [_hu(("Kh", "5c"), "fold"), _hu(("7h", "2c"), "fold")]
    for i, hand in enumerate(hands):
        hand.hand_id = f"h{i}"
    [report] = audit(hands, "id1")
    assert [d.cls for d in report.misfolds] == ["K5o"]
    assert report.rate == 0.0


def test_a_flatted_wheel_ace_in_the_big_blind_is_a_three_bet_candidate():
    events = [ev(3, 1, 5), ev(2, 2, 10), ev(8, 1, 30), ev(7, 2, 30),
              ev(9, turn=1, run=1, cards=["2d", "9c", "Kh"]),
              ev(0, 2), ev(0, 1), ev(9, turn=2, run=1, cards=["3s"]),
              ev(0, 2), ev(0, 1), ev(9, turn=3, run=1, cards=["8d"]),
              ev(0, 2), ev(0, 1), ev(10, 1, 60, pot=60)]
    hand = pokernow_hand([(1, 1000, ["Qd", "Qc"]), (2, 1000, ["As", "4s"])],
                         dealer=1, events=events)
    reports = audit([hand], "id2")
    assert [d.cls for d in flatted_bluffs(reports)] == ["A4s"]
