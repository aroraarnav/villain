"""Hero: what only the exporting player's own hand history can tell you."""

import pytest

from villain.db import Store
from villain.hero import (
    CHECK,
    FOLD,
    MARGIN,
    MIN_TELL_HANDS,
    SIZING,
    SIZING_TELL_GAP,
    TIMING,
    TIMING_TELL_GAP,
    Bucket,
    Grade,
    GradeReport,
    StreetStrength,
    Tell,
    combined_grid,
    find_hero,
    fold_grades,
    hand_class,
    hero_visibility,
    missed_value,
    preflop_range,
    range_narrowing,
    sizing_tell,
    texture_label,
    timing_tell,
)


@pytest.fixture
def stored(tmp_path, hands):
    with Store(tmp_path / "v.db") as store:
        store.add_hands(hands)
        yield store


# -- hand_class ---------------------------------------------------------------

@pytest.mark.parametrize("cards,expected", [
    (("Ah", "Kd"), "AKo"),
    (("Kd", "Ah"), "AKo"),           # order of the two cards must not matter
    (("Ah", "Kh"), "AKs"),
    (("7c", "7d"), "77"),
    (("2c", "3c"), "32s"),
    (("2c", "3d"), "32o"),
])
def test_hand_class(cards, expected):
    assert hand_class(cards) == expected


# -- find_hero ------------------------------------------------------------

def test_find_hero_picks_the_export_owner(stored):
    """The fixture has one player with cards known on every hand -- the
    account that exported it -- and several others only shown occasionally
    at showdown. min_hands is lowered because the fixture is tiny."""
    hero_id = find_hero(stored, min_hands=10)
    row = next(r for r in stored.players() if int(r["id"]) == hero_id)
    assert row["hands"] == 18


def test_find_hero_refuses_a_thin_or_absent_signal(stored):
    """The real default bar (100+ hands) is far above this fixture's size,
    so nobody should qualify -- a thin fixture must not be misread as hero."""
    assert find_hero(stored) is None


# -- preflop_range ----------------------------------------------------------

def test_preflop_range_accounts_for_every_dealt_hand(stored):
    hero_id = find_hero(stored, min_hands=10)
    by_position = preflop_range(stored.player_hands(hero_id), hero_id)
    assert by_position
    for pos in by_position.values():
        assert sum(pos.dealt_classes.values()) == pos.hands
        # Every hand is either a walk (no line below adds up to it) or ends
        # in exactly one of these four buckets.
        assert pos.raised + pos.called + pos.checked + pos.folded <= pos.hands


def test_preflop_range_raised_classes_are_a_subset_of_dealt(stored):
    hero_id = find_hero(stored, min_hands=10)
    by_position = preflop_range(stored.player_hands(hero_id), hero_id)
    for pos in by_position.values():
        for cls, count in pos.raised_classes.items():
            assert count <= pos.dealt_classes.get(cls, 0)
        for cls, count in pos.played_classes.items():
            assert count <= pos.dealt_classes.get(cls, 0)


def test_hero_visibility_matches_dealt_hands(stored):
    hero_id = find_hero(stored, min_hands=10)
    hero_hands = stored.player_hands(hero_id)
    seen, total = hero_visibility(hero_hands, hero_id)
    assert total == 18       # the fixture's hero, matching test_find_hero_picks_the_export_owner
    assert seen == total     # cards known on every one of hero's own hands


class _StubModel:
    """Always predicts a fixed strength, so fold_grades's own arithmetic --
    not the fitted model -- is what gets tested here."""

    def predict(self, features):
        return 0.5


def test_fold_grades_produces_well_formed_grades(stored):
    hero_id = find_hero(stored, min_hands=10)
    hero_hands = stored.player_hands(hero_id)
    report = fold_grades(hero_hands, hero_id, _StubModel())
    assert isinstance(report, GradeReport)
    for g in report.grades:
        assert 0.0 <= g.strength <= 1.0
        assert g.faced_strength == 0.5
        assert 0.0 < g.required_equity < 1.0
        assert g.street in (1, 2, 3)


