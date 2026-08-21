"""From stat books to profiles: shrunk estimates and derived features.

``PROFILE_FEATURES`` is the vector everything downstream agrees on -- archetype
matching, clustering, skill and the exploit rules all read the same numbers, so
a definition change propagates everywhere at once instead of drifting.

Estimates are shrunk through two levels. Population first: what players in
general do at this table size. Then the player themselves, across the other
table sizes they have been seen at -- somebody who never folds three-handed is
a decent prior for how they play heads-up, far better than the population is.
The discount on that second level is deliberate; the regimes are related, not
the same game.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .priors import CONTINUOUS, NEIGHBORS, REGIME_LABELS, SHORT, Estimate, logit, prior_for, regime, shrink, sigmoid
from .stats import VS_HERO, Meter, Ratio, StatBook

# The features that define a player, in the order clustering expects.
PROFILE_FEATURES = [
    "vpip", "pfr", "raise_share", "three_bet", "fold_to_three_bet",
    "cbet:flop", "cbet:turn", "cbet:river",
    "fold_to_cbet:flop", "fold_to_cbet:turn",
    "fold_vs_bet:flop", "fold_vs_bet:turn", "fold_vs_bet:river",
    "check_raise:flop", "donk:flop",
    "wwsf", "wtsd", "wsd",
    "aggression:flop", "aggression:turn", "aggression:river",
    "limp", "bb_defend",
]

# Frequencies computed from other counters rather than counted directly.
# Aggression includes checks in the denominator: "of everything they do" means
# check/call/fold/bet/raise. Omitting checks made a 50/50 bet-or-check player
# look 100% aggressive and polluted archetype + skill reads.
DERIVED = {
    "aggression:flop": (("act:flop:bet", "act:flop:raise"),
                        ("act:flop:bet", "act:flop:raise", "act:flop:call",
                         "act:flop:fold", "act:flop:check")),
    "aggression:turn": (("act:turn:bet", "act:turn:raise"),
                        ("act:turn:bet", "act:turn:raise", "act:turn:call",
                         "act:turn:fold", "act:turn:check")),
    "aggression:river": (("act:river:bet", "act:river:raise"),
                         ("act:river:bet", "act:river:raise", "act:river:call",
                          "act:river:fold", "act:river:check")),
}

#: How much a player's stats in a *neighbouring* table size are worth as a
#: prior for this one. Related game, not the same game.
CROSS_REGIME_DISCOUNT = 0.35

#: The fold frequency a competent player defends at, facing a normal bet. Used
#: as the fixed reference for :meth:`Profile.fold_accuracy`; the same number
#: skill.py's discipline component scores against.
CORRECT_FOLD = 0.44


@dataclass
class Profile:
    player_id: str
    name: str
    hands: int
    regime: str
    table_size: float
    stats: dict[str, Estimate] = field(default_factory=dict)
    means: dict[str, float] = field(default_factory=dict)   # continuous, shrunk
    archetype: str = "unknown"
    archetype_confidence: float = 0.0
    archetype_mix: list[tuple[str, float]] = field(default_factory=list)
    tags: list = field(default_factory=list)
    #: Statistics on which this player treats you differently from everybody
    #: else. Attached by whoever built the profile rather than derived here:
    #: the against-you counters are deliberately not part of the shrunk stats
    #: (they have no population to shrink toward), so they are read from the
    #: books by :mod:`villain.dynamics` and hung here for the surfaces.
    adjustments: list = field(default_factory=list)
    skill: object | None = None
    first_seen: int | None = None
    last_seen: int | None = None
    borrowed_from: list[str] = field(default_factory=list)
    #: Empirically fitted population, when the database has enough players to
    #: support one. Carried here so everything downstream measures against the
    #: same population the shrinkage used -- the archetype label, the exploit
    #: thresholds and the rating were all still reading the built-in online
    #: defaults, so "learn from your own pool" only ever moved the number and
    #: never the read.
    priors: dict = field(default_factory=dict)

    def fold_accuracy(self) -> float | None:
        """Mean distance from the breakeven fold frequency, across streets.

        Two-sided on purpose, and that is the whole point of it. Every trait in
        :mod:`villain.archetypes` is a *signed* deviation -- folds more than the
        field, or less -- so a player who folds far too much and one who folds
        far too little sit at opposite ends of every axis the matcher has, while
        both are making the same kind of mistake. There was no way to say "folds
        about right", which is exactly what separates a good regular from
        somebody whose frequencies merely look tight.

        Measured against one fixed reference rather than each player's own
        faced sizes. That is deliberate: pricing it per-player inverts the
        signal, because somebody who calls too much gets shown smaller bets,
        which lowers his own breakeven until he clears it. On real players the
        personalised version rated two known-weak opponents as *more*
        disciplined than two known-strong ones. The fixed bar is the frequency
        a competent player defends at, and distance from it is the measure.
        """
        errors = []
        for street in ("flop", "turn", "river"):
            est = self.stats.get(f"fold_vs_bet:{street}")
            if est is None or est.native_opps < 12:
                continue
            errors.append(abs(est.value - CORRECT_FOLD))
        return sum(errors) / len(errors) if errors else None

    def population(self, stat: str) -> float:
        """The population frequency this profile is measured against."""
        from .priors import population_mean
        fitted = self.priors.get(stat)
        return fitted[0] if fitted else population_mean(stat, self.regime)
    #: hands played at each table size, busiest first. Empty for a profile
    #: built from a single regime.
    contributions: dict[str, int] = field(default_factory=dict)

    @property
    def table_mix(self) -> str:
        """Plain description of where these hands came from."""
        if not self.contributions:
            return self.regime_label
        parts = [f"{n} {REGIME_LABELS.get(r, r)}" for r, n in self.contributions.items()]
        return ", ".join(parts)

    def get(self, stat: str) -> float | None:
        e = self.stats.get(stat)
        return e.value if e else None

    def opps(self, stat: str) -> float:
        e = self.stats.get(stat)
        return e.opps if e else 0.0

    @property
    def regime_label(self) -> str:
        return REGIME_LABELS.get(self.regime, self.regime)

    @property
    def winrate_bb100(self) -> float | None:
        v = self.means.get("net_bb")
        return v * 100 if v is not None else None

    @property
    def sample_quality(self) -> str:
        """Plain-language reliability, so a read is never quoted bare."""
        if self.hands >= 500:
            return "solid"
        if self.hands >= 150:
            return "usable"
        if self.hands >= 50:
            return "thin"
        return "guesswork"


def build_profile(book: StatBook, others: dict[str, StatBook] | None = None,
                  priors: dict[str, tuple[float, float]] | None = None,
                  native: dict[str, float] | None = None) -> Profile:
    """Shrink one regime's book into a profile.

    ``others`` is the same player's books in other regimes, used as a personal
    prior. ``priors`` overrides the population defaults, which is how
    empirically fitted priors from the database get used.
    """
    reg = book.regime or regime(book.mean("table_size") or 6.0)
    others = {r: b for r, b in (others or {}).items() if r != reg and b.hands > 0}
    priors = priors or {}

    profile = Profile(
        player_id=book.player_id, name=book.name, hands=book.hands,
        regime=reg, table_size=book.mean("table_size") or 0.0,
        first_seen=book.first_seen, last_seen=book.last_seen,
        borrowed_from=[r for r in NEIGHBORS.get(reg, ()) if r in others],
        priors=dict(priors),
    )

    def estimate(stat: str, hits: float, opps: float) -> Estimate:
        mean, strength = priors.get(stat) or prior_for(stat, reg)
        mean, strength = _personal_prior(stat, others, mean, strength)
        est = shrink(hits, opps, mean, strength)
        return replace(est, native_opps=(native or {}).get(stat, opps))

    for stat, ratio in book.ratios.items():
        # seat:/saw: are bookkeeping. vs: is left out for a different reason:
        # shrinking it toward the field would measure "how he plays against
        # you" against a population that has never played you. Its baseline is
        # the player's own pooled rate, which is what the rest of this loop
        # produces, so it is read separately once these estimates exist.
        if stat.startswith(("seat:", "saw:", VS_HERO)):
            continue
        profile.stats[stat] = estimate(stat, ratio.hits, ratio.opps)

    for stat, (num_keys, den_keys) in DERIVED.items():
        hits = sum(book.ratios[k].hits for k in num_keys if k in book.ratios)
        opps = sum(book.ratios[k].hits for k in den_keys if k in book.ratios)
        if opps:
            profile.stats[stat] = estimate(stat, hits, opps)

    continuous = CONTINUOUS.get(reg, {})
    for stat, meter in book.meters.items():
        if meter.n <= 0:
            continue
        prior_mean, prior_n = continuous.get(stat, (None, 0.0))
        profile.means[stat] = (meter.mean if prior_mean is None
                               else (meter.total + prior_mean * prior_n) / (meter.n + prior_n))
        profile.means[f"{stat}#n"] = meter.n
        if meter.sd is not None:
            profile.means[f"{stat}#sd"] = meter.sd

    return profile


def _personal_prior(stat: str, others: dict[str, StatBook], pop_mean: float,
                    pop_strength: float) -> tuple[float, float]:
    """Bend the population prior toward what this player does elsewhere."""
    hits = opps = 0.0
    for book in others.values():
        ratio = book.ratios.get(stat)
        if ratio:
            hits += ratio.hits
            opps += ratio.opps
    if opps <= 0:
        return pop_mean, pop_strength
    mean = (hits + pop_mean * pop_strength) / (opps + pop_strength)
    return mean, pop_strength + CROSS_REGIME_DISCOUNT * opps


def build_profiles(by_regime: dict[str, StatBook], min_hands: int = 1,
                   priors: dict[str, tuple[float, float]] | None = None,
                   populations: dict[str, dict] | None = None) -> list[Profile]:
    """One profile per regime the player has been seen in, busiest first.

    Each book is shrunk toward *that* table's fitted prior. Handing every
    slice the busiest-regime blob is how a heads-up book of a 6-max regular
    got measured against 6-max VPIP -- 55% is a nit heads-up and a maniac
    at that prior.
    """
    profiles = []
    for reg, book in by_regime.items():
        if book.hands < min_hands:
            continue
        blob = (populations or {}).get(reg) or priors
        profiles.append(build_profile(book, others=by_regime, priors=blob))
    profiles.sort(key=lambda p: -p.hands)
    return profiles


def merge_books(by_regime: dict[str, StatBook]) -> StatBook:
    """All regimes in one book. Use for lifetime totals, never for frequencies."""
    total = StatBook(regime="all")
    for book in by_regime.values():
        total.merge(book)
    return total


def feature_vector(profile: Profile) -> list[float | None]:
    return [profile.get(f) for f in PROFILE_FEATURES]


def evidence(profile: Profile) -> list[float]:
    """How much real data backs each feature, 0-1. Clustering weights by this."""
    return [profile.stats[f].weight if f in profile.stats else 0.0 for f in PROFILE_FEATURES]


# ---------------------------------------------------------------------------
# one player, one profile
# ---------------------------------------------------------------------------
# Splitting statistics by table size is a statistical necessity and a
# presentational disaster. It is necessary because 55% VPIP is tight heads-up
# and reckless at a full ring, so pooling the raw counts produces a number that
# describes neither game. It is a disaster because the person reading it wants
# to know what this opponent is like, not to hold four partial reads in their
# head and reconcile them.
#
# The fix is to pool in the right space. A player's *style* -- how far they sit
# from normal for the game they are in -- carries across table sizes even
# though their raw frequencies do not. So each table's counts are converted to
# a deviation from that table's own population, translated onto the primary
# table's scale, and only then added together.
#
# Concretely, for each statistic:
#
#   1. shrink the other table's rate toward that table's population, so a
#      3-of-4 sample does not arrive as 75%;
#   2. measure the deviation in log-odds, which is the regime-invariant part;
#   3. re-express that deviation against the primary table's population;
#   4. add it in as pseudo-counts, discounted, because related games are not
#      the same game.
#
# The result reads as a single profile measured against the game they play
# most, informed by everything else they have done.

def primary_regime(by_regime: dict[str, StatBook]) -> str:
    """The table size this player is mostly seen at."""
    live = {r: b for r, b in by_regime.items() if b.hands > 0}
    if not live:
        return SHORT
    return max(live.items(), key=lambda kv: kv[1].hands)[0]


def unified_book(by_regime: dict[str, StatBook],
                 populations: dict[str, dict] | None = None,
                 ) -> tuple[StatBook, dict[str, int], dict[str, float]]:
    """Fold every table size into one book on the primary table's scale."""
    live = {r: b for r, b in by_regime.items() if b.hands > 0}
    if not live:
        return StatBook(), {}, {}

    home = primary_regime(live)
    source = live[home]
    merged = StatBook(player_id=source.player_id, name=source.name, regime=home)
    merged.hands = sum(b.hands for b in live.values())
    merged.first_seen = min((b.first_seen for b in live.values()
                             if b.first_seen is not None), default=None)
    merged.last_seen = max((b.last_seen for b in live.values()
                            if b.last_seen is not None), default=None)

    native: dict[str, float] = {}
    for stat, ratio in source.ratios.items():
        if stat.startswith(VS_HERO):
            continue      # see the translation loop below
        merged.ratios[stat].hits = ratio.hits
        merged.ratios[stat].opps = ratio.opps
        native[stat] = ratio.opps
    for stat, meter in source.meters.items():
        merged.meters[stat].merge(meter)

    for reg, book in live.items():
        if reg == home:
            continue
        for stat, ratio in book.ratios.items():
            # Translation re-expresses a rate against another table size's
            # population. There is no population for a vs: counter, so pooling
            # it across regimes is a question for whoever reads it, not
            # something to decide here by translating against a number that
            # does not describe it.
            if ratio.opps <= 0 or stat.startswith(VS_HERO):
                continue
            translated = _translate_rate(stat, ratio, reg, home,
                                         populations=populations)
            weight = CROSS_REGIME_DISCOUNT * ratio.opps
            merged.ratios[stat].hits += translated * weight
            merged.ratios[stat].opps += weight
        for stat, meter in book.meters.items():
            if meter.n <= 0 or stat == "table_size":
                continue
            share = CROSS_REGIME_DISCOUNT
            merged.meters[stat].n += meter.n * share
            merged.meters[stat].total += meter.total * share
            merged.meters[stat].sumsq += meter.sumsq * share

    # table_size stays honest: the mean of every hand actually played, so the
    # profile can say what mix it came from.
    merged.meters["table_size"] = Meter()
    for book in live.values():
        merged.meters["table_size"].merge(book.meters.get("table_size", Meter()))

    contributions = {r: b.hands for r, b in sorted(
        live.items(), key=lambda kv: -kv[1].hands)}
    return merged, contributions, native


