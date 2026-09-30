"""The session review: what it flags, what it leaves alone, and that every
word it puts on screen is defined."""

import json

import pytest

from tests.helpers import ev, pokernow_hand
from villain.glossary import TERMS
from villain.review import (
    SIZE_BANDS,
    changes,
    export_text,
    fixes,
    folds_by_size,
    key_hands,
    session_review,
    stale_read,
    trend,
)
from villain.stats import Ratio, StatBook


def _board(flop, turn, river):
    return [ev(9, turn=1, run=1, cards=flop), ev(9, turn=2, run=1, cards=[turn]),
            ev(9, turn=3, run=1, cards=[river])]


def _hand(events, hero=("7h", "2c"), villain=("As", "Ad"), hand_id="h1"):
    """Heads-up: seat 1 (hero) on the button, seat 2 in the big blind."""
    return pokernow_hand([(1, 5000, list(hero)), (2, 5000, list(villain))],
                         dealer=1, events=events, hand_id=hand_id)


# -- folds by bet size ------------------------------------------------------------

def test_a_fold_to_a_third_pot_bet_is_priced_against_a_quarter():
    flop = ["Kd", "9s", "4c"]
    events = [ev(3, 1, 5), ev(2, 2, 10), ev(7, 1, 10), ev(0, 2),
              ev(9, turn=1, run=1, cards=flop), ev(8, 2, 6), ev(11, 1),
              ev(16, 2, 6), ev(10, 2, 20, pot=20)]
    [band] = folds_by_size([_hand(events)], "id1")
    assert band.label == SIZE_BANDS[0][0]
    assert (band.faced, band.folded) == (1, 1)
    # 6 into 20: bet / (pot + bet) = 6 / 26.
    assert band.max_fold == pytest.approx(6 / 26)


# -- flags on the big pots ------------------------------------------------------------

def _bluff_after_check_raise(hero=("7h", "2c")):
    return _hand([
        ev(3, 1, 5), ev(2, 2, 10), ev(8, 1, 30), ev(7, 2, 30),
        ev(9, turn=1, run=1, cards=["Kd", "Qs", "9c"]),
        ev(0, 2), ev(8, 1, 30), ev(8, 2, 120), ev(7, 1, 120),
        ev(9, turn=2, run=1, cards=["4h"]), ev(0, 2), ev(0, 1),
        ev(9, turn=3, run=1, cards=["3d"]), ev(0, 2), ev(8, 1, 300), ev(7, 2, 300),
        ev(15), ev(12, 1, cards=list(hero)), ev(12, 2, cards=["As", "Ad"]),
        ev(10, 2, 900, pot=900),
    ], hero=hero)


def test_a_big_bluff_into_a_player_who_raised_is_flagged():
    [pot] = key_hands([_bluff_after_check_raise()], "id1", {})
    kinds = [f["kind"] for f in pot["flags"]]
    assert kinds == ["bluff_into_strength"]
    assert "p2" in pot["flags"][0]["text"], "names who showed the strength"


def test_the_same_bet_with_the_nuts_is_not_flagged():
    [pot] = key_hands([_bluff_after_check_raise(hero=("Kh", "Kc"))], "id1", {})
    assert not pot["flags"]


def test_a_flagged_hand_is_one_fix_however_many_streets_it_spans():
    hand = {"hand_id": "h1", "pot_bb": 90.0,
            "flags": [{"kind": "bluff_into_strength", "bb": 12.0, "text": "turn"},
                      {"kind": "bluff_into_strength", "bb": 30.0, "text": "river"}]}
    [item] = fixes([], {"flatted_bluffs": [], "three_bets": [], "bluffs": 0, "vs": None},
                   [], [hand])
    assert item["weight"] == 2 and "turn" in item["detail"] and "river" in item["detail"]