def test_combined_grid_sums_every_position(stored):
    hero_id = find_hero(stored, min_hands=10)
    by_position = preflop_range(stored.player_hands(hero_id), hero_id)
    grid = combined_grid(by_position)
    total_dealt = sum(n for _, n in grid.values())
    assert total_dealt == sum(p.hands for p in by_position.values())
    for played, dealt in grid.values():
        assert 0 <= played <= dealt


# -- fold grades: the priced comparison -----------------------------------------------------

def _grade(strength, faced_strength, required_equity, pot_before_bb=10.0, to_call_bb=5.0):
    return Grade(
        FOLD, hand_id="h", street=1, hole_cards=("Ah", "Kh"), board=["2c", "7d", "9s"],
        strength=strength, faced_strength=faced_strength,
        required_equity=required_equity,
        pot_before_bb=pot_before_bb, to_call_bb=to_call_bb,
    )


def test_edge_is_strength_minus_what_the_bet_represents():
    g = _grade(strength=0.80, faced_strength=0.55, required_equity=0.30)
    assert g.edge == pytest.approx(0.25)


def test_a_fold_is_flagged_only_when_both_bars_clear():
    # Clears the model comparison but not the raw price: not a mistake.
    priced_out = _grade(strength=0.40, faced_strength=0.20, required_equity=0.50)
    assert priced_out.edge > MARGIN
    assert not priced_out.flagged

    # Clears the price but is no stronger than what the bet usually is:
    # not a mistake either.
    unremarkable = _grade(strength=0.60, faced_strength=0.58, required_equity=0.30)
    assert unremarkable.strength > unremarkable.required_equity
    assert not unremarkable.flagged

    # Clears both: a real mistake.
    real = _grade(strength=0.80, faced_strength=0.55, required_equity=0.30)
    assert real.flagged


def test_worst_ranks_by_worth_not_by_edge_alone():
    """A bigger pot with the same edge should rank as the worse fold."""
    small_pot = _grade(strength=0.80, faced_strength=0.55, required_equity=0.30,
                       pot_before_bb=5.0, to_call_bb=2.0)
    big_pot = _grade(strength=0.80, faced_strength=0.55, required_equity=0.30,
                     pot_before_bb=50.0, to_call_bb=20.0)
    report = GradeReport(grades=[small_pot, big_pot])
    assert report.worst(2) == [big_pot, small_pot]


def test_rate_and_by_street_ignore_unflagged_grades():
    ok = _grade(strength=0.40, faced_strength=0.45, required_equity=0.30)
    bad = _grade(strength=0.90, faced_strength=0.50, required_equity=0.30)
    report = GradeReport(grades=[ok, bad])
    assert report.graded == 2
    assert report.flagged == [bad]
    assert report.rate == pytest.approx(0.5)
    assert report.by_street() == {1: (1, 2)}


# -- sizing_tell --------------------------------------------------------------

def _tell(kind, strong_hands, strong_avg, weak_hands, weak_avg):
    strong = Bucket("top half", hands=strong_hands, total=strong_hands * strong_avg)
    weak = Bucket("bottom half", hands=weak_hands, total=weak_hands * weak_avg)
    return Tell(by_street={1: (strong, weak)}, kind=kind)


def _sizing(strong_hands, strong_avg, weak_hands, weak_avg):
    return _tell(SIZING, strong_hands, strong_avg, weak_hands, weak_avg)


def test_gap_needs_both_sides_thick_enough():
    thin = _sizing(strong_hands=MIN_TELL_HANDS - 1, strong_avg=0.9,
                   weak_hands=MIN_TELL_HANDS, weak_avg=0.4)
    assert thin.gap(1) is None

    thick = _sizing(strong_hands=MIN_TELL_HANDS, strong_avg=0.9,
                    weak_hands=MIN_TELL_HANDS, weak_avg=0.4)
    assert thick.gap(1) == pytest.approx(0.5)


def test_tells_needs_the_gap_to_clear_the_bar():
    small_gap = _sizing(strong_hands=20, strong_avg=0.55, weak_hands=20, weak_avg=0.50)
    assert small_gap.gap(1) < SIZING_TELL_GAP
    assert small_gap.tells() == []

    real_gap = _sizing(strong_hands=20, strong_avg=0.70, weak_hands=20, weak_avg=0.40)
    assert real_gap.tells() == [(1, real_gap.gap(1))]


