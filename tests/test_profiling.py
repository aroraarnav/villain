"""Priors, archetype matching, exploits and skill.

Several of these are regression tests for mistakes made while building the
model, and they are named for the mistake rather than the fix.
"""

import pytest

from villain.archetypes import ARCHETYPE_BY_NAME, ARCHETYPES, IMPORTANCE, match, target_frequency
from villain.exploits import MIN_CONFIDENCE, MIN_OPPS, SHOWDOWN_MIN_OPPS, breakeven_fold, dedupe_leaks, find_leaks, measured_bluff_size, spots_to_confirm
from villain.priors import POPULATION, Estimate, population_mean, prior_for, regime, shrink
from villain.profile import PROFILE_FEATURES, build_profile
from villain.skill import deduped_exploitability, rate
from villain.stats import StatBook

# -- priors -----------------------------------------------------------------

@pytest.mark.parametrize("players,expected", [
    (2, "hu"), (3, "3max"), (4, "6max"), (6, "6max"), (7, "full"), (9, "full")])
def test_regime_boundaries(players, expected):
    assert regime(players) == expected


def test_three_handed_sits_between_heads_up_and_six_max():
    """3-max is its own game; borrowing 6-max priors would call everyone a maniac."""
    for stat in ("vpip", "pfr", "three_bet"):
        hu = POPULATION["hu"][stat]
        three = POPULATION["3max"][stat]
        six = POPULATION["6max"][stat]
        assert six < three < hu, stat


def test_shrinkage_moves_with_evidence():
    mean, strength = prior_for("fold_to_cbet:flop", "hu")
    thin = shrink(3, 3, mean, strength)
    thick = shrink(60, 80, mean, strength)
    assert abs(thin.value - mean) < 0.10        # 3 of 3 barely moves it
    assert thick.value > 0.60                   # 60 of 80 does
    assert thin.weight < thick.weight
    assert thin.hi - thin.lo > thick.hi - thick.lo


def test_a_zero_alpha_posterior_still_returns_a_probability():
    """scipy's Beta is undefined at α=0; the NaN it returns became
    ``"top_leak_severity": NaN`` on the demo roster, which the browser
    could not parse."""
    import math
    est = Estimate(value=0, lo=0, hi=1, opps=10, raw=0.0, prior=0.0,
                   weight=1, alpha=0.0, beta=10.0)
    assert math.isfinite(est.prob_above(0.5))
    assert math.isfinite(est.prob_below(0.5))
    assert 0.0 <= est.prob_above(0.5) <= 1.0


def test_estimate_exposes_its_posterior():
    est = shrink(10, 20, 0.4, 25)
    assert est.prob_above(0.0) == pytest.approx(1.0, abs=1e-6)
    assert est.prob_above(est.value) == pytest.approx(0.5, abs=0.05)
    assert est.prob_above(0.3) > est.prior_prob_above(0.3)


def test_cross_regime_priors_borrow_from_the_same_player():
    """Someone who never folds three-handed is a better prior than the field."""
    three = StatBook(player_id="p", regime="3max", hands=200)
    three.ratios["fold_vs_bet:turn"].hits = 5
    three.ratios["fold_vs_bet:turn"].opps = 100
    heads_up = StatBook(player_id="p", regime="hu", hands=10)
    heads_up.ratios["fold_vs_bet:turn"].hits = 1
    heads_up.ratios["fold_vs_bet:turn"].opps = 2

    alone = build_profile(heads_up)
    borrowed = build_profile(heads_up, others={"3max": three})
    assert borrowed.get("fold_vs_bet:turn") < alone.get("fold_vs_bet:turn")
    assert borrowed.borrowed_from == ["3max"]


# -- archetypes -------------------------------------------------------------

def test_every_archetype_is_recovered_from_its_own_frequencies(synth_profile):
    for regime_name in ("hu", "3max", "6max"):
        for arch in ARCHETYPES:
            profile = synth_profile(arch.name, regime=regime_name, opps=60)
            assert match(profile)[0] == arch.name, f"{arch.name} at {regime_name}"