def test_fixes_rank_by_decisions_touched():
    spot = {"flagged": True, "rate": 0.5, "target": 0.82, "n": 60, "label": "Button",
            "misfolds": [{"hand_id": str(i), "cls": "K5o"} for i in range(9)], "loose": []}
    small = dict(spot, misfolds=spot["misfolds"][:3], label="Small blind")
    items = fixes([small, spot], {"flatted_bluffs": [], "three_bets": [], "bluffs": 0,
                                  "vs": None}, [], [])
    assert [i["title"] for i in items] == ["Play more hands: button",
                                          "Play more hands: small blind"]


# -- the trend -----------------------------------------------------------------------------

def _sitting(fold: bool, n: int = 12):
    events = ([ev(3, 1, 5), ev(2, 2, 10), ev(11, 1), ev(16, 2, 5), ev(10, 2, 15, pot=15)]
              if fold else
              [ev(3, 1, 5), ev(2, 2, 10), ev(8, 1, 30), ev(11, 2), ev(16, 1, 20),
               ev(10, 1, 20, pot=20)])
    hands = []
    for i in range(n):
        hand = _hand(events, hero=("Kh", "5c"), hand_id=f"{fold}{i}")
        hands.append(hand)
    return ({"id": int(fold)}, hands)


@pytest.mark.parametrize("order,status", [
    ((True, False), "fixed"),
    ((False, True), "new"),
    ((True, True), "persistent"),
])
def test_trend_says_whether_a_leak_is_new_persistent_or_fixed(order, status):
    rows = trend([_sitting(fold) for fold in order], "id1")
    row = next(r for r in rows if r["key"] == "hu:open")
    assert row["status"] == status


# -- opponents ---------------------------------------------------------------------------------

def _book(stat, hits, opps):
    book = StatBook(player_id="1", name="x", regime="3max", hands=int(opps))
    book.ratios[stat] = Ratio(hits=hits, opps=opps)
    return book


def test_a_change_is_measured_against_the_rest_of_their_history():
    tonight = {"3max": _book("limp", 0, 40)}
    stored = {"3max": _book("limp", 60, 340)}           # tonight included
    [change] = changes(tonight, stored)
    assert change["tonight"] == 0.0
    assert change["usual"] == pytest.approx(60 / 300)


def test_a_small_wobble_is_not_a_change():
    assert not changes({"3max": _book("limp", 7, 40)}, {"3max": _book("limp", 67, 340)})


def test_a_limper_who_stopped_limping_is_a_stale_read():
    moved = [{"stat": "limp", "tonight": 0.0, "usual": 0.22}]
    assert stale_read("limper", moved)["stat"] == "limp"
    assert stale_read("limper", [{"stat": "limp", "tonight": 0.4, "usual": 0.22}]) is None
    # Raise share moving is not what the limper plan stands on.
    assert stale_read("limper", [{"stat": "raise_share", "tonight": 0.7, "usual": 0.4}]) is None


# -- end to end ----------------------------------------------------------------------------------

def test_the_review_of_a_stored_sitting_is_serializable(seeded):
    from villain.webapp.heroview import _cached_hero_id
    hero = _cached_hero_id(seeded)
    for session in seeded.sessions():
        review = session_review(seeded, session, hero)
        json.dumps(review)
        assert len(review["key_hands"]) <= 6
        assert review["players"]


def test_the_export_shows_hero_cards_and_hides_unshown_ones():
    hand = _hand([ev(3, 1, 5), ev(2, 2, 10), ev(11, 1), ev(16, 2, 5), ev(10, 2, 15, pot=15)])
    text = export_text([hand], {}, "id1")
    assert "[7h 2c]" in text
    assert "As" not in text, "a folded-to opponent's cards never showed"


def test_every_term_the_sessions_page_explains_is_defined():
    """The page calls termTip on these; an undefined one renders an empty
    tooltip beside a number nobody can then interpret."""
    used = {"fix first", "all-in equity", "reference range", "misfold", "loose play",
            "3-bet bluff", "weak call vs 3-bet", "most you can fold", "persistent",
            "new", "fixed", "provisional", "stale read", "changed tonight"}
    assert used <= set(TERMS)