def _pop_mean_strength(stat: str, regime: str,
                       populations: dict[str, dict] | None = None,
                       ) -> tuple[float, float]:
    """Fitted (mean, strength) for this regime when we have one, else built-in."""
    blob = (populations or {}).get(regime) or {}
    fitted = blob.get(stat)
    if isinstance(fitted, tuple) and len(fitted) >= 2:
        return fitted[0], fitted[1]
    return prior_for(stat, regime)


def _translate_rate(stat: str, ratio: Ratio, source: str, target: str,
                    populations: dict[str, dict] | None = None) -> float:
    """Re-express a rate measured in one regime on another regime's scale.

    Shrunk first, because an unshrunk 0% or 100% has no finite log-odds and a
    tiny sample would translate into an extreme claim. Source and target
    populations must be the *same* field the rest of the pipeline uses --
    fitted, once ``villain fit`` has run. Translating a home-game 6-max
    observation against the online 24% VPIP table made it look like a huge
    heads-up deviation, and shrinkage after the merge cannot undo that.
    """
    mean, strength = _pop_mean_strength(stat, source, populations)
    shrunk = shrink(ratio.hits, ratio.opps, mean, strength).value
    source_pop = mean
    target_pop = _pop_mean_strength(stat, target, populations)[0]
    if source_pop == target_pop:
        return shrunk
    deviation = logit(shrunk) - logit(source_pop)
    return sigmoid(logit(target_pop) + deviation)


def build_unified(by_regime: dict[str, StatBook],
                  priors: dict[str, tuple[float, float]] | None = None,
                  populations: dict[str, dict] | None = None) -> Profile | None:
    """One profile per player, informed by every table size they have played."""
    book, contributions, native = unified_book(by_regime, populations=populations)
    if not contributions:
        return None
    profile = build_profile(book, priors=priors, native=native)
    profile.contributions = contributions
    profile.borrowed_from = [r for r in contributions if r != profile.regime]
    return profile
