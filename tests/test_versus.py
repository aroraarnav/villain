"""The against-you slice of each counter.

The slice is only ever read against the pooled counter it came from, so the
tests here are mostly relationships between the two: it can never exceed its
parent, it must equal it when you were the only opponent, and adding it must
not have moved the parent by a single opportunity.
"""

import pytest

from villain.evidence import find
from villain.features import record_hand, record_hands
from villain.hero import hero_of
from villain.priors import regime as regime_of
from villain.profile import build_profile, build_unified, unified_book
from villain.stats import VS_HERO


def _books(hands, hero):
    books: dict = {}
    for hand in hands:
        record_hand(hand, books, hero=hero)
    return books


@pytest.fixture(scope="module")
def hero(hands):
    return hero_of(hands)


@pytest.fixture(scope="module")
def books(hands):
    return record_hands(hands)


def _versus(books):
    for pid, by_regime in books.items():
        for regime, book in by_regime.items():
            for stat, ratio in book.ratios.items():
                if stat.startswith(VS_HERO):
                    yield pid, regime, book, stat, ratio


def test_the_slice_is_recorded_at_all(books):
    assert list(_versus(books)), "fixture should produce vs: counters"


def test_every_slice_has_the_pooled_counter_it_is_read_against(books):
    for _pid, _regime, book, stat, _ratio in _versus(books):
        assert stat[len(VS_HERO):] in book.ratios, f"{stat} has no pooled parent"


def test_a_slice_never_exceeds_its_parent(books):
    for _pid, _regime, book, stat, ratio in _versus(books):
        parent = book.ratios[stat[len(VS_HERO):]]
        assert ratio.opps <= parent.opps
        assert ratio.hits <= parent.hits


def test_heads_up_the_slice_is_the_whole_counter(hands, hero):
    """With one opponent every decision is against them, so the two agree.

    The sharpest check available on the counterparty logic: any decision
    credited to the wrong player shows up here as a mismatch."""
    hu = [h for h in hands
          if regime_of(len(h.seats)) == "hu"
          and any(s.player_id == hero for s in h.seats)]
    assert hu, "fixture should contain heads-up hands with the exporter"
    books = _books(hu, hero)
    checked = 0
    for _pid, _regime, book, stat, ratio in _versus(books):
        parent = book.ratios[stat[len(VS_HERO):]]
        assert (ratio.hits, ratio.opps) == (parent.hits, parent.opps), stat
        checked += 1
    assert checked


def test_the_exporter_has_no_slice_of_their_own(books, hero):
    for _regime, book in books[hero].items():
        assert not [s for s in book.ratios if s.startswith(VS_HERO)]


def test_no_hero_means_no_slice(hands):
    for _pid, _regime, _book, stat, _ratio in _versus(_books(hands, None)):
        pytest.fail(f"recorded {stat} with nobody identified as the exporter")


def test_pooled_counters_are_untouched(hands, hero):
    """Adding the slice must not move a single existing number."""
    without = _books(hands, None)
    with_hero = _books(hands, hero)
    assert set(without) == set(with_hero)
    for pid, by_regime in without.items():
        for regime, book in by_regime.items():
            other = with_hero[pid][regime]
            pooled = {s: (r.hits, r.opps) for s, r in other.ratios.items()
                      if not s.startswith(VS_HERO)}
            assert {s: (r.hits, r.opps) for s, r in book.ratios.items()} == pooled
            assert {s: (m.n, m.total) for s, m in book.meters.items()} == \
                   {s: (m.n, m.total) for s, m in other.meters.items()}


# -- the fences -------------------------------------------------------------
# Everything that measures a statistic against the field has to leave this
# namespace alone, because there is no field frequency for "folds to that guy".


def test_profiles_carry_no_versus_stats(books):
    for _pid, by_regime in books.items():
        for _regime, book in by_regime.items():
            profile = build_profile(book, others=by_regime)
            assert not [s for s in profile.stats if s.startswith(VS_HERO)]


def test_the_unified_book_drops_them_rather_than_translating(books):
    """A rate has no meaning on another table size's scale without a
    population to be a deviation from, and this one has none."""
    for _pid, by_regime in books.items():
        merged, _contributions, native = unified_book(by_regime)
        assert not [s for s in merged.ratios if s.startswith(VS_HERO)]
        assert not [s for s in native if s.startswith(VS_HERO)]
        unified = build_unified(by_regime)
        if unified is not None:
            assert not [s for s in unified.stats if s.startswith(VS_HERO)]


def test_the_prior_fit_never_sees_them(seeded):

    samples = seeded.population_samples()
    assert samples, "fixture should produce population samples"
    for _regime, stats in samples.items():
        assert not [s for s in stats if s.startswith(VS_HERO)]


# -- evidence ---------------------------------------------------------------


def test_the_hands_behind_a_slice_are_a_subset_of_the_parent(hands, books, hero):
    """Same extraction as the number, so the panel cannot drift from it."""
    checked = 0
    for pid, _regime, _book, stat, ratio in _versus(books):
        if ratio.opps < 1:
            continue
        sliced = find(hands, pid, stat)
        pooled = find(hands, pid, stat[len(VS_HERO):])
        assert sliced, f"no evidence for {stat} (n={ratio.opps})"
        assert {e.hand_id for e in sliced} <= {e.hand_id for e in pooled}
        checked += 1
    assert checked