def test_sizing_tell_produces_one_bucket_pair_per_postflop_street(stored):
    hero_id = find_hero(stored, min_hands=10)
    st = sizing_tell(stored.player_hands(hero_id), hero_id)
    assert set(st.by_street) == {1, 2, 3}      # flop, turn, river -- never preflop
    for strong, weak in st.by_street.values():
        assert strong.hands >= 0 and weak.hands >= 0


def test_sizing_tell_describe_names_the_gap_when_it_clears_the_bar():
    real_gap = _sizing(strong_hands=20, strong_avg=0.70, weak_hands=20, weak_avg=0.40)
    assert "observant opponent" in real_gap.describe(1)

    small_gap = _sizing(strong_hands=20, strong_avg=0.55, weak_hands=20, weak_avg=0.50)
    assert "observant opponent" not in small_gap.describe(1)

    empty = Tell(by_street={}, kind=SIZING)
    assert empty.describe(1) is None


def test_sizing_tell_describe_lead_false_drops_the_street_opener():
    g = _sizing(strong_hands=20, strong_avg=0.70, weak_hands=20, weak_avg=0.40)
    assert g.describe(1, lead=True).startswith("On the flop,")
    assert g.describe(1, lead=False).startswith("You bet")
    assert "flop" not in g.describe(1, lead=False)


# -- texture_label --------------------------------------------------------------

@pytest.mark.parametrize("board,expected", [
    (["2c", "5c", "9c"], "wet"),        # suited
    (["7c", "8d", "9h"], "wet"),        # connected
    (["Ac", "8d", "2h"], "dry"),
    (["2c", "2d", "9h"], "dry"),        # paired but not suited/connected
])
def test_texture_label(board, expected):
    assert texture_label(board) == expected


# -- the sentence a grade renders itself as ---------------------------------------------------------

def test_fold_grade_in_words_states_both_numbers():
    g = _grade(strength=0.80, faced_strength=0.55, required_equity=0.30)
    words = g.in_words
    assert "80%" in words and "55%" in words and "30%" in words


def test_fold_grade_summary_is_shorter_than_in_words():
    g = _grade(strength=0.80, faced_strength=0.55, required_equity=0.30)
    assert "80%" in g.summary and "55%" in g.summary and "30%" in g.summary
    assert len(g.summary) < len(g.in_words)


def test_fold_report_by_texture_matches_by_street_shape():
    wet = Grade(FOLD, hand_id="a", street=1, hole_cards=("Ah", "Kh"),
                board=["2c", "5c", "9c"], strength=0.9, faced_strength=0.5,
                required_equity=0.3, pot_before_bb=10.0, to_call_bb=5.0)
    dry = Grade(FOLD, hand_id="b", street=1, hole_cards=("Ah", "Kh"),
                board=["Ac", "8d", "2h"], strength=0.4, faced_strength=0.45,
                required_equity=0.3, pot_before_bb=10.0, to_call_bb=5.0)
    report = GradeReport(grades=[wet, dry])
    assert wet.texture == "wet" and dry.texture == "dry"
    assert report.by_texture() == {"wet": (1, 1), "dry": (0, 1)}


# -- missed_value -----------------------------------------------------------

def _missed(strength, faced_strength, pot_before_bb=10.0):
    return Grade(
        CHECK, hand_id="h", street=1, hole_cards=("Ah", "Kh"), board=["2c", "7d", "9s"],
        strength=strength, faced_strength=faced_strength, pot_before_bb=pot_before_bb,
    )


def test_missed_value_needs_the_bigger_margin():
    """CHECK's margin is deliberately wider than FOLD's -- see GradeKind. A
    gap that would count as a fold mistake should not automatically count as
    missed value."""
    small_edge = _missed(strength=0.70, faced_strength=0.55)   # edge 0.15 > MARGIN(0.05)
    assert small_edge.edge > MARGIN
    assert small_edge.edge < CHECK.margin
    assert not small_edge.flagged

    big_edge = _missed(strength=0.90, faced_strength=0.50)     # edge 0.40 > CHECK.margin
    assert big_edge.flagged