def test_thin_samples_stay_uncertain(synth_profile):
    thin = synth_profile("station", opps=4)
    thick = synth_profile("station", opps=200)
    assert match(thin)[1] < match(thick)[1]
    assert match(thick)[1] > 0.8


def test_archetypes_are_scored_over_a_common_feature_set():
    """Regression: scoring each prototype over only the features it names made
    whichever prototype named the fewest win every time."""
    counts = {a.name: len(a.traits) for a in ARCHETYPES}
    assert min(counts.values()) < max(counts.values())      # they do differ
    book = StatBook(player_id="flat", regime="6max", hands=300)
    for feature in PROFILE_FEATURES:
        pop = population_mean(feature, "6max")
        book.ratios[feature].hits = pop * 100
        book.ratios[feature].opps = 100
    book.meters["table_size"].add(6, 1)
    # Field-average play must not land confidently anywhere: it is the case the
    # prototypes are least able to distinguish. This used to assert TAG, which
    # encoded the very bug that made TAG a magnet -- a bucket that *is* the
    # population centroid wins every ambiguous player by default. TAG now has
    # its own identity (tighter than field, folds rivers less), so the honest
    # assertion is about the shape of the mix, not about which name is on top.
    name, conf, mix = match(build_profile(book))
    assert conf < 0.6, f"field-average play should not be a confident {name}"
    assert sum(share for _, share in mix[:3]) > 0.5   # spread over near-field buckets
    extreme = {"nit", "maniac", "station", "limper"}
    assert name not in extreme, f"field-average play read as {name}"


def test_passive_buckets_catch_non_tags(synth_profile):
    """Home-game fish used to collapse onto TAG because it sat at zero."""
    assert match(synth_profile("loose passive", opps=80))[0] == "loose passive"
    assert match(synth_profile("tight passive", opps=80))[0] == "tight passive"
    assert match(synth_profile("tag", opps=80))[0] == "tag"


def test_archetype_targets_track_table_size():
    """The same prototype means different frequencies at different table sizes."""
    station = next(a for a in ARCHETYPES if a.name == "station")
    assert (target_frequency(station, "vpip", "hu")
            > target_frequency(station, "vpip", "6max"))


def test_feature_importance_is_shared_not_per_archetype():
    """Per-archetype weights would make the likelihoods incomparable."""
    for arch in ARCHETYPES:
        assert not hasattr(arch, "weights")
    assert set(IMPORTANCE) <= set(PROFILE_FEATURES)


# -- exploits ---------------------------------------------------------------

def test_breakeven_folds_follow_pot_odds():
    assert breakeven_fold(0.5) == pytest.approx(1 / 3)
    assert breakeven_fold(1.0) == pytest.approx(0.5)
    assert breakeven_fold(0.66) == pytest.approx(0.397, abs=0.002)


def test_a_leak_needs_evidence_not_just_a_prior():
    """Regression: population frequencies sitting near a breakeven point made
    every unseen player look exploitable."""
    empty = StatBook(player_id="new", regime="hu", hands=3)
    empty.ratios["fold_vs_bet:river"].hits = 1
    empty.ratios["fold_vs_bet:river"].opps = 1
    empty.meters["table_size"].add(2, 1)
    assert find_leaks(build_profile(empty)) == []


def test_a_player_who_is_the_field_still_gets_priced_leaks():
    """Lift asks 'did the data move the prior'. A 37k-hand exporter who
    dominates the fit *is* the prior, so lift is ~0 on every real leak and
    the Hero tab said nothing yet."""
    book = StatBook(player_id="hero", name="Hero", regime="6max", hands=37000)
    book.meters["table_size"].add(6, 1)
    book.ratios["fold_vs_bet:flop"].hits = 7000
    book.ratios["fold_vs_bet:flop"].opps = 10000
    profile = build_profile(book)
    # The fitted field is this player: same rate, a prior strong enough that
    # posterior and prior both sit on the leak side of pot-odds.
    profile.stats["fold_vs_bet:flop"] = shrink(7000, 10000, 0.70, 80.0)
    profile.priors["fold_vs_bet:flop"] = (0.70, 80.0)
    est = profile.stats["fold_vs_bet:flop"]
    assert est.weight >= 0.75
    assert est.prob_above(0.50) - est.prior_prob_above(0.50) < 0.12
    leaks = find_leaks(profile)
    assert any(l.id == "overfold_flop" for l in leaks), {l.id for l in leaks}
    skill = rate(profile)
    assert skill.exploitability > 0, "silence here is what inflated the rating"


