"""The plain-language layer: what a leak says, and that every rule has words."""

import json

import pytest

from villain.archetypes import ARCHETYPES
from villain.exploits import PRESSURE, RULES, TIERS, find_leaks, size_band
from villain.playbook import COMBINATIONS, PLAYBOOK, combinations_for, entry_for

# -- coverage ---------------------------------------------------------------

def test_every_rule_has_a_playbook_entry():
    """A leak with no words is a number nobody can act on."""
    missing = [r.id for r in RULES if not entry_for(r.id)]
    assert not missing, f"no playbook entry for {missing}"


def test_no_orphan_playbook_entries():
    assert not set(PLAYBOOK) - {r.id for r in RULES}


def test_every_entry_answers_all_four_questions():
    for leak_id, entry in PLAYBOOK.items():
        for field in ("behavior", "why", "do", "dont"):
            text = getattr(entry, field)
            assert text and len(text) > 40, f"{leak_id}.{field} is too thin"


def test_dont_is_a_real_counter_mistake():
    """The 'do not' field exists to stop over-adjustment; it must say so."""
    for leak_id, entry in PLAYBOOK.items():
        assert entry.dont.lower().startswith(("do not", "never", "don't")), leak_id
        assert entry.dont != entry.do, leak_id


def test_combinations_reference_real_rules():
    known = {r.id for r in RULES}
    for combo in COMBINATIONS:
        assert combo.leaks <= known, combo.headline
        assert len(combo.leaks) >= 2


def test_combinations_only_fire_when_all_parts_are_present():
    combo = COMBINATIONS[0]
    part = next(iter(combo.leaks))
    assert combo not in combinations_for({part})
    assert combo in combinations_for(combo.leaks)


def test_archetype_plans_are_substantial():
    """The plan is the headline advice; two terse sentences will not do."""
    for arch in ARCHETYPES:
        assert len(arch.plan) > 200, arch.name
        assert "  " not in arch.plan, f"{arch.name} has a formatting artefact"


# -- the derived plain-language fields --------------------------------------

@pytest.mark.parametrize("severity,expected", [
    (5.0, "big"), (1.5, "solid"), (0.5, "modest"), (0.05, "small")])
def test_size_bands(severity, expected):
    assert size_band(severity)[0] == expected


def test_pressure_covers_every_tier():
    for _, tier in TIERS:
        assert tier in PRESSURE


def test_leak_exposes_words_as_well_as_numbers(synth_profile):
    leaks = find_leaks(synth_profile("overfolder", regime="hu", opps=150))
    assert leaks
    leak = leaks[0]
    for field in ("behavior", "why", "do", "dont", "priority", "pressure", "in_words"):
        assert getattr(leak, field), field
    assert "%" in leak.in_words
    assert leak.size in {"big", "solid", "modest", "small"}


def test_in_words_states_the_direction_correctly(synth_profile):
    """'more often than breakeven' and 'less often than' are opposite reads."""
    over = find_leaks(synth_profile("overfolder", regime="hu", opps=150))
    station = find_leaks(synth_profile("station", regime="hu", opps=150))
    high = next(l for l in over if l.direction == "high")
    low = next(l for l in station if l.direction == "low")
    assert "more often than" in high.in_words
    assert "less often than" in low.in_words


def test_analyze_export_carries_the_language(tmp_path, hands):
    from villain.analyze import as_dict
    from villain.db import Store
    with Store(tmp_path / "v.db") as store:
        store.add_hands(hands)
        player = max(store.players(), key=lambda r: r["hands"] or 0)
        payload = as_dict(store.profiles(int(player["id"]))[0])
    assert "combinations" in payload and "plan" in payload
    json.dumps(payload)
    for leak in payload["leaks"]:
        for field in ("behavior", "why", "do", "dont", "priority", "in_words"):
            assert leak[field], field


# -- the optional model -----------------------------------------------------

# -- one player, one profile ------------------------------------------------

def test_unified_profile_pools_every_table_size(hands):
    """A player seen at two table sizes gets one profile, not two."""
    from villain.features import record_hands
    from villain.profile import build_unified
    books = record_hands(hands)
    multi = [by for by in books.values() if len([b for b in by.values() if b.hands]) > 1]
    assert multi, "fixture should have a player at more than one table size"
    for by_regime in multi:
        unified = build_unified(by_regime)
        assert unified is not None
        assert len(unified.contributions) > 1
        assert unified.hands == sum(b.hands for b in by_regime.values() if b.hands)
        assert unified.regime == max(by_regime.items(),
                                     key=lambda kv: kv[1].hands)[0]


