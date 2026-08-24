"""Statistic definitions, checked against a hand whose answers are known by hand."""

import pytest

from villain.features import record_hand, record_hands
from villain.model import Act, Action, Hand, Seat, Street
from villain.priors import regime
from villain.stats import HandView, Meter, Ratio, StatBook, size_bucket


def build_hand(actions, *, board=None, seats=None, bb=10):
    hand = Hand(hand_id="t1", site="test", table_id="t", started_at=0,
                big_blind=bb, small_blind=bb // 2)
    hand.seats = seats or [
        Seat(seat=1, player_id="a", name="A", stack=100 * bb, position="BTN"),
        Seat(seat=2, player_id="b", name="B", stack=100 * bb, position="BB"),
    ]
    hand.board = board or []
    hand.actions = actions
    return hand


def act(street, seat, kind, amount=0, to_amount=0, pot_before=0, to_call=0,
        think_ms=None):
    return Action(street=street, seat=seat, act=kind, amount=amount,
                  to_amount=to_amount, pot_before=pot_before, to_call=to_call,
                  think_ms=think_ms)


def test_ratio_and_meter_merge_is_additive():
    a, b = Ratio(3, 10), Ratio(2, 5)
    a.merge(b)
    assert (a.hits, a.opps, a.rate) == (5, 15, pytest.approx(1 / 3))
    m, n = Meter(), Meter()
    for v in (1.0, 2.0, 3.0):
        m.add(v)
    for v in (4.0, 5.0):
        n.add(v)
    m.merge(n)
    assert m.n == 5 and m.mean == pytest.approx(3.0)


def test_statbook_merge_pools_two_sessions():
    a = StatBook(player_id="x", regime="hu", hands=10)
    a.count("vpip", True)
    b = StatBook(player_id="x", regime="hu", hands=5)
    b.count("vpip", False)
    a.merge(b)
    assert a.hands == 15
    assert a.ratios["vpip"].hits == 1 and a.ratios["vpip"].opps == 2


@pytest.mark.parametrize("fraction,expected", [
    (0.25, "small"), (0.5, "mid"), (0.75, "big"), (1.5, "over")])
def test_size_buckets(fraction, expected):
    assert size_bucket(fraction) == expected


@pytest.mark.parametrize("bb,expected", [
    (12, "short"), (24.9, "short"), (25, "mid"), (69, "mid"), (70, "deep"), (200, "deep")])
def test_stack_buckets(bb, expected):
    from villain.stats import stack_bucket
    assert stack_bucket(bb) == expected


def test_vpip_and_pfr_on_a_known_hand():
    """BTN raises, BB folds: BTN has VPIP and PFR, BB has neither."""
    hand = build_hand([
        act(Street.PREFLOP, 1, Act.POST_SB, 5, 5),
        act(Street.PREFLOP, 2, Act.POST_BB, 10, 10, pot_before=5),
        act(Street.PREFLOP, 1, Act.RAISE, 25, 30, pot_before=15, to_call=5),
        act(Street.PREFLOP, 2, Act.FOLD, pot_before=40, to_call=20),
    ])
    books = {}
    record_hand(hand, books)
    btn = books["a"]["hu"]
    bb = books["b"]["hu"]
    assert btn.rate("vpip") == 1.0 and btn.rate("pfr") == 1.0
    assert btn.rate("rfi:BTN") == 1.0
    assert btn.rate("rfi:BTN:deep") == 1.0
    assert bb.rate("vpip") == 0.0
    assert bb.rate("fold_to_steal") == 1.0


def test_cbet_and_fold_to_cbet_denominators():
    """The preflop raiser c-bets; the caller folding counts as fold-to-cbet."""
    hand = build_hand([
        act(Street.PREFLOP, 1, Act.POST_SB, 5, 5),
        act(Street.PREFLOP, 2, Act.POST_BB, 10, 10, pot_before=5),
        act(Street.PREFLOP, 1, Act.RAISE, 25, 30, pot_before=15, to_call=5),
        act(Street.PREFLOP, 2, Act.CALL, 20, 30, pot_before=40, to_call=20),
        act(Street.FLOP, 2, Act.CHECK, pot_before=60),
        act(Street.FLOP, 1, Act.BET, 30, 30, pot_before=60),
        act(Street.FLOP, 2, Act.FOLD, pot_before=90, to_call=30),
    ], board=["2c", "7d", "9h"])
    books = {}
    record_hand(hand, books)
    raiser, caller = books["a"]["hu"], books["b"]["hu"]
    assert raiser.rate("cbet:flop") == 1.0
    assert raiser.rate("cbet:flop:srp") == 1.0
    assert raiser.rate("cbet:flop:deep") == 1.0
    assert caller.rate("fold_to_cbet:flop") == 1.0
    assert caller.rate("fold_vs_bet:flop") == 1.0
    # A half-pot bet lands in the "mid" bucket, not "small".
    assert caller.rate("fold_vs_bet:flop:mid") == 1.0
    assert caller.rate("fold_vs_bet:flop:stk:deep") == 1.0


def test_retaking_the_lead_ends_the_delayed_cbet_run():
    """Check the flop, bet the turn, bet the river: the river is a barrel.

    ``delayed_cbet`` is "raised preflop, checked the flop, stabbing later" --
    they gave the lead up. Once they bet the turn they have it back, so the
    river is what ``cbet:river`` counts ("having bet the turn, how often they
    fire the river as well"). The flag never cleared, so every later street
    stayed delayed and both cbet:turn and cbet:river lost the opportunity --
    while the other seat was already being booked fold_to_cbet:river against
    the very same bet.
    """
    hand = build_hand([
        act(Street.PREFLOP, 1, Act.POST_SB, 5, 5),
        act(Street.PREFLOP, 2, Act.POST_BB, 10, 10, pot_before=5),
        act(Street.PREFLOP, 1, Act.RAISE, 25, 30, pot_before=15, to_call=5),
        act(Street.PREFLOP, 2, Act.CALL, 20, 30, pot_before=40, to_call=20),
        act(Street.FLOP, 2, Act.CHECK, pot_before=60),
        act(Street.FLOP, 1, Act.CHECK, pot_before=60),
        act(Street.TURN, 2, Act.CHECK, pot_before=60),
        act(Street.TURN, 1, Act.BET, 40, 40, pot_before=60),
        act(Street.TURN, 2, Act.CALL, 40, 40, pot_before=100, to_call=40),
        act(Street.RIVER, 2, Act.CHECK, pot_before=140),
        act(Street.RIVER, 1, Act.BET, 100, 100, pot_before=140),
        act(Street.RIVER, 2, Act.FOLD, pot_before=240, to_call=100),
    ], board=["2c", "7d", "9h", "Ks", "3s"])
    books = {}
    record_hand(hand, books)
    raiser, caller = books["a"]["hu"], books["b"]["hu"]
    assert raiser.rate("cbet:flop") == 0.0            # checked it: gave the lead up
    assert raiser.rate("delayed_cbet:turn") == 1.0    # took it back here
    assert raiser.rate("cbet:river") == 1.0           # so this one is a barrel
    assert raiser.opps("delayed_cbet:river") == 0
    # The two halves of one decision have to agree about what it was.
    assert caller.rate("fold_to_cbet:river") == 1.0


def test_a_second_check_with_the_lead_is_still_a_delayed_cbet():
    """The counterpart: never bet, so every later street stays delayed."""
    hand = build_hand([
        act(Street.PREFLOP, 1, Act.POST_SB, 5, 5),
        act(Street.PREFLOP, 2, Act.POST_BB, 10, 10, pot_before=5),
        act(Street.PREFLOP, 1, Act.RAISE, 25, 30, pot_before=15, to_call=5),
        act(Street.PREFLOP, 2, Act.CALL, 20, 30, pot_before=40, to_call=20),
        act(Street.FLOP, 2, Act.CHECK, pot_before=60),
        act(Street.FLOP, 1, Act.CHECK, pot_before=60),
        act(Street.TURN, 2, Act.CHECK, pot_before=60),
        act(Street.TURN, 1, Act.CHECK, pot_before=60),
        act(Street.RIVER, 2, Act.CHECK, pot_before=60),
        act(Street.RIVER, 1, Act.BET, 40, 40, pot_before=60),
    ], board=["2c", "7d", "9h", "Ks", "3s"])
    books = {}
    record_hand(hand, books)
    raiser = books["a"]["hu"]
    assert raiser.rate("delayed_cbet:turn") == 0.0
    assert raiser.rate("delayed_cbet:river") == 1.0
    assert raiser.opps("cbet:river") == 0


def test_fold_vs_raise_is_facing_a_raise_not_a_bet():
    """The original bettor folding to a raise is fold_vs_raise, not fold_vs_bet."""
    hand = build_hand([
        act(Street.PREFLOP, 1, Act.POST_SB, 5, 5),
        act(Street.PREFLOP, 2, Act.POST_BB, 10, 10, pot_before=5),
        act(Street.PREFLOP, 1, Act.RAISE, 25, 30, pot_before=15, to_call=5),
        act(Street.PREFLOP, 2, Act.CALL, 20, 30, pot_before=40, to_call=20),
        act(Street.FLOP, 2, Act.CHECK, pot_before=60),
        act(Street.FLOP, 1, Act.BET, 30, 30, pot_before=60),
        act(Street.FLOP, 2, Act.RAISE, 90, 90, pot_before=90, to_call=30),
        act(Street.FLOP, 1, Act.FOLD, pot_before=180, to_call=60),
    ], board=["2c", "7d", "9h"])
    books = {}
    record_hand(hand, books)
    raiser = books["a"]["hu"]
    assert raiser.rate("fold_vs_raise:flop") == 1.0
    assert books["b"]["hu"].opps("fold_vs_raise:flop") == 0


def test_check_raise_requires_a_check_first():
    hand = build_hand([
        act(Street.PREFLOP, 1, Act.POST_SB, 5, 5),
        act(Street.PREFLOP, 2, Act.POST_BB, 10, 10, pot_before=5),
        act(Street.PREFLOP, 1, Act.CALL, 5, 10, pot_before=15, to_call=5),
        act(Street.PREFLOP, 2, Act.CHECK, to_amount=10, pot_before=20),
        act(Street.FLOP, 2, Act.CHECK, pot_before=20),
        act(Street.FLOP, 1, Act.BET, 10, 10, pot_before=20),
        act(Street.FLOP, 2, Act.RAISE, 30, 30, pot_before=30, to_call=10),
    ], board=["2c", "7d", "9h"])
    books = {}
    record_hand(hand, books)
    assert books["b"]["hu"].rate("check_raise:flop") == 1.0
    assert books["a"]["hu"].opps("check_raise:flop") == 0


def test_three_bet_denominator_is_facing_a_raise():
    hand = build_hand([
        act(Street.PREFLOP, 1, Act.POST_SB, 5, 5),
        act(Street.PREFLOP, 2, Act.POST_BB, 10, 10, pot_before=5),
        act(Street.PREFLOP, 1, Act.RAISE, 25, 30, pot_before=15, to_call=5),
        act(Street.PREFLOP, 2, Act.RAISE, 80, 90, pot_before=40, to_call=20),
        act(Street.PREFLOP, 1, Act.FOLD, pot_before=130, to_call=60),
    ])
    books = {}
    record_hand(hand, books)
    assert books["b"]["hu"].rate("three_bet") == 1.0
    assert books["b"]["hu"].rate("three_bet:BB") == 1.0
    assert books["b"]["hu"].rate("three_bet:BB:vs:BTN") == 1.0
    assert books["b"]["hu"].rate("three_bet:deep") == 1.0
    assert books["a"]["hu"].rate("fold_to_three_bet") == 1.0
    assert books["a"]["hu"].opps("three_bet") == 0


def test_stats_are_bucketed_by_table_size(hands):
    """The same player three-handed and heads-up keeps two separate books."""
    books = record_hands(hands)
    multi_regime = [pid for pid, by in books.items() if len(by) > 1]
    assert multi_regime, "fixture should contain a player at two table sizes"
    for pid in multi_regime:
        for reg, book in books[pid].items():
            assert book.regime == reg
            assert regime(book.mean("table_size")) == reg


def test_hand_view_tracks_who_saw_each_street(hands):
    for hand in hands:
        view = HandView(hand)
        for seat, street in view.folded_on.items():
            # A player who folded on the flop never saw the turn.
            later = [s for s in Street if s > street]
            for s in later:
                assert seat not in view.saw.get(s, set())


def test_vpip_and_pfr_are_counted_once_per_hand():
    """Regression: a player who limps and then calls a raise put money in once.

    Counting each preflop decision separately inflated both numerator and
    denominator, which made samples look larger than they were and quietly
    raised the confidence attached to every read built on them.
    """
    hand = build_hand([
        act(Street.PREFLOP, 1, Act.POST_SB, 5, 5),
        act(Street.PREFLOP, 2, Act.POST_BB, 10, 10, pot_before=5),
        act(Street.PREFLOP, 1, Act.CALL, 5, 10, pot_before=15, to_call=5),
        act(Street.PREFLOP, 2, Act.RAISE, 30, 40, pot_before=20, to_call=0),
        act(Street.PREFLOP, 1, Act.CALL, 30, 40, pot_before=50, to_call=30),
    ])
    books = {}
    record_hand(hand, books)
    limper = books["a"]["hu"]
    assert limper.opps("vpip") == 1
    assert limper.rate("vpip") == 1.0
    assert limper.opps("pfr") == 1
    assert limper.rate("pfr") == 0.0


def test_a_player_who_never_acts_gets_no_vpip_opportunity():
    """A big blind that everybody folds to never had a decision to make."""
    hand = build_hand([
        act(Street.PREFLOP, 1, Act.POST_SB, 5, 5),
        act(Street.PREFLOP, 2, Act.POST_BB, 10, 10, pot_before=5),
        act(Street.PREFLOP, 1, Act.FOLD, pot_before=15, to_call=5),
    ])
    books = {}
    record_hand(hand, books)
    assert books["b"]["hu"].opps("vpip") == 0


def test_tank_and_snap_split_by_street():
    """A turn tank-fold must not inflate the flop tank rate."""
    from villain.features import SNAP_MS, TANK_MS

    hand = build_hand([
        act(Street.PREFLOP, 1, Act.POST_SB, 5, 5),
        act(Street.PREFLOP, 2, Act.POST_BB, 10, 10, pot_before=5),
        act(Street.PREFLOP, 1, Act.RAISE, 25, 30, pot_before=15, to_call=5,
            think_ms=SNAP_MS - 100),
        act(Street.PREFLOP, 2, Act.CALL, 20, 30, pot_before=40, to_call=20,
            think_ms=SNAP_MS - 100),
        act(Street.FLOP, 2, Act.CHECK, pot_before=60, think_ms=2_000),
        act(Street.FLOP, 1, Act.BET, 40, 40, pot_before=60,
            think_ms=2_000),
        act(Street.FLOP, 2, Act.CALL, 40, 40, pot_before=100, to_call=40,
            think_ms=SNAP_MS - 100),
        act(Street.TURN, 2, Act.CHECK, pot_before=140, think_ms=2_000),
        act(Street.TURN, 1, Act.BET, 100, 100, pot_before=140,
            think_ms=2_000),
        act(Street.TURN, 2, Act.FOLD, pot_before=240, to_call=100,
            think_ms=TANK_MS + 500),
    ], board=["As", "Kd", "7c", "2h"])
    books = {}
    record_hand(hand, books)
    caller = books["b"]["hu"]
    assert caller.rate("tank_fold:turn") == 1.0
    assert caller.opps("tank_fold:turn") == 1
    assert caller.opps("tank_fold:flop") == 0
    assert caller.rate("snap_call:flop") == 1.0
    assert caller.rate("tank_fold") == 1.0
    assert caller.rate("snap_call") == 1.0
    assert caller.rate("pace:snap:flop:call") == 1.0
    assert caller.rate("timed:flop:call") == 1.0
    # Folds are not pace-grid actions; tank_fold still records the pause.
    assert caller.opps("pace:tank:turn:fold") == 0


def test_timing_tells_use_share_and_outcomes_not_folklore():
    """Snap-check share + fold-next vs normal — no 'Giving up' caption."""
    from villain.features import record_hand
    from villain.profile import build_profile
    from villain.timing import timing_tells

    books = {}
    # Snap-check flop, then fold the turn bet → fold_next hits.
    for i in range(10):
        hand = build_hand([
            act(Street.PREFLOP, 1, Act.POST_SB, 5, 5),
            act(Street.PREFLOP, 2, Act.POST_BB, 10, 10, pot_before=5),
            act(Street.PREFLOP, 1, Act.RAISE, 25, 30, pot_before=15, to_call=5,
                think_ms=2_000),
            act(Street.PREFLOP, 2, Act.CALL, 20, 30, pot_before=40, to_call=20,
                think_ms=2_000),
            act(Street.FLOP, 2, Act.CHECK, pot_before=60, think_ms=400),
            act(Street.FLOP, 1, Act.CHECK, pot_before=60, think_ms=2_000),
            act(Street.TURN, 2, Act.CHECK, pot_before=60, think_ms=2_000),
            act(Street.TURN, 1, Act.BET, 40, 40, pot_before=60, think_ms=2_000),
            act(Street.TURN, 2, Act.FOLD, pot_before=100, to_call=40, think_ms=2_000),
        ], board=["As", "Kd", "7c", "2h"])
        hand.hand_id = f"snap{i}"
        record_hand(hand, books)

    # Normal-pace check flop, call the turn → fold_next misses.
    for i in range(10):
        hand = build_hand([
            act(Street.PREFLOP, 1, Act.POST_SB, 5, 5),
            act(Street.PREFLOP, 2, Act.POST_BB, 10, 10, pot_before=5),
            act(Street.PREFLOP, 1, Act.RAISE, 25, 30, pot_before=15, to_call=5,
                think_ms=2_000),
            act(Street.PREFLOP, 2, Act.CALL, 20, 30, pot_before=40, to_call=20,
                think_ms=2_000),
            act(Street.FLOP, 2, Act.CHECK, pot_before=60, think_ms=2_000),
            act(Street.FLOP, 1, Act.CHECK, pot_before=60, think_ms=2_000),
            act(Street.TURN, 2, Act.CHECK, pot_before=60, think_ms=2_000),
            act(Street.TURN, 1, Act.BET, 40, 40, pot_before=60, think_ms=2_000),
            act(Street.TURN, 2, Act.CALL, 40, 40, pot_before=100, to_call=40,
                think_ms=2_000),
        ], board=["As", "Kd", "7c", "2h"])
        hand.hand_id = f"norm{i}"
        record_hand(hand, books)

    book = books["b"]["hu"]
    assert book.rate("pace:snap:flop:check") == pytest.approx(0.5, abs=0.05)
    assert book.rate("after:snap:flop:check:fold_next") == 1.0
    assert book.rate("after:normal:flop:check:fold_next") == 0.0

    profile = build_profile(book)
    cells = {f"{c.pace}:{c.street}:{c.action}": c for c in timing_tells(profile)}
    snap_check = cells["snap:flop:check"]
    assert snap_check.n >= 5
    assert snap_check.share == pytest.approx(0.5, abs=0.05)
    assert snap_check.label != "Giving up"
    assert snap_check.fold_next == pytest.approx(1.0)
    assert snap_check.fold_next_base == pytest.approx(0.0)
    assert "Weaker" in snap_check.label or "fold" in snap_check.read.lower()


def test_aggression_denominator_includes_checks():
    """A bet-or-check player is 50% aggressive once checks enter the denom."""
    books = {}
    for i, kind in enumerate((Act.CHECK, Act.BET)):
        hand = build_hand([
            act(Street.PREFLOP, 1, Act.POST_SB, 5, 5),
            act(Street.PREFLOP, 2, Act.POST_BB, 10, 10, pot_before=5),
            act(Street.PREFLOP, 1, Act.CALL, 5, 10, pot_before=15, to_call=5),
            act(Street.PREFLOP, 2, Act.CHECK, to_amount=10, pot_before=20),
            act(Street.FLOP, 2, Act.CHECK, pot_before=20),
            act(Street.FLOP, 1, kind, amount=10 if kind is Act.BET else 0,
                to_amount=10 if kind is Act.BET else 0, pot_before=20),
            act(Street.FLOP, 2, Act.FOLD, pot_before=30, to_call=10)
            if kind is Act.BET else act(Street.FLOP, 2, Act.CHECK, pot_before=20),
        ], board=["2c", "7d", "9h"])
        hand.hand_id = f"agg{i}"
        record_hand(hand, books)
    book = books["a"]["hu"]
    num = book.ratios["act:flop:bet"].hits + book.ratios["act:flop:raise"].hits
    den = sum(book.ratios[f"act:flop:{k}"].hits
              for k in ("bet", "raise", "call", "fold", "check"))
    assert den == 2 and num / den == 0.5
    # Without checks in the denom this would have been 1.0.
    without_checks = sum(book.ratios[f"act:flop:{k}"].hits
                         for k in ("bet", "raise", "call", "fold"))
    assert num / without_checks == 1.0


def test_fold_vs_bet_counts_once_per_street():
    """Facing bet → raise → facing re-raise → fold is one opportunity for each."""
    hand = build_hand([
        act(Street.PREFLOP, 1, Act.POST_SB, 5, 5),
        act(Street.PREFLOP, 2, Act.POST_BB, 10, 10, pot_before=5),
        act(Street.PREFLOP, 1, Act.CALL, 5, 10, pot_before=15, to_call=5),
        act(Street.PREFLOP, 2, Act.CHECK, to_amount=10, pot_before=20),
        act(Street.FLOP, 2, Act.CHECK, pot_before=20),
        act(Street.FLOP, 1, Act.BET, 10, 10, pot_before=20),
        act(Street.FLOP, 2, Act.RAISE, 30, 30, pot_before=30, to_call=10),
        act(Street.FLOP, 1, Act.RAISE, 90, 90, pot_before=60, to_call=20),
        act(Street.FLOP, 2, Act.FOLD, pot_before=150, to_call=60),
    ], board=["2c", "7d", "9h"])
    books = {}
    record_hand(hand, books)
    # Without the once-per-street gate, B would have 2 fold_vs_bet opportunities
    # (raise, then fold). Only the first face counts.
    assert books["b"]["hu"].opps("fold_vs_bet:flop") == 1
    assert books["b"]["hu"].rate("fold_vs_bet:flop") == 0.0
    assert books["a"]["hu"].opps("fold_vs_bet:flop") == 1
    assert books["a"]["hu"].rate("fold_vs_bet:flop") == 0.0


def test_fold_vs_bet_splits_hu_and_position():
    hand = build_hand([
        act(Street.PREFLOP, 1, Act.POST_SB, 5, 5),
        act(Street.PREFLOP, 2, Act.POST_BB, 10, 10, pot_before=5),
        act(Street.PREFLOP, 1, Act.RAISE, 25, 30, pot_before=15, to_call=5),
        act(Street.PREFLOP, 2, Act.CALL, 20, 30, pot_before=40, to_call=20),
        act(Street.FLOP, 2, Act.CHECK, pot_before=60),
        act(Street.FLOP, 1, Act.BET, 30, 30, pot_before=60),
        act(Street.FLOP, 2, Act.FOLD, pot_before=90, to_call=30),
    ], board=["2c", "7d", "9h"])
    books = {}
    record_hand(hand, books)
    caller = books["b"]["hu"]
    assert caller.rate("fold_vs_bet:flop:hu") == 1.0
    assert caller.opps("fold_vs_bet:flop:mw") == 0
    assert caller.rate("fold_vs_bet:flop:oop") == 1.0


def test_uniformly_slow_player_is_not_a_tank_folder():
    """Absolute 8s tanks would flag everyone slow; relative pace must not."""
    from villain.features import TANK_MS

    books = {}
    slow = TANK_MS + 1_000
    for i in range(8):
        hand = build_hand([
            act(Street.PREFLOP, 1, Act.POST_SB, 5, 5, think_ms=slow),
            act(Street.PREFLOP, 2, Act.POST_BB, 10, 10, pot_before=5, think_ms=slow),
            act(Street.PREFLOP, 1, Act.RAISE, 25, 30, pot_before=15, to_call=5,
                think_ms=slow),
            act(Street.PREFLOP, 2, Act.CALL, 20, 30, pot_before=40, to_call=20,
                think_ms=slow),
            act(Street.FLOP, 2, Act.CHECK, pot_before=60, think_ms=slow),
            act(Street.FLOP, 1, Act.BET, 40, 40, pot_before=60, think_ms=slow),
            act(Street.FLOP, 2, Act.FOLD, pot_before=100, to_call=40, think_ms=slow),
        ], board=["As", "Kd", "7c"])
        hand.hand_id = f"slow{i}"
        record_hand(hand, books)
    caller = books["b"]["hu"]
    # After a baseline of slow actions, another slow fold is normal pace.
    assert caller.rate("tank_fold") == pytest.approx(0.0, abs=0.2)


# -- parallel extraction ------------------------------------------------------

def _books_snapshot(books):
    return {
        (pid, reg): (
            book.hands,
            sorted((k, round(r.hits, 9), round(r.opps, 9))
                   for k, r in book.ratios.items()),
            sorted((k, round(m.n, 9), round(m.total, 9))
                   for k, m in book.meters.items()),
        )
        for pid, by_regime in books.items() for reg, book in by_regime.items()
    }


def test_splitting_the_batch_does_not_change_the_books():
    """Counters merge, so the answer must not depend on the chunking.

    ``record_hands`` splits the expensive pass across processes on a large
    import. That is only safe because every hand is independent once the pace
    cutoffs are frozen -- this asserts it directly rather than trusting it,
    by chunking by hand instead of by process.
    """
    from tests.conftest import FIXTURE
    from villain.features import merge_books, record_hand, record_hands
    from villain.parsers import parse_file

    hands = parse_file(FIXTURE)
    whole = record_hands(hands, workers=1)

    # Same hands, recorded one chunk at a time and merged.
    from villain.features import _pace_thresholds, _think_pass
    from villain.hero import hero_of
    scratch = {}
    for hand in hands:
        _think_pass(hand, scratch)
    locks = {(pid, reg): _pace_thresholds(b)
             for pid, by in scratch.items() for reg, b in by.items()}
    hero = hero_of(hands)
    pieces = {}
    for i in range(0, len(hands), 3):
        part = {}
        for hand in hands[i:i + 3]:
            record_hand(hand, part, pace_locks=locks, hero=hero)
        merge_books(pieces, part)

    assert _books_snapshot(pieces) == _books_snapshot(whole)


def test_a_worker_failure_falls_back_instead_of_failing_the_import(monkeypatch):
    """An optimization must not be able to lose somebody's import."""
    import villain.features as features
    from tests.conftest import FIXTURE
    from villain.parsers import parse_file

    hands = parse_file(FIXTURE) * 40          # over PARALLEL_MIN_HANDS

    class Boom:
        def __init__(self, *a, **k): raise OSError("no processes here")

    monkeypatch.setattr(features, "ProcessPoolExecutor", Boom)
    books = features.record_hands(hands)
    assert books, "fallback must still produce books"