def test_a_leaky_fitted_prior_is_still_not_a_read_about_a_thin_sample():
    """The other half of the same guard: without lift, a new player in a
    leaky home game inherits the field's leaks before anyone has seen them."""
    book = StatBook(player_id="new", regime="6max", hands=8)
    book.meters["table_size"].add(6, 1)
    book.ratios["fold_vs_bet:flop"].hits = 4
    book.ratios["fold_vs_bet:flop"].opps = 6
    profile = build_profile(book)
    profile.stats["fold_vs_bet:flop"] = shrink(4, 6, 0.70, 80.0)
    profile.priors["fold_vs_bet:flop"] = (0.70, 80.0)
    est = profile.stats["fold_vs_bet:flop"]
    assert est.weight < 0.75
    assert not any(l.id == "overfold_flop" for l in find_leaks(profile))


def test_overfolding_is_detected_and_priced(synth_profile):
    profile = synth_profile("overfolder", regime="hu", opps=120)
    leaks = find_leaks(profile)
    assert any(l.id.startswith("overfold") for l in leaks)
    top = leaks[0]
    assert top.severity > 0
    assert top.value > top.threshold
    assert top.opps >= MIN_OPPS


def test_stations_get_the_opposite_advice(synth_profile):
    leaks = find_leaks(synth_profile("station", regime="hu", opps=120))
    ids = {l.id for l in leaks}
    assert "station_turn" in ids or "station_river" in ids
    assert not any(i.startswith("overfold") for i in ids)


def test_leaks_are_sorted_by_money(synth_profile):
    leaks = find_leaks(synth_profile("overfolder", regime="hu", opps=150))
    assert leaks == sorted(leaks, key=lambda l: -l.severity)


def test_spots_to_confirm_says_never_on_the_wrong_side():
    """More hands at the same rate cannot rescue a leak that isn't real:
    the posterior concentrates away from the trigger, not toward it."""
    est = Estimate(value=0.30, lo=0.20, hi=0.40, opps=50, raw=0.30, prior=0.35,
                   weight=0.8, alpha=15.0, beta=35.0)
    assert spots_to_confirm(est, 0.40, "high") is None


def test_spots_to_confirm_gives_a_number_for_a_thin_real_leak(synth_profile):
    """A leak that already clears the trigger but is too thin to confirm gets
    a finite spot count; one that has already cleared MIN_CONFIDENCE needs no
    more."""
    thin = find_leaks(synth_profile("overfolder", regime="hu", opps=13),
                      min_confidence=0.55)
    watch = [l for l in thin if l.confidence < MIN_CONFIDENCE]
    assert watch
    assert all(l.confirms_in is not None and l.confirms_in > 0 for l in watch)

    confirmed = find_leaks(synth_profile("overfolder", regime="hu", opps=150))
    assert confirmed
    assert all(l.confirms_in == 0 for l in confirmed)


# -- skill ------------------------------------------------------------------

def test_thin_samples_are_pulled_toward_average(synth_profile):
    thin = rate(synth_profile("maniac", opps=3))
    assert 40 < thin.score < 60
    assert thin.confidence < 0.3
    assert thin.tier == "unknown"
    assert not thin.measured
    assert thin.label == "unknown"


def test_a_thin_sample_is_not_rated_competent(synth_profile):
    """The displayed number was 50 plus a sample-size lid, then labeled
    competent -- ranking a 40-hand player against a 2,000-hand regular."""
    thin = rate(synth_profile("tag", opps=10))
    solid = rate(synth_profile("tag", opps=400))
    assert not thin.measured
    assert solid.measured
    assert thin.tier == "unknown"
    assert solid.tier != "unknown"