def test_pooling_measures_style_not_raw_frequency():
    """A player who is normal for their table must not look loose elsewhere.

    Someone playing exactly the 3-max average translates to the heads-up
    average, not to the same raw percentage -- that is the whole point of
    pooling in log-odds against each table's own population.
    """
    from villain.priors import population_mean
    from villain.profile import _translate_rate
    from villain.stats import Ratio

    average_3max = Ratio(hits=55, opps=100)      # exactly the 3-max norm
    assert population_mean("vpip", "3max") == pytest.approx(0.55)
    translated = _translate_rate("vpip", average_3max, "3max", "hu")
    assert translated == pytest.approx(population_mean("vpip", "hu"), abs=0.03)


def test_pooling_carries_a_real_deviation_across_tables():
    """Someone much looser than their table stays looser after translation."""
    from villain.priors import population_mean
    from villain.profile import _translate_rate
    from villain.stats import Ratio
    loose = Ratio(hits=85, opps=100)             # well above the 3-max norm
    translated = _translate_rate("vpip", loose, "3max", "hu")
    assert translated > population_mean("vpip", "hu")


def test_pooling_uses_fitted_means_when_they_exist():
    """Builtin online VPIP 0.24 vs this pool's 0.42: translating against the
    wrong field made a 6-max observation look like a huge HU deviation."""
    from villain.profile import _translate_rate
    from villain.stats import Ratio

    pops = {"6max": {"vpip": (0.42, 25.0)}, "hu": {"vpip": (0.55, 25.0)}}
    average = Ratio(hits=42, opps=100)
    translated = _translate_rate("vpip", average, "6max", "hu", populations=pops)
    assert translated == pytest.approx(0.55, abs=0.03)


def test_other_tables_are_discounted_not_ignored(hands):
    """Related games, not the same game: they move the estimate, but less."""
    from villain.features import record_hands
    from villain.profile import CROSS_REGIME_DISCOUNT, unified_book
    books = record_hands(hands)
    by_regime = max(books.values(), key=lambda by: sum(b.hands for b in by.values()))
    merged, contributions, _native = unified_book(by_regime)
    home = max(contributions, key=contributions.get)
    for stat, ratio in by_regime[home].ratios.items():
        other = sum(b.ratios[stat].opps for r, b in by_regime.items()
                    if r != home and stat in b.ratios)
        if not other:
            continue
        expected = ratio.opps + CROSS_REGIME_DISCOUNT * other
        assert merged.ratios[stat].opps == pytest.approx(expected)
        break
    assert CROSS_REGIME_DISCOUNT < 1.0


def test_table_mix_is_stated_plainly(hands):
    from villain.features import record_hands
    from villain.profile import build_unified
    books = record_hands(hands)
    for by_regime in books.values():
        profile = build_unified(by_regime)
        if profile and len(profile.contributions) > 1:
            assert "hands" not in profile.table_mix
            assert any(word in profile.table_mix
                       for word in ("heads-up", "3-handed", "short-handed", "full ring"))
            return
    pytest.fail("no multi-table player in the fixture")


# -- saying something useful when nothing clears the bar --------------------

def test_watchlist_holds_near_misses_only(synth_profile):
    """A watch item is below the reporting bar but above coincidence, and must
    never duplicate something already confirmed."""
    from villain.exploits import MIN_CONFIDENCE, WATCH_CONFIDENCE, find_leaks, find_watchlist
    profile = synth_profile("overfolder", regime="hu", opps=40)
    confirmed = {l.id for l in find_leaks(profile)}
    for item in find_watchlist(profile):
        assert item.id not in confirmed
        assert WATCH_CONFIDENCE <= item.confidence < MIN_CONFIDENCE
        assert item.tier == "watch"


def test_watch_items_are_not_priced_as_reads(synth_profile):
    """No price on an unconfirmed read: the tool cannot say what it is worth."""
    from villain.analyze import as_dict
    profile = synth_profile("limper", regime="3max", opps=30)
    payload = as_dict(profile)
    for item in payload["watchlist"]:
        assert "severity_bb100" not in item


def test_a_weak_player_always_has_something_to_show(synth_profile):
    """Silence is its own claim. A player the rating calls weak, with nothing
    listed against them, reads as 'no information'."""
    from villain.analyze import as_dict
    for archetype in ("limper", "station", "nit", "overfolder"):
        payload = as_dict(synth_profile(archetype, regime="6max", opps=25))
        assert payload["leaks"] or payload["watchlist"] or payload["weak_spots"], \
            f"{archetype} rated {payload['skill']['score']} with nothing to show"