def test_a_check_faces_no_price_so_only_the_margin_gates_it():
    """The second bar in `flagged` is pot odds, and a check faces none. It
    stays in the shared rule because required_equity is 0 for a CHECK, which
    is the honest reading rather than a special case."""
    g = _missed(strength=0.90, faced_strength=0.50)
    assert g.required_equity == 0.0 and g.to_call_bb == 0.0
    assert g.worth() == pytest.approx(g.edge * g.pot_before_bb)


def test_missed_value_report_aggregates_like_a_fold_report():
    ok = _missed(strength=0.40, faced_strength=0.45)
    bad = _missed(strength=0.90, faced_strength=0.50)
    report = GradeReport(grades=[ok, bad])
    assert report.graded == 2
    assert report.flagged == [bad]
    assert report.rate == pytest.approx(0.5)
    assert report.by_street() == {1: (1, 2)}


def test_missed_value_in_words_states_both_numbers():
    g = _missed(strength=0.90, faced_strength=0.50)
    words = g.in_words
    assert "90%" in words and "50%" in words


def test_missed_value_summary_is_shorter_than_in_words():
    g = _missed(strength=0.90, faced_strength=0.50)
    assert "90%" in g.summary and "50%" in g.summary
    assert len(g.summary) < len(g.in_words)


def test_missed_value_produces_well_formed_grades(stored):
    hero_id = find_hero(stored, min_hands=10)
    hero_hands = stored.player_hands(hero_id)
    report = missed_value(hero_hands, hero_id, _StubModel())
    assert isinstance(report, GradeReport)
    for g in report.grades:
        assert 0.0 <= g.strength <= 1.0
        assert g.faced_strength == 0.5
        assert g.street in (1, 2, 3)


# -- timing_tell --------------------------------------------------------------

def _timing(strong_hands, strong_avg, weak_hands, weak_avg):
    return _tell(TIMING, strong_hands, strong_avg, weak_hands, weak_avg)


def test_timing_gap_needs_both_sides_thick_enough():
    thin = _timing(strong_hands=MIN_TELL_HANDS - 1, strong_avg=5.0,
                   weak_hands=MIN_TELL_HANDS, weak_avg=2.0)
    assert thin.gap(1) is None

    thick = _timing(strong_hands=MIN_TELL_HANDS, strong_avg=5.0,
                    weak_hands=MIN_TELL_HANDS, weak_avg=2.0)
    assert thick.gap(1) == pytest.approx(3.0)


def test_timing_tells_needs_the_gap_to_clear_the_bar():
    small_gap = _timing(strong_hands=20, strong_avg=3.0, weak_hands=20, weak_avg=2.5)
    assert abs(small_gap.gap(1)) < TIMING_TELL_GAP
    assert small_gap.tells() == []

    real_gap = _timing(strong_hands=20, strong_avg=3.0, weak_hands=20, weak_avg=8.0)
    assert real_gap.tells() == [(1, real_gap.gap(1))]


def test_timing_describe_names_the_bluff_tank_direction():
    """Negative gap -- weaker hands take longer -- is the tank-as-bluff tell."""
    tanks_bluffs = _timing(strong_hands=20, strong_avg=3.0, weak_hands=20, weak_avg=8.0)
    assert "bluffs" in tanks_bluffs.describe(1)

    tanks_value = _timing(strong_hands=20, strong_avg=8.0, weak_hands=20, weak_avg=3.0)
    assert "strong hands" in tanks_value.describe(1)


def test_timing_describe_lead_false_drops_the_street_opener():
    g = _timing(strong_hands=20, strong_avg=3.0, weak_hands=20, weak_avg=8.0)
    assert g.describe(1, lead=True).startswith("On the flop,")
    assert g.describe(1, lead=False).startswith("You took")
    assert "flop" not in g.describe(1, lead=False)


def test_timing_tell_produces_one_bucket_pair_per_postflop_street(stored):
    hero_id = find_hero(stored, min_hands=10)
    tt = timing_tell(stored.player_hands(hero_id), hero_id)
    assert set(tt.by_street) == {1, 2, 3}
    for strong, weak in tt.by_street.values():
        assert strong.hands >= 0 and weak.hands >= 0


# -- range_narrowing ----------------------------------------------------------

