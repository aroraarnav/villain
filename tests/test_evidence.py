"""Evidence and replay: every number must be traceable to the hands behind it."""

import pytest

from villain.db import Store
from villain.evidence import find, street_of
from villain.features import record_hands
from villain.model import Street
from villain.profile import build_unified
from villain.replay import replay


@pytest.fixture
def stored(tmp_path, hands):
    with Store(tmp_path / "v.db") as store:
        store.add_hands(hands)
        yield store


@pytest.mark.parametrize("stat,expected", [
    ("fold_vs_bet:river", Street.RIVER),
    ("cbet:flop", Street.FLOP),
    ("fold_vs_bet:turn:big", Street.TURN),
    ("vpip", None),
])
def test_street_is_read_off_the_stat_name(stat, expected):
    assert street_of(stat) is expected


def test_evidence_count_matches_the_statistic(hands):
    """The point of the feature: the hands listed must be the hands counted.

    Both come from the same extraction, so any drift here means the evidence
    is lying about which hands produced the number."""
    books = record_hands(hands)
    for player_key, by_regime in books.items():
        for regime, book in by_regime.items():
            for stat in ("vpip", "pfr", "fold_vs_bet:flop", "cbet:flop"):
                ratio = book.ratios.get(stat)
                if not ratio or ratio.opps < 2:
                    continue
                found = [e for e in find(hands, player_key, stat) if e.regime == regime]
                assert len(found) == ratio.opps, f"{player_key} {regime} {stat}"
                assert sum(1 for e in found if e.hit) == ratio.hits


def test_evidence_includes_the_misses(hands):
    """Opportunities, not just hits: showing only the folds would misrepresent
    a fold frequency."""
    books = record_hands(hands)
    for player_key in books:
        found = find(hands, player_key, "vpip")
        if len(found) > 3 and any(e.hit for e in found):
            assert any(not e.hit for e in found), "denominator hands must be shown too"
            return


def test_evidence_is_newest_first(hands):
    books = record_hands(hands)
    player_key = max(books, key=lambda k: sum(b.hands for b in books[k].values()))
    found = find(hands, player_key, "vpip")
    assert found == sorted(found, key=lambda e: -e.started_at)


def test_evidence_carries_a_readable_summary(hands):
    books = record_hands(hands)
    player_key = max(books, key=lambda k: sum(b.hands for b in books[k].values()))
    for e in find(hands, player_key, "fold_vs_bet:flop"):
        assert e.summary and "bb" in e.summary or "checked" in e.summary
        assert e.hand_id


def test_player_hands_are_rekeyed_to_internal_ids(stored, hands):
    player = max(stored.players(), key=lambda r: r["hands"] or 0)
    pid = int(player["id"])
    got = stored.player_hands(pid)
    assert got
    for hand in got:
        assert any(s.player_id == str(pid) for s in hand.seats)


def test_evidence_works_end_to_end_through_the_store(stored):
    """The path the UI actually takes: player id -> hands -> evidence."""
    player = max(stored.players(), key=lambda r: r["hands"] or 0)
    pid = int(player["id"])
    hands = stored.player_hands(pid)
    profile = build_unified(stored.books(pid))
    stat = next(s for s in ("vpip", "pfr") if profile.opps(s) > 0)
    found = find(hands, str(pid), stat)
    assert found, f"no evidence for {stat} despite {profile.opps(stat)} opportunities"


# -- replay -----------------------------------------------------------------

def test_replay_covers_every_street_that_happened(hands):
    for hand in hands:
        r = replay(hand)
        names = [s["name"] for s in r["streets"]]
        assert names[0] == "preflop"
        assert len(names) == len(set(names))
        if hand.board:
            assert "flop" in names


def test_replay_action_count_matches_the_hand(hands):
    for hand in hands:
        r = replay(hand)
        assert sum(len(s["actions"]) for s in r["streets"]) == len(hand.actions)


def test_replay_marks_the_focus_player(hands):
    hand = next(h for h in hands if len(h.actions) > 4)
    key = hand.seats[0].player_id
    r = replay(hand, focus=key)
    assert any(s["focus"] for s in r["seats"])
    focused = [a for st in r["streets"] for a in st["actions"] if a["focus"]]
    assert focused, "the focus player's actions must be marked"
    assert all(a["seat"] == hand.seats[0].seat for a in focused)


def test_replay_deals_each_card_once(hands):
    for hand in hands:
        r = replay(hand)
        dealt = [c for s in r["streets"] for c in s["new_cards"]]
        assert dealt == hand.board
        assert len(dealt) == len(set(dealt))


def test_replay_is_serialisable(hands):
    import json
    for hand in hands[:5]:
        json.dumps(replay(hand))