def test_weak_spots_explain_the_rating(synth_profile):
    from villain.skill import WEAK_COMPONENT, rate, weaknesses
    profile = synth_profile("limper", regime="3max", opps=60)
    weak = weaknesses(rate(profile))
    assert weak, "a limper should have a visibly weak component"
    assert weak == sorted(weak, key=lambda c: c.score)
    assert all(c.score < WEAK_COMPONENT for c in weak)
    assert all(c.name != "Resistance to exploitation" for c in weak)


def test_every_rated_component_is_explained():
    """A weakness nobody can interpret is not worth showing."""
    from villain.archetypes import ARCHETYPE_BY_NAME, target_frequency
    from villain.glossary import component_help
    from villain.profile import PROFILE_FEATURES, build_profile
    from villain.skill import rate
    from villain.stats import StatBook
    book = StatBook(player_id="x", regime="6max", hands=300)
    for f in PROFILE_FEATURES:
        book.ratios[f].hits = target_frequency(ARCHETYPE_BY_NAME["tag"], f, "6max") * 60
        book.ratios[f].opps = 60
    book.meters["table_size"].add(6, 1)
    book.meters["open_bb"].add(2.5, 1)
    for component in rate(build_profile(book)).components:
        assert component_help(component.name), f"no explanation for {component.name!r}"


# -- reliability of the optional model --------------------------------------

# -- a rule that cannot fire is not conservative, it is dead -----------------

def test_a_small_threshold_keeps_a_reachable_trigger():
    """MARGIN is absolute, and two thresholds are smaller than it.

    ``no_three_bet`` (0.05) and ``never_check_raises`` (0.04) had their
    triggers pushed to 0.00 and -0.01 -- below any frequency that exists, so
    neither could fire on any player at any sample size.
    """
    from villain.exploits import MARGIN, trigger_for

    for threshold in (0.04, 0.05):
        trigger = trigger_for(threshold, "low")
        assert trigger > 0.0, f"{threshold} gives an unreachable trigger"
        assert trigger < threshold, "the guard against noise must still bite"

    # Unchanged where the threshold is comfortably larger than the margin.
    assert trigger_for(0.40, "low") == pytest.approx(0.40 - MARGIN)
    assert trigger_for(0.44, "high") == pytest.approx(0.44 + MARGIN)


def test_every_low_rule_can_be_triggered_by_some_frequency():
    """No rule may ask for a frequency below zero."""
    from villain.exploits import RULES, trigger_for
    from villain.profile import Profile

    blank = Profile(player_id="x", name="x", hands=0, regime="6max", table_size=6.0)
    for rule in RULES:
        if rule.direction != "low":
            continue
        try:
            threshold = rule.threshold(blank)
        except Exception:
            continue
        assert trigger_for(threshold, "low") > 0.0, f"{rule.id} cannot fire"


def test_no_rule_asks_for_a_frequency_nobody_could_post(tmp_path):
    """A rule whose trigger sits outside the pool's own range is dead.

    Nine of the ten chosen thresholds were: `bluffs_rivers` asked 0.40 where
    the pool's highest is 0.245, and every timing rule sat above its own
    ceiling. The pool-derived ones are now held inside the band players
    actually occupy -- including the margin the trigger adds on top, which is
    where two of them escaped it on the second pass.
    """
    from villain.exploits import MARGIN, POOL_HIGH, POOL_LOW, pool_bar, trigger_for
    from villain.profile import Profile

    low, high = 0.05, 0.30
    p = Profile(player_id="x", name="x", hands=500, regime="6max", table_size=6.0)
    p.priors = {"range:some_stat": (low, high)}

    hi_bar = pool_bar(p, "some_stat", POOL_HIGH, 0.99)
    assert trigger_for(hi_bar, "high") <= high, "nobody could ever clear it"
    lo_bar = pool_bar(p, "some_stat", POOL_LOW, 0.0)
    assert trigger_for(lo_bar, "low") >= low - MARGIN, "nobody could ever fall under it"

    # With no fitted pool, the chosen number is used unchanged.
    blank = Profile(player_id="y", name="y", hands=0, regime="6max", table_size=6.0)
    assert pool_bar(blank, "some_stat", POOL_HIGH, 0.42) == 0.42
