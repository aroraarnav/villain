"""The Hold'em engine: blinds, action order, side pots, chip conservation."""

import numpy as np
import pytest
from helpers import seats as _seats

from villain.cards import card_id
from villain.holdem import Hand, Seat


def _ids(text):
    return tuple(int(card_id(c)) for c in text.split())


def test_heads_up_blinds_and_first_to_act():
    h = Hand(_seats(100, 100), button=0, sb=1, bb=2, rng=np.random.default_rng(1))
    assert h.seats[0].street_put == 1        # button posts the small blind
    assert h.seats[1].street_put == 2
    assert h.pot == 3
    assert h.to_act == 0                      # ...and acts first preflop


def test_three_handed_utg_acts_first():
    h = Hand(_seats(100, 100, 100), button=0, sb=1, bb=2, rng=np.random.default_rng(1))
    assert h.seats[1].street_put == 1         # SB
    assert h.seats[2].street_put == 2         # BB
    assert h.to_act == 0                       # UTG (button, 3-handed) is first


def test_fold_hands_pot_to_the_other_player():
    h = Hand(_seats(100, 100), button=0, sb=1, bb=2, rng=np.random.default_rng(2))
    h.act("fold")                              # button/SB folds preflop
    assert h.over
    # The big blind's unmatched chip comes back; only the called part is won.
    assert h.winners == {1: 2}
    assert "Uncalled 1 returned to" in " ".join(h.log)
    assert h.seats[1].stack == 101 and h.seats[0].stack == 99


def test_bb_gets_the_option_and_can_check_it_closed():
    h = Hand(_seats(100, 100, 100), button=0, sb=1, bb=2, rng=np.random.default_rng(3))
    h.act("call")                              # UTG calls
    h.act("call")                              # SB completes
    assert h.to_act == 2                        # BB has the option
    assert h.legal().can_check
    h.act("check")                             # closes preflop
    assert h.street == 1 and len(h.board) == 3


def test_min_raise_is_enforced():
    h = Hand(_seats(100, 100), button=0, sb=1, bb=2, rng=np.random.default_rng(4))
    lg = h.legal()
    assert lg.min_raise_to == 4                # bet 2 + full raise 2
    with pytest.raises(ValueError):
        h.act("raise", 3)                      # below the minimum


def test_chips_are_conserved_through_a_full_hand():
    h = Hand(_seats(100, 100, 100), button=0, sb=1, bb=2, rng=np.random.default_rng(7))
    total = sum(s.stack for s in h.seats) + h.pot   # stacks + blinds already posted
    guard = 0
    while not h.over:
        lg = h.legal()
        h.act("check" if lg.can_check else "call")   # a table of calling stations
        guard += 1
        assert guard < 100
    assert sum(s.stack for s in h.seats) == total


def test_side_pot_a_short_all_in_cannot_win_the_side_pot():
    h = Hand(_seats(0, 0, 0), button=0, sb=1, bb=2, rng=np.random.default_rng(0))
    h.board = list(_ids("Ah Kd Qc 2s 7h"))
    h.seats[0].hole = _ids("Ac Ad")            # trip aces -- strongest
    h.seats[1].hole = _ids("Kc Ks")            # trip kings
    h.seats[2].hole = _ids("7c 8c")            # a pair of sevens
    for s in h.seats:
        s.folded = False
        s.street_put = 0
    h.seats[0].hand_put = 10                    # short all-in
    h.seats[1].hand_put = 50
    h.seats[2].hand_put = 50
    h.winners = None
    h._settle()
    # Main pot 30 (10x3) to A; side pot 80 (40x2, B and C only) to B.
    assert h.winners == {0: 30, 1: 80}
    assert sum(h.winners.values()) == 110      # everything contributed is paid out


def test_split_pot_is_shared():
    h = Hand(_seats(0, 0), button=0, sb=1, bb=2, rng=np.random.default_rng(0))
    h.board = list(_ids("Ah Kd Qc Js Th"))     # a broadway straight on the board
    h.seats[0].hole = _ids("2c 3d")
    h.seats[1].hole = _ids("4c 5d")            # both play the board -> chop
    for s in h.seats:
        s.folded = False
        s.street_put = 0
        s.hand_put = 20
    h.winners = None
    h._settle()
    assert h.winners == {0: 20, 1: 20}


