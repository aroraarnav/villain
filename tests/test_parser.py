"""The parser is the foundation: if it mis-decodes an opcode every statistic
downstream is quietly wrong, so these tests check the money, not just the shape."""

import pytest

from villain.model import Act, Street, hand_from_dict, hand_to_dict, positions_for


def test_every_hand_balances(hands):
    """Chips in must equal chips out. This is what proves the opcodes are right."""
    for hand in hands:
        invested = sum(s.invested for s in hand.seats)
        won = sum(s.won for s in hand.seats)
        assert invested == won, f"hand {hand.hand_id} does not balance"


def test_no_unknown_opcodes(hands):
    for hand in hands:
        assert not [f for f in hand.flags if f.startswith("unknown_event")]


def test_blinds_and_positions(hands):
    for hand in hands:
        posts = [a for a in hand.actions if a.act in (Act.POST_SB, Act.POST_BB)]
        assert len(posts) >= 2
        sb = next(a for a in posts if a.act is Act.POST_SB)
        assert sb.amount == hand.small_blind


def test_heads_up_button_posts_the_small_blind(hands):
    for hand in hands:
        if len(hand.seats) != 2:
            continue
        sb = next(a for a in hand.actions if a.act is Act.POST_SB)
        assert hand.seat(sb.seat).position == "BTN"


def test_amounts_are_increments_and_totals(hands):
    """``to_amount`` is cumulative for the street, ``amount`` is what was added."""
    for hand in hands:
        running: dict[tuple[int, int], int] = {}
        for action in hand.actions:
            key = (int(action.street), action.seat)
            before = running.get(key, 0)
            assert action.to_amount == before + action.amount
            running[key] = action.to_amount


def test_raise_versus_bet_classification(hands):
    """Preflop opens are raises (blinds are already wagered); flop leads are bets."""
    for hand in hands:
        for action in hand.actions:
            if action.act is Act.BET:
                assert action.street is not Street.PREFLOP
            if action.act is Act.BET:
                assert action.to_call == 0


def test_single_card_reveals_are_kept_out_of_hole_cards(hands):
    partial = [s for h in hands for s in h.seats if len(s.revealed) == 1]
    assert partial, "fixture should contain a one-card reveal"
    for seat in partial:
        assert seat.hole_cards == () or len(seat.hole_cards) == 2
        assert all(c is not None for c in seat.revealed)


def test_board_cards_are_well_formed(hands):
    for hand in hands:
        assert len(hand.board) in (0, 3, 4, 5)
        for card in hand.board:
            assert len(card) == 2


def test_serialisation_roundtrip(hands):
    for hand in hands:
        assert hand_to_dict(hand_from_dict(hand_to_dict(hand))) == hand_to_dict(hand)


@pytest.mark.parametrize("seats,dealer,expected", [
    ([1, 2], 1, {1: "BTN", 2: "BB"}),
    ([1, 2, 3], 1, {1: "BTN", 2: "SB", 3: "BB"}),
    ([1, 2, 3, 4, 5, 6], 6, {6: "BTN", 1: "SB", 2: "BB", 3: "UTG", 4: "HJ", 5: "CO"}),
])
def test_position_assignment(seats, dealer, expected):
    assert positions_for(seats, dealer) == expected


def test_dead_button_falls_forward():
    """An empty dealer seat moves the button forward, keeping positions contiguous.

    Real rooms leave the button on the dead seat and kill the small blind; this
    approximates it by advancing to the next occupied seat, which keeps every
    positional statistic well defined.
    """
    assert positions_for([2, 4, 6], 3) == {4: "BTN", 6: "SB", 2: "BB"}


def _minimal_hand(**overrides):
    """A tiny PokerNow hand dict for parser edge cases."""
    base = {
        "id": "h-test",
        "startedAt": 1,
        "bigBlind": 20,
        "smallBlind": 10,
        "ante": 0,
        "gameType": "th",
        "dealerSeat": 1,
        "players": [
            {"seat": 1, "id": "a", "name": "A", "stack": 1000},
            {"seat": 2, "id": "b", "name": "B", "stack": 1000},
        ],
        "events": [
            {"at": 1, "payload": {"type": 3, "seat": 1, "value": 10}},
            {"at": 2, "payload": {"type": 2, "seat": 2, "value": 20}},
            {"at": 3, "payload": {"type": 11, "seat": 1}},
            {"at": 4, "payload": {"type": 10, "seat": 2, "value": 30}},
            {"at": 5, "payload": {"type": 15}},
        ],
    }
    base.update(overrides)
    return base