def test_range_narrowing_only_counts_streets_hero_actually_saw(stored):
    hero_id = find_hero(stored, min_hands=10)
    rn = range_narrowing(stored.player_hands(hero_id), hero_id)
    assert all(isinstance(s, StreetStrength) for s in rn)
    assert all(s.street in (1, 2, 3) for s in rn)
    assert all(0.0 <= s.avg_strength <= 1.0 for s in rn)
    assert all(s.hands > 0 for s in rn)
    # Hands live get thinner (or equal) street by street -- nobody more hands
    # on the turn than saw the flop.
    by_street = {s.street: s.hands for s in rn}
    if 1 in by_street and 2 in by_street:
        assert by_street[2] <= by_street[1]
    if 2 in by_street and 3 in by_street:
        assert by_street[3] <= by_street[2]


# -- hero_of: the same question asked of a hand list --------------------------
# Getting this wrong is silent. Nothing crashes; one opponent's decisions are
# just relabelled as your own. So both routes to the answer are checked, along
# with the refusal to guess when neither is clear.

from dataclasses import replace  # noqa: E402

from villain.hero import MIN_UNBIASED, hero_of  # noqa: E402
from villain.model import hand_from_dict, hand_to_dict  # noqa: E402

EXPORTER = "1oC_kmhrYm"     # the ``playerId`` in the fixture export


def _stripped(hands):
    """The same hands with the export's own word for it removed."""
    return [replace(hand_from_dict(hand_to_dict(h)), hero_seat=None) for h in hands]


def test_parser_records_the_exporter_seat(hands):
    seated = [h for h in hands if any(s.player_id == EXPORTER for s in h.seats)]
    assert seated, "fixture should seat the exporter"
    for hand in seated:
        assert hand.hero_seat is not None
        assert hand.seat(hand.hero_seat).player_id == EXPORTER


def test_hands_without_the_exporter_have_no_hero_seat(hands):
    for hand in hands:
        if not any(s.player_id == EXPORTER for s in hand.seats):
            assert hand.hero_seat is None


def test_hero_seat_survives_serialisation(hands):
    for hand in hands:
        assert hand_from_dict(hand_to_dict(hand)).hero_seat == hand.hero_seat


def test_hero_seat_defaults_to_none_for_hands_stored_before_it_existed(hands):
    payload = hand_to_dict(hands[0])
    del payload["hero_seat"]
    assert hand_from_dict(payload).hero_seat is None


def test_the_export_own_word_wins(hands):
    assert hero_of(hands) == EXPORTER


def test_hero_follows_the_seat_through_rekeying(hands):
    """``rebuild`` re-keys seats onto internal ids; the answer must follow."""
    rekeyed = []
    for hand in hands:
        copy = hand_from_dict(hand_to_dict(hand))
        for seat in copy.seats:
            seat.player_id = f"internal-{seat.player_id}"
        rekeyed.append(copy)
    assert hero_of(rekeyed) == f"internal-{EXPORTER}"


def test_inference_agrees_when_the_file_does_not_say(hands):
    """The same reasoning find_hero uses, reached without a store."""
    assert hero_of(_stripped(hands)) == EXPORTER


def test_too_few_hands_to_infer_returns_none(hands):
    assert hero_of(_stripped(hands)[:2]) is None


def test_no_hands_returns_none():
    assert hero_of([]) is None


def test_a_villain_who_shows_every_hand_is_not_mistaken_for_the_exporter(hands):
    """Cards turned face up are not evidence of who exported. This is the
    stricter signal find_hero cannot use over a whole database, and it is why
    the hand-count floor here can be so much lower."""
    stripped = []
    for hand in _stripped(hands):
        for seat in hand.seats:
            if len(seat.hole_cards) == 2:
                seat.showed = True
        stripped.append(hand)
    assert hero_of(stripped) is None


def test_the_margin_holds_against_a_frequent_shower(hands):
    other = next(s.player_id for h in hands for s in h.seats if s.player_id != EXPORTER)
    stripped, shown = [], 0
    for hand in _stripped(hands):
        for seat in hand.seats:
            if seat.player_id == other and shown < MIN_UNBIASED:
                seat.hole_cards, seat.showed = ("Ah", "Kd"), False
                shown += 1
        stripped.append(hand)
    assert hero_of(stripped) == EXPORTER