def test_pot_does_not_reset_between_streets():
    # Heads-up: 10 in each preflop, then 10 in each on the flop. The pot must
    # be 20 after preflop and 40 after the flop -- never reset to the street's
    # bets alone.
    h = Hand(_seats(200, 200), button=0, sb=1, bb=2, rng=np.random.default_rng(5))
    h.act("raise", 10)          # button raises to 10
    h.act("call")               # BB calls -> round closes to the flop
    assert h.street == 1 and h.pot == 20
    h.act("raise", 10)          # flop bet of 10
    h.act("call")               # called -> pot must carry the preflop 20
    assert h.pot == 40


def test_initiative_survives_a_checked_street():
    """A flop check-through used to wipe the lead, so delayed c-bets never fired."""
    h = Hand(_seats(200, 200), button=0, sb=1, bb=2, rng=np.random.default_rng(5))
    h.act("raise", 6)
    h.act("call")
    assert h.street == 1
    assert h.initiative == 0                    # preflop raiser carries the lead
    h.act("check")                              # BB checks
    h.act("check")                              # BTN checks back
    assert h.street == 2
    assert h.initiative == 0                    # still theirs
    assert 0 in h.declined_initiative


def test_betting_again_takes_the_lead_back():
    """Check the flop, bet the turn: the next street is a barrel, not a stab.

    ``declined_initiative`` is what the policy reads to tell a delayed c-bet
    from a second barrel. It never cleared, so once a seat checked a flop with
    the lead every later street they bet stayed 'delayed' for the rest of the
    hand -- against a stat whose denominator had by then moved on."""
    h = Hand(_seats(200, 200), button=0, sb=1, bb=2, rng=np.random.default_rng(11))
    h.act("raise", 6)
    h.act("call")
    assert h.street == 1
    h.act("check")                              # BB checks
    h.act("check")                              # BTN checks back with the lead
    assert 0 in h.declined_initiative
    assert h.street == 2
    h.act("check")                              # BB checks
    h.act("raise", 8)                           # BTN bets the turn
    assert 0 not in h.declined_initiative


def test_a_short_all_in_does_not_reopen_raising():
    """An all-in shorter than a full raise: players already square may call
    the extra or fold, not raise. Players who have not yet acted still can.
    The engine advertised the first half and then left can_raise True."""
    h = Hand(_seats(200, 14, 200), button=0, sb=1, bb=2, rng=np.random.default_rng(6))
    # 3-handed: 0 UTG/BTN, 1 SB short, 2 BB.
    h.act("raise", 10)                          # UTG opens to 10 (full)
    h.act("raise", 14)                          # SB all-in for 14; +4 < min_raise 8
    assert h.seats[1].all_in
    assert h.to_act == 2                        # BB has not acted yet
    assert h.legal().can_raise                  # ...and so may still raise
    h.act("call")
    assert h.to_act == 0                        # UTG already square with the 10
    assert h.legal().can_call
    assert not h.legal().can_raise              # incomplete raise does not re-open


def test_pot_kind_and_opener_survive_the_flop():
    h = Hand(_seats(200, 200), button=0, sb=1, bb=2, rng=np.random.default_rng(8))
    h.act("raise", 6)
    h.act("call")
    assert h.street == 1
    assert h.pot_kind == "srp"
    assert h.opener == 0
    assert 1 in h.called_prev


def test_a_three_bet_pot_is_tagged_3bp():
    h = Hand(_seats(200, 200), button=0, sb=1, bb=2, rng=np.random.default_rng(9))
    h.act("raise", 6)
    h.act("raise", 18)
    h.act("call")
    assert h.street == 1
    assert h.pot_kind == "3bp"
    assert h.opener == 0


def test_opening_an_unbet_street_logs_as_a_bet():
    h = Hand(_seats(200, 200), button=0, sb=1, bb=2, rng=np.random.default_rng(10))
    h.act("call")
    h.act("check")
    assert h.street == 1
    h.act("raise", 6)
    assert h.log[-1].endswith("bets 6")
    h.act("raise", 18)
    assert h.log[-1].endswith("raises to 18")