def test_a_tag_in_this_field_is_not_scored_as_loose():
    """Hand selection is distance from TAG-at-this-table, not online 15% VPIP.

    A 28% VPIP in a 42% home game is a TAG; scoring it against the built-in
    15% target called that selection bad and dragged every looser regular
    down with it."""
    book = StatBook(player_id="hero", name="Hero", regime="6max", hands=5000)
    book.meters["table_size"].add(6, 1)
    book.ratios["vpip"].hits = 285
    book.ratios["vpip"].opps = 1000
    profile = build_profile(book)
    profile.stats["vpip"] = shrink(285, 1000, 0.42, 80.0)
    profile.priors["vpip"] = (0.42, 80.0)
    tag_here = target_frequency(ARCHETYPE_BY_NAME["tag"], "vpip", "6max", profile)
    online_tag = target_frequency(ARCHETYPE_BY_NAME["tag"], "vpip", "6max")
    assert tag_here > 0.25
    assert online_tag < 0.20
    selection = next(c for c in rate(profile).components if c.name == "Hand selection")
    assert selection.score > 90, (
        f"TAG-at-this-field ({100 * tag_here:.0f}% VPIP) scored {selection.score:.0f} "
        f"for a {100 * profile.get('vpip'):.1f}% player")


def test_exploitable_players_rate_below_solid_ones(synth_profile):
    solid = rate(synth_profile("tag", regime="hu", opps=150))
    leaky = rate(synth_profile("station", regime="hu", opps=150))
    assert solid.score > leaky.score
    assert leaky.exploitability > solid.exploitability


def test_tightness_is_penalised_less_than_looseness(synth_profile):
    """A nit gives up value; a maniac gives up money. They are not equal errors."""
    nit = rate(synth_profile("nit", regime="6max", opps=150))
    maniac = rate(synth_profile("maniac", regime="6max", opps=150))
    assert nit.score > maniac.score


def test_rating_components_are_transparent(synth_profile):
    skill = rate(synth_profile("lag", regime="hu", opps=120))
    assert skill.components
    assert all(0 <= c.score <= 100 for c in skill.components)
    assert any(c.name == "Resistance to exploitation" for c in skill.components)


def test_size_bucket_breakeven_matches_bucket_midpoint():
    assert breakeven_fold(0.33) == pytest.approx(0.33 / 1.33)
    assert breakeven_fold(0.85) == pytest.approx(0.85 / 1.85)


def test_measured_bluff_size_prefers_faced_sizing():
    """Bluffs at them are priced on the size they folded to, not their own bets."""
    book = StatBook(player_id="x", name="X", regime="hu", hands=50)
    profile = build_profile(book)
    profile.means["bet_size:flop"] = 0.4
    profile.means["faced_size:flop"] = 0.75
    assert measured_bluff_size(profile, "flop") == pytest.approx(0.75)
    assert measured_bluff_size(profile, "flop", "big") == pytest.approx(0.85)


def test_bluff_severity_is_absolute_bb_per_100():
    """Regression: an extra /100 priced every leak in bb/hand while labeling
    it bb/100. Pin an absolute number so that cannot silently return."""
    from villain.exploits import RULES, _severity
    from villain.priors import shrink

    book = StatBook(player_id="x", name="X", regime="hu", hands=100)
    book.meters["table_size"].add(2, 1)
    profile = build_profile(book)
    profile.hands = 100
    profile.stats["fold_vs_bet:flop"] = shrink(30, 50, 0.40, 20)
    profile.means["pot_to_bluff:flop"] = 6.0
    profile.means["faced_size:flop"] = 2.0 / 3.0
    rule = next(r for r in RULES if r.id == "overfold_flop")
    # pot=6, size=2/3, fold-be=0.20 → gain/spot = 6*0.2*(5/3)=2.0
    # spots/100=50, capture=0.35 → 2.0 * 0.35 * 50 = 35.0
    assert _severity(profile, rule, 0.60, 0.40) == pytest.approx(35.0)


