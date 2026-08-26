"""Sittings: derived from time gaps between hands, not stored separately."""

import pytest

from villain.db import Store


@pytest.fixture
def store(tmp_path, hands):
    with Store(tmp_path / "v.db") as s:
        s.add_hands(hands)
        yield s


def test_sessions_account_for_every_hand(store, hands):
    sessions = store.sessions()
    assert sessions
    assert sum(s["hands"] for s in sessions) == len(hands)


def test_session_detail_net_bb_matches_the_sum_of_seat_net(store, hands):
    """net_bb is a plain sum of seat.net/bb across the sitting's hands --
    check it against the same arithmetic done independently from the raw
    hands, not from the book machinery that produces it."""
    sessions = store.sessions()
    session = sessions[0]
    detail = store.session_detail(session)
    assert detail

    by_hand_id = {h.hand_id: h for h in hands}
    alias_map = {(r["site"], r["account"]): int(r["player_id"])
                for r in store.conn.execute("SELECT site, account, player_id FROM aliases")}

    for row in detail:
        expected = 0.0
        for hid in session["hand_ids"]:
            hand = by_hand_id.get(hid)
            if hand is None:
                continue
            for seat in hand.seats:
                pid = alias_map.get((hand.site, seat.player_id))
                if pid == row["player_id"]:
                    expected += seat.net / hand.big_blind
        assert row["net_bb"] == pytest.approx(expected, abs=0.05)


def test_session_detail_net_bb_present_for_every_player(store):
    sessions = store.sessions()
    detail = store.session_detail(sessions[0])
    for row in detail:
        assert isinstance(row["net_bb"], float)


def test_session_trends_skip_derived_aggression():
    """aggression:* is assembled in build_profile and is not in book.ratios,
    so sitting trends for it never fired. Dead stats, not a wrong number."""
    from villain.db import Store
    assert "aggression:flop" not in Store.SESSION_STATS
    assert "aggression:turn" not in Store.SESSION_STATS
    assert Store.SESSION_MIN_OPPS >= 40


def test_a_sitting_survives_a_deleted_player(store):
    """Deleting somebody leaves their seats resolving to nobody.

    The hands stay -- they are the source of truth for everyone else at that
    table -- so the sitting still has to render. It did not: the unresolved
    seat kept its raw site account as its book key and ``int()`` on that took
    the whole route down with a 500, which is every sitting the deleted player
    ever sat in."""
    sessions = store.sessions()
    before = {row["player_id"] for row in store.session_detail(sessions[0])}
    victim = sorted(before)[0]

    store.delete_player(victim)

    after = store.session_detail(sessions[0])
    assert {row["player_id"] for row in after} == before - {victim}
    assert all(isinstance(row["player_id"], int) for row in after)
    # And every other sitting they sat in, not just the first.
    for session in store.sessions():
        store.session_detail(session)