def test_opcode_14_is_ignored_not_unknown():
    from villain.parsers.pokernow import _parse_hand
    raw = _minimal_hand()
    raw["events"].insert(2, {"at": 2, "payload": {"type": 14, "seat": 1}})
    hand = _parse_hand(raw, "table")
    assert hand is not None
    assert "unknown_event:14" not in hand.flags


def test_award_to_unseated_player_does_not_crash():
    from villain.parsers.pokernow import _parse_hand
    raw = _minimal_hand()
    raw["events"].append({"at": 6, "payload": {"type": 10, "seat": 99, "value": 5,
                                                "cards": ["As", "Kd"]}})
    hand = _parse_hand(raw, "table")
    assert hand is not None
    assert 99 not in {s.seat for s in hand.seats}


def test_ante_seeds_committed_so_hand_balances():
    from villain.parsers.pokernow import _parse_hand
    raw = _minimal_hand(ante=2)
    # Award must cover blinds + antes.
    raw["events"] = [
        {"at": 1, "payload": {"type": 3, "seat": 1, "value": 10}},
        {"at": 2, "payload": {"type": 2, "seat": 2, "value": 20}},
        {"at": 3, "payload": {"type": 11, "seat": 1}},
        {"at": 4, "payload": {"type": 10, "seat": 2, "value": 34}},
        {"at": 5, "payload": {"type": 15}},
    ]
    hand = _parse_hand(raw, "table")
    assert hand.ante == 2
    assert "pot_mismatch" not in hand.flags
    assert sum(s.invested for s in hand.seats) == sum(s.won for s in hand.seats)


def test_straddle_flag_is_recorded():
    from villain.parsers.pokernow import _parse_hand
    hand = _parse_hand(_minimal_hand(straddleSeat=2), "table")
    assert "straddle" in hand.flags


def _straddled_hand():
    """SB, BB, then a straddle from the third seat at twice the big blind.

    The shape every straddled hand in a real export has: opcode 6, the seat
    after the big blind, exactly 2x it.
    """
    raw = _minimal_hand(straddleSeat=3)
    raw["players"].append({"seat": 3, "id": "c", "name": "C", "stack": 1000})
    raw["events"] = [
        {"at": 1, "payload": {"type": 3, "seat": 1, "value": 10}},   # small blind
        {"at": 2, "payload": {"type": 2, "seat": 2, "value": 20}},   # big blind
        {"at": 3, "payload": {"type": 6, "seat": 3, "value": 40}},   # the straddle
        {"at": 4, "payload": {"type": 11, "seat": 1}},               # SB folds
        {"at": 5, "payload": {"type": 11, "seat": 2}},               # BB folds
        {"at": 6, "payload": {"type": 10, "seat": 3, "value": 70}},  # straddler takes it
        {"at": 7, "payload": {"type": 15}},
    ]
    return raw


def test_a_straddle_is_read_as_chips_not_just_a_flag():
    """The bug this covers dropped 4.64% of a real database.

    `straddleSeat` in the metadata set a flag, but the event carrying the money
    was unrecognised and skipped -- so the pot came up short by the straddle
    while the award did not. Less went in than came out, which cannot happen at
    a real table, so the hand was decoded as untrustworthy and thrown away.
    """
    from villain.parsers.pokernow import _parse_hand
    hand = _parse_hand(_straddled_hand(), "table")
    assert hand is not None
    assert "unknown_event:6" not in hand.flags
    assert "pot_mismatch" not in hand.flags, "the straddle's chips never reached the pot"
    # 10 + 20 + 40 in, 70 out.
    assert hand.pot == 70


def test_a_straddle_is_a_post_not_a_voluntary_raise():
    """Money in before cards. Counting it as a raise would make every
    straddling player look like the most aggressive person at the table."""
    from villain.model import Act
    from villain.parsers.pokernow import _parse_hand
    hand = _parse_hand(_straddled_hand(), "table")
    posts = [a for a in hand.actions if a.act is Act.POST_STRADDLE]
    assert len(posts) == 1
    assert posts[0].seat == 3
    assert posts[0].amount == 40
    assert posts[0].act.is_post
    assert not posts[0].is_voluntary