def test_limps_priced_as_preflop_isolation_not_flop_bluff():
    """Regression: limps used the flop-bluff EV model against limp frequency,
    so a 30% limper printed ~75 bb/100 from that leak alone."""
    from villain.exploits import RULES, _severity
    from villain.priors import shrink

    book = StatBook(player_id="x", name="X", regime="6max", hands=100)
    book.meters["table_size"].add(6, 1)
    profile = build_profile(book)
    profile.hands = 100
    profile.stats["limp"] = shrink(30, 100, 0.15, 40)
    profile.means["open_bb"] = 3.0
    rule = next(r for r in RULES if r.id == "limps")
    assert rule.spot == "preflop" and rule.ev == "steal"
    # Excess limp 0.15 × (open 3 + blinds+limp 2.5) × capture 0.5 × 100 spots
    # = 0.15 * 5.5 * 0.5 * 100 = 41.25
    assert _severity(profile, rule, 0.30, 0.15) == pytest.approx(41.25)


def test_overlapping_leaks_do_not_double_count_skill():
    from villain.exploits import Leak
    from villain.playbook import entry_for

    leaks = [
        Leak("overfold_flop", "a", "b", "fold_vs_bet:flop", 0.6, 0.4, 0.4,
             20, 0.9, 0.3, 2.0, "high", entry_for("overfold_flop")),
        Leak("overfold_cbet", "a", "b", "fold_to_cbet:flop", 0.6, 0.4, 0.4,
             20, 0.9, 0.3, 1.5, "high", entry_for("overfold_cbet")),
        Leak("overfold_flop_big", "a", "b", "fold_vs_bet:flop:big", 0.7, 0.46, 0.4,
             12, 0.85, 0.25, 1.8, "high", entry_for("overfold_flop_big")),
    ]
    assert deduped_exploitability(leaks) == pytest.approx(2.0)
    assert {l.id for l in dedupe_leaks(leaks)} == {"overfold_flop"}


def test_showdown_leaks_need_thicker_samples(synth_profile):
    profile = synth_profile("maniac", regime="hu", opps=12)
    # Thin river_bet_bluff must not clear the showdown bar.
    if "river_bet_bluff" in profile.stats:
        profile.stats["river_bet_bluff"] = shrink(
            8, 12, 0.30, 20)
        assert profile.stats["river_bet_bluff"].opps < SHOWDOWN_MIN_OPPS
        ids = {l.id for l in find_leaks(profile)}
        assert "bluffs_rivers" not in ids


def test_resolve_stat_prefers_heads_up_fold_sample():
    """MDF is a one-defender formula; OOP mixes multiway and is the wrong universe."""
    from villain.exploits import RULES, _resolve_stat
    from villain.priors import shrink

    book = StatBook(player_id="x", name="X", regime="6max", hands=200)
    book.meters["table_size"].add(6, 1)
    profile = build_profile(book)
    profile.stats["fold_vs_bet:flop"] = shrink(90, 200, 0.44, 20)
    profile.stats["fold_vs_bet:flop:oop"] = shrink(75, 150, 0.44, 20)
    profile.stats["fold_vs_bet:flop:hu"] = shrink(40, 100, 0.44, 20)
    rule = next(r for r in RULES if r.id == "overfold_flop")
    assert _resolve_stat(profile, rule) == "fold_vs_bet:flop:hu"


def test_heads_up_books_fall_back_to_the_pooled_fold_sample():
    """Synth profiles and HU-regime books have no multiway mix."""
    from villain.exploits import RULES, _resolve_stat
    from villain.priors import shrink

    book = StatBook(player_id="x", name="X", regime="hu", hands=100)
    profile = build_profile(book)
    profile.stats["fold_vs_bet:flop"] = shrink(40, 50, 0.40, 20)
    rule = next(r for r in RULES if r.id == "overfold_flop")
    assert _resolve_stat(profile, rule) == "fold_vs_bet:flop"