def test_the_hero_log_is_first_person():
    """The subject is You; 'You calls' is not English."""
    h = Hand([Seat("You", 200), Seat("Arav", 200)], button=0, sb=1, bb=2,
             rng=np.random.default_rng(11))
    h.hero_seat = 0
    assert h.log[0] == "You post 1"
    assert h.log[1] == "Arav posts 2"
    h.act("call")
    assert h.log[-1] == "You call"
    h.act("check")
    assert h.street == 1
    h.act("check")                                 # Arav, third person
    assert h.log[-1] == "Arav checks"
    h.act("raise", 6)
    assert h.log[-1] == "You bet 6"
    h.act("fold")
    assert "You win " in h.log[-1]




def test_a_covering_stack_that_loses_is_not_a_winner():
    """The uncalled excess was paid out as a win: a 400 stack that shoved into
    a 60 stack and lost was logged "wins 340" and marked a winner."""
    for seed in range(200):
        h = Hand(_seats(400, 60), button=0, sb=1, bb=2, rng=np.random.default_rng(seed))
        h.act("raise", 400)
        h.act("call")
        if h.seats[0].stack < 400:                 # the big stack lost
            break
    assert h.over
    assert 0 not in h.winners
    assert h.seats[0].stack == 340
    assert "Uncalled 340 returned to" in " ".join(h.log)


def test_nothing_can_be_played_after_the_hand_is_over():
    """_settle left to_act set, so a late act paid the pot out a second time."""
    h = Hand(_seats(100, 100), button=0, sb=1, bb=2, rng=np.random.default_rng(398))
    h.act("fold")
    with pytest.raises(RuntimeError):
        h.act("call")
    assert sum(s.stack for s in h.seats) == 200


def test_call_with_nothing_owed_and_a_free_fold_are_refused():
    h = Hand(_seats(100, 100), button=0, sb=1, bb=2, rng=np.random.default_rng(1))
    h.act("call")                                  # SB completes
    with pytest.raises(ValueError):
        h.act("call")                              # BB owes nothing
    with pytest.raises(ValueError):
        h.act("fold")
    assert not h.limped - {0}


def test_blinds_that_put_everyone_all_in_run_the_board_out():
    """Nobody can act after the blinds; the hand used to sit there, neither
    over nor anyone's turn, and the sim step crashed on it."""
    h = Hand(_seats(2, 2), button=0, sb=2, bb=2, rng=np.random.default_rng(4))
    assert h.over and len(h.board) == 5
    assert sum(s.stack for s in h.seats) == 4


def test_short_all_ins_that_add_up_to_a_raise_reopen_the_action():
    """A opens 100, B shoves 148, C shoves 208: neither is a full raise on its
    own, but A now faces 108 more -- a full raise over what A last matched."""
    h = Hand(_seats(148, 208, 1000, 1000), button=0, sb=1, bb=2,
             rng=np.random.default_rng(5))
    assert h.to_act == 3
    h.act("raise", 100)                            # A (UTG), a full raise of 98
    h.act("raise", 148)                            # B, all in, +48: incomplete
    h.act("raise", 208)                            # C, all in, +60: incomplete
    h.act("call")                                  # BB calls 208
    assert h.to_act == 3
    assert h.legal().can_raise


def test_one_short_all_in_does_not_reopen_the_action():
    h = Hand(_seats(148, 1000, 1000, 1000), button=0, sb=1, bb=2,
             rng=np.random.default_rng(5))
    h.act("raise", 100)
    h.act("raise", 148)                            # +48 only
    h.act("call")
    h.act("call")
    assert h.to_act == 3
    assert not h.legal().can_raise


def test_odd_chip_goes_left_of_the_button():
    """Split pots gave the odd chip to the lowest seat index."""
    h = Hand(_seats(100, 100, 100), button=1, sb=1, bb=3, rng=np.random.default_rng(0))
    for i, hole in ((0, ("2c", "3d")), (1, ("4h", "5h")), (2, ("2d", "3c"))):
        h.seats[i].hole = tuple(card_id(c) for c in hole)
    # Popped from the end: flop, turn, river -- a broadway straight for all.
    h._deck = [card_id(c) for c in ("Th", "Jc", "Qd", "Ks", "As")]
    h.act("call")                                  # seat 1 (UTG) calls 3
    h.act("call")                                  # seat 2 (SB) completes
    h.act("check")                                 # seat 0 (BB)
    h.act("raise", 3)                              # seat 2 bets the flop
    h.act("call")                                  # seat 0
    h.act("fold")                                  # seat 1: 15 in the pot
    while not h.over:
        h.act("check")
    assert h.winners == {2: 8, 0: 7}