def _flop_fold_book(regime, hands, faced=0.66, **ratios):
    book = StatBook(player_id="x", name="X", regime=regime, hands=hands)
    book.meters["table_size"].add({"hu": 2, "6max": 6}[regime], 1)
    book.meters["faced_size:flop"].add(faced, 200)
    book.meters["pot_to_bluff:flop"].add(8.0, 200)
    for stat, (hits, opps) in ratios.items():
        book.ratios[stat].hits = hits
        book.ratios[stat].opps = opps
    return book


def test_multiway_oop_folds_are_not_a_heads_up_overfold():
    """A 6-max field sits on MDF heads-up and folds more multiway / OOP.

    Pricing the OOP slice at a one-defender breakeven is how most of a home
    game got 'folds too often to flop bets'."""
    book = _flop_fold_book(
        "6max", 400,
        **{"fold_vs_bet:flop": (94, 200),          # 47%, the pooled mix
           "fold_vs_bet:flop:hu": (39, 100),       # 39%, on the MDF line
           "fold_vs_bet:flop:oop": (76, 150),      # 51%, MW-inflated
           "fold_vs_bet:flop:mw": (55, 100),       # 55%
           "fold_vs_bet:flop:small": (41, 100)})   # past small-bet MDF, mixed
    profile = build_profile(book)
    ids = {l.id for l in find_leaks(profile)}
    assert not any(i.startswith("overfold_flop") for i in ids)


def test_heads_up_overfold_still_fires_at_six_max():
    book = _flop_fold_book(
        "6max", 400,
        **{"fold_vs_bet:flop": (70, 100),
           "fold_vs_bet:flop:hu": (70, 100),
           "fold_vs_bet:flop:oop": (70, 100)})
    profile = build_profile(book)
    ids = {l.id for l in find_leaks(profile)}
    assert "overfold_flop" in ids
    leak = next(l for l in find_leaks(profile) if l.id == "overfold_flop")
    assert leak.stat == "fold_vs_bet:flop:hu"


def test_cbet_overfold_does_not_fire_off_a_multiway_mix():
    """fold_to_cbet pools HU and MW. Without a HU fold leak it must not
    become the leftover 'surrenders to continuation bets' after the parent
    overfold_flop stops firing."""
    book = _flop_fold_book(
        "6max", 400,
        **{"fold_vs_bet:flop": (94, 200),
           "fold_vs_bet:flop:hu": (39, 100),
           "fold_to_cbet:flop": (70, 120)})
    profile = build_profile(book)
    ids = {l.id for l in find_leaks(profile)}
    assert "overfold_cbet" not in ids
    assert not any(i.startswith("overfold_flop") for i in ids)


def test_the_field_tick_uses_the_fitted_population():
    """The chart compared a shrunk estimate to the online mean after fit
    replaced it, so most of a home-game pool read high-VPIP vs field."""
    from villain.priors import population_mean, shrink
    from villain.webapp.payloads import _references

    book = StatBook(player_id="x", name="X", regime="6max", hands=200)
    book.meters["table_size"].add(6, 1)
    profile = build_profile(book, priors={"vpip": (0.42, 30.0)})
    profile.stats["vpip"] = shrink(80, 200, 0.42, 30)
    refs = _references("vpip", profile.regime, profile)
    assert refs["population"] == pytest.approx(0.42)
    assert population_mean("vpip", "6max") != pytest.approx(0.42)


def test_skill_prices_leaks_at_the_same_bar_as_the_profile(synth_profile):
    """rate() used 0.55; the profile list used 0.70, so worth bb/100 disagreed."""
    from villain.exploits import MIN_CONFIDENCE, find_leaks

    profile = synth_profile("station", regime="hu", opps=200)
    skill = rate(profile)
    listed = find_leaks(profile, min_confidence=MIN_CONFIDENCE)
    from villain.skill import deduped_exploitability
    assert skill.exploitability == pytest.approx(deduped_exploitability(listed), abs=0.01)
