"""Exploitative buckets.

An archetype is a *plan*, stored as deviations from the population in
log-odds so one prototype works at every table size. Matching scores raw
counts against each implied frequency (overdispersed Beta-Binomial), not
distance to already-shrunk numbers -- shrinking then measuring counts the
uncertainty twice. Every archetype is scored on the same features.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .priors import logit, population_mean, sigmoid, spread_of
from .profile import PROFILE_FEATURES, Profile

#: How much each feature counts toward identifying a plan. Shared by every
#: archetype: these are exponents in a likelihood, so varying them per
#: archetype would make the scores incomparable.
IMPORTANCE = {
    # Preflop is one axis measured four ways. Old weights summed to 9.3 on
    # that single question and, with n_preflop >> n_river, decided the label
    # outright. Volume (vpip) stays; the rest of the block is halved.
    "vpip": 2.0, "pfr": 0.9, "raise_share": 1.4, "three_bet": 1.2,
    "fold_to_three_bet": 0.6, "limp": 0.7, "bb_defend": 1.0,
    # Station / maniac / trapper / TAG vs weak-tight are postflop questions.
    "aggression:flop": 1.8, "aggression:turn": 1.8, "aggression:river": 1.5,
    "cbet:flop": 1.3, "cbet:turn": 1.1, "cbet:river": 0.8,
    "check_raise:flop": 1.6, "donk:flop": 0.8,
    # River fold was 3.4 when it was the last reachable postflop feature;
    # with the whole block in range, 3.4 on n≈263 is variance.
    "fold_vs_bet:flop": 1.6, "fold_vs_bet:turn": 1.8, "fold_vs_bet:river": 1.8,
    "fold_to_cbet:flop": 1.4, "fold_to_cbet:turn": 1.0,
    "wtsd": 1.6, "wsd": 1.0, "wwsf": 0.8,
}
DEFAULT_IMPORTANCE = 1.0


@dataclass(frozen=True)
class Archetype:
    name: str
    summary: str
    plan: str
    traits: dict[str, float]        # feature -> deviation from population, in spreads

    def deviation(self, feature: str) -> float:
        """Unmentioned features are a prediction of population-average play."""
        return self.traits.get(feature, 0.0)


ARCHETYPES: list[Archetype] = [
    Archetype(
        "nit",
        "Far tighter than the table demands and folds rather than defends.",
        "Steal their blinds every orbit -- they fold far more than the "
        "price of a raise requires, so it prints regardless of your cards. "
        "Then get out of the way. When they raise, or call two streets, "
        "they have it, and the pots they contest are the ones to keep "
        "small. Your profit here is a lot of tiny uncontested pots, not one "
        "big one.",
        # Tight *and* folding, else this is only a tighter TAG. bb_defend is
        # the lowest claim any prototype makes. raise_share stays near field
        # so VPIP/PFR cannot imply raising more hands than they play -- which
        # is what happens under the built-in (online) priors every new db has.
        {"vpip": -1.5, "pfr": -0.9, "raise_share": +0.2, "three_bet": -0.3,
         "fold_to_three_bet": +0.8, "limp": -0.9, "bb_defend": -1.3,
         "fold_to_cbet:flop": +1.0, "fold_vs_bet:flop": +1.0, "fold_vs_bet:turn": +1.2,
         "wtsd": -1.1, "aggression:flop": -0.6, "check_raise:flop": -0.6},
    ),
    Archetype(
        "station",
        "Calls far too much and folds far too little; will not release a pair.",
        "Bet every hand that is ahead, on all three streets, and size up as "
        "you go -- they are not folding to a price, so charge them "
        "properly. Top pair with a bad kicker is a three-street value hand "
        "here. The discipline is the other half: never bluff, not on a "
        "scare card and not with a busted draw. Checking back your air is "
        "where most of the edge against them comes from.",
        # Fold family is the identity; loose entry alone is "loose passive".
        {"vpip": +0.3, "pfr": -0.4, "raise_share": -0.6, "three_bet": -0.4, "limp": +0.4,
         "fold_to_cbet:flop": -1.4, "fold_vs_bet:flop": -1.4,
         "fold_vs_bet:turn": -1.5, "fold_vs_bet:river": -1.3,
         "wtsd": +1.1, "wsd": -0.8,
         "aggression:flop": -0.8, "aggression:turn": -0.9},
    ),
    Archetype(
        "overfolder",
        "Plays plenty of pots and surrenders them the moment there is pressure.",
        "Bet at them constantly and keep the sizes small -- they are "
        "folding to the fact of the bet, not to its price, so there is no "
        "reason to risk more. Flop, then turn, and take the river too when "
        "the board misses the obvious draws. The one rule: stop the moment "
        "they do anything other than fold. Their raises and their multi-"
        "street calls are genuine, because everything weak left the hand "
        "already.",
        # Same fold axis as nit, opposite volume. Without the vpip split the
        # two prototypes are the same claim twice.
        {"vpip": +0.3, "pfr": 0.0, "raise_share": 0.0, "three_bet": -0.2, "limp": 0.0,
         "fold_to_cbet:flop": +1.2, "fold_to_cbet:turn": +1.2,
         "fold_vs_bet:flop": +1.2, "fold_vs_bet:turn": +1.3, "fold_vs_bet:river": +1.1,
         "wtsd": -1.0, "check_raise:flop": -0.7, "wwsf": -1.0,
         "aggression:flop": -0.6},
    ),
    Archetype(
        "maniac",
        "Relentless aggression at a frequency no range can support.",
        "Play patient and passive, and let them do the betting. Call down "
        "far lighter than feels comfortable -- middle pair is a real hand "
        "against a range this wide -- and flat rather than raise your "
        "strong hands so they keep firing into them. Never bluff and never "
        "raise as a bluff: that is the one part of their game already "
        "working. Expect variance; the money arrives in lumps.",
        # Targets sit near the top of what this pool posts, not past it --
        # otherwise nobody is a maniac. Differs from LAG on degree and on
        # the blinds (defends everything, folds to nothing). Fold family is
        # required: aggression alone tripped no exploit rule.
        {"vpip": +1.0, "pfr": +1.4, "raise_share": +1.1, "three_bet": +1.6,
         "limp": -1.0, "bb_defend": +1.1, "fold_to_three_bet": -1.0,
         "cbet:flop": +0.8, "cbet:turn": +1.2,
         "aggression:flop": +1.2, "aggression:turn": +1.4, "aggression:river": +1.3,
         "check_raise:flop": +1.2,
         # The money leak, and the half of the archetype that "relentless
         # aggression" alone does not capture: a maniac cannot fold either.
         # Without it the prototype is only an intensified LAG and nothing
         # prices against it -- a synthetic player built from this vector
         # tripped no exploit rule at all, which is not what the word means.
         "fold_vs_bet:flop": -1.0, "fold_vs_bet:turn": -1.1, "fold_to_cbet:flop": -1.0,
         "wtsd": +0.4, "wwsf": -0.3, "wsd": -0.4},
    ),
    Archetype(
        "lag",
        "Wide, aggressive and competent -- applies pressure with a real plan.",
        "Tighten what you open but widen everything you continue with -- "
        "against this player the money is made after the flop, not before "
        "it. Re-raise their late-position opens, and call down more on "
        "turns and rivers rather than folding to pressure. Do not start a "
        "bluffing war on the later streets; they are competent, and that "
        "part of their game works. Position matters more here than against "
        "anyone else.",
        # Aggression that works (positive wwsf). vpip is +0.1 because home-game
        # LAGs are not the wide ones -- authoring loose *and* aggressive made
        # this win on preflop volume, 17 of 54 players, on the wrong axis.
        {"vpip": +0.1, "pfr": +0.4, "raise_share": +0.5, "three_bet": +0.6,
         "limp": -0.9, "fold_to_three_bet": -0.4,
         "cbet:flop": +0.5, "cbet:turn": +0.8,
         "aggression:flop": +1.1, "aggression:turn": +1.2, "aggression:river": +1.1,
         "check_raise:flop": +1.0,
         "fold_vs_bet:turn": -0.8, "fold_vs_bet:river": -0.7, "wwsf": +0.9},
    ),
    Archetype(
        "tag",
        "Solid home-game reg: tight, enters raising, keeps betting after the flop.",
        "There is no cheap edge here, so stop looking for one -- the "
        "mistake against a solid player is inventing a read and over-"
        "adjusting to it. Play a straightforward positional game, keep pots "
        "small out of position, and take thin value where it exists. If "
        "there is a weaker player at the table, your attention belongs on "
        "them; against this one, breaking even is a fine result.",
        # Fewest claims, so rejects land here (41% of 58 at last count). The
        # axis it was silent on is postflop initiative: weak-tight regs sit a
        # spread below on turn/river aggression with identical preflop.
        # cbet:flop is *negative* -- known-good players check back a wide
        # range in position and fire the turn. Authoring "solid so c-bet more"
        # put TAGs on the wrong side of their own label.
        {"vpip": -1.1, "pfr": +0.1, "raise_share": +1.0, "three_bet": +0.3,
         "limp": -1.0, "donk:flop": -0.8, "fold_to_three_bet": +0.3,
         "cbet:flop": -0.4, "cbet:turn": +0.4,
         "aggression:flop": 0.0, "aggression:turn": +0.1, "aggression:river": +0.2,
         "check_raise:flop": +0.4, "wtsd": -0.2, "wsd": +0.5, "wwsf": +0.5},
    ),
    Archetype(
        "tight passive",
        "Opens like a reg and gives up after the flop -- not a TAG.",
        "Open wider into their blinds and keep betting when they check; they "
        "are folding more than the pot odds ask for and rarely put you in "
        "hard spots themselves. Do not pay off their rare raises -- those "
        "are the strong end of an otherwise timid range. Value is thinner "
        "than against a station because they will get away from weak pairs, "
        "so prefer bluffs and small-stab continuation bets over three-street "
        "value with mediocre holdings.",
        # Preflop is a character-for-character copy of TAG's -- that is the
        # mechanism. Silence is a field prediction, and on limp the field mean
        # sits in an empty middle (n≈2900, ~2 nats of loss). A 0.2-spread
        # residue on raise_share at n=1745 outweighs the three-street
        # aggression gap this prototype exists to detect.
        {"vpip": -1.1, "pfr": +0.1, "raise_share": +1.0, "three_bet": +0.3,
         "limp": -1.0, "donk:flop": -0.8, "fold_to_three_bet": +0.3,
         "cbet:flop": -1.0, "cbet:turn": -0.5,
         "aggression:flop": -0.7, "aggression:turn": -0.9, "aggression:river": -0.9,
         "check_raise:flop": -0.7,
         "fold_vs_bet:turn": +0.2,
         "wtsd": +0.7, "wsd": -0.2, "wwsf": -0.5},
    ),
    Archetype(
        "loose passive",
        "Sees too many flops and then calls along -- passive fish, not a station.",
        "Isolate their limps and calls with a wide raising range, then bet "
        "for thin value on later streets; they came to play and will stick "
        "around with second pair. Bluff less than against a nit -- they call "
        "too often for pure air to print -- but do not turn into a station "
        "yourself: when they finally raise, give them credit. The edge is "
        "volume of small-to-medium value pots, not one heroic bluff.",
        # Elevated VPIP is the gate, and the fold family is deliberately
        # milder than station's: this is the bucket for loose-and-passive,
        # while refusing to fold at all is what earns the other name.
        {"vpip": +1.5, "pfr": -0.2, "raise_share": -0.4, "three_bet": -0.4, "limp": +0.3,
         "fold_to_cbet:flop": -0.5, "fold_vs_bet:flop": -0.5, "fold_vs_bet:turn": -0.4,
         "aggression:flop": -0.8, "aggression:turn": -0.8, "cbet:flop": -0.4,
         "wtsd": +0.7, "wsd": -0.5, "wwsf": -0.5},
    ),
    Archetype(
        "limper",
        "Passive preflop -- limps in, calls raises, then plays fit-or-fold.",
        "Raise over every limp with a wide range, around four big blinds "
        "plus one per limper, and bet the flop whether or not you "
        "connected. Their limping range is capped and built for seeing "
        "cheap flops, so they miss constantly and give up when they do. "
        "Slow down when they call the flop -- that call means something "
        "real -- and fold to their raises without hesitation.",
        # +0.9 not +2.5: limp's fitted spread is 1.60 vs assumed 1.00, so
        # +2.5 was past anyone in the pool. This bucket held 22%.
        {"raise_share": -1.1, "vpip": +0.2, "pfr": -1.0, "limp": +0.7, "three_bet": -0.9,
         "fold_to_cbet:flop": +0.7, "fold_vs_bet:flop": +0.6,
         "aggression:flop": -0.9, "cbet:flop": -0.3, "wwsf": -0.7},
    ),
    Archetype(
        "trapper",
        "Tight and passive with a slow-play habit: checks strength, then raises.",
        "Bet your good hands for value but stay alert: this player checks "
        "strong hands rather than betting them, so a check is not the "
        "invitation it is against most opponents. Keep pots small with "
        "marginal holdings and treat every check-raise as the real thing -- "
        "they do not have a bluffing range there. The edge comes from not "
        "paying them off in the big pots they engineer.",
        # Check-raise is the identity, wsd the confirmation. Preflop is TAG's
        # (and tight-passive's): three share one preflop signature, separated
        # after the flop. Near-neutral preflop was tried; this then claimed
        # 11% of a pool where every one of them check-raised *less* than field.
        {"vpip": -1.0, "pfr": +0.1, "raise_share": +0.9, "three_bet": +0.2, "limp": -1.0,
         "check_raise:flop": +1.1, "donk:flop": -0.6,
         "cbet:flop": -1.2, "cbet:turn": -1.0,
         "aggression:flop": -0.8, "aggression:turn": -0.5,
         "wtsd": +0.5, "wsd": +0.8},
    ),
]


ARCHETYPE_BY_NAME = {a.name: a for a in ARCHETYPES}

#: Folding too much and too little are the same mistake; signed deviations
#: cannot say that. Competent known labels sit ≤0.064 from the reference,
#: weak ones ≥0.069.
DISCIPLINE = {
    "tag": 0.045, "lag": 0.060, "nit": 0.110, "station": 0.130,
    "overfolder": 0.130, "maniac": 0.120, "tight passive": 0.100,
    "loose passive": 0.110, "limper": 0.110, "trapper": 0.080,
}
DISCIPLINE_SPREAD = 0.035

#: Independent Gaussian on fold_accuracy, *after* the correlation discount.
#: Weight 0.2: above that, synthetic maniac (no fold trait, DISCIPLINE 0.12)
#: lands on lag. Calibration 0.041 → 0.014; every prototype still recovers.
DISCIPLINE_WEIGHT = 0.2

#: Overdispersed Beta-Binomial: an archetype is a region, not a point.
#: 40 ≈ one population spread. Per-feature concentration was tried after
#: spreads were fitted and loses (0.55366 → 0.55563 held-out; halves-agree
#: 0.522 → 0.442) -- it hands wwsf/wsd concentrations of 180/100 vs 5 for
#: raise_share. Clamping never beats flat. Do not retry without re-deriving
#: IMPORTANCE alongside.
CONCENTRATION = 40.0

#: Naive-Bayes over-counts correlated features. 0.28 from held-out loss /
#: calibration / top-1 after the spread fit (eigenvalue n_eff was 0.221).
#: Do not retune against prototype-generated players -- that harness cannot
#: see misfit and always endorses too much confidence. 0.55 on real halves
#: gave calibration error 0.275 and nine players at 1.00.
CORRELATION_DISCOUNT = 0.28

#: Near-flat on purpose. A 2.5x prior gap is 0.9 nats of tilt; the likelihood
#: margin from the most aggressive player in the pool to `lag` was 0.6 nats.
#: Online mix (maniac 0.04 vs lag 0.10) lost every close call before a hand.
POPULATION_MIX = {
    "tag": 0.12, "lag": 0.12, "loose passive": 0.12, "limper": 0.11,
    "station": 0.10, "tight passive": 0.10, "overfolder": 0.09,
    "maniac": 0.08, "nit": 0.08, "trapper": 0.08,
}


def deviations(profile: Profile) -> dict[str, float]:
    """Player minus population, in spreads, for every feature with data."""
    out: dict[str, float] = {}
    for feature in PROFILE_FEATURES:
        est = profile.stats.get(feature)
        if est is None or est.opps <= 0:
            continue
        pop = profile.population(feature)
        out[feature] = ((logit(est.value) - logit(pop))
                        / spread_of(feature, profile.regime, profile.priors))
    return out


#: Below this, a prototype has been shrunk so far it no longer describes the
#: thing it is named after; leave it unreachable rather than quietly relabel it.
MIN_PROTOTYPE_SCALE = 0.45

#: Off restores the authored traits exactly, for A/B against the harness.
PROTOTYPE_RESCALE = True


def prototype_scale(arch: Archetype, profile: Profile | None) -> float:
    """How far a prototype must shrink for every target to be humanly posted.

    Traits are authored as multiples of a spread constant that runs about
    twice the pool's real postflop scatter, so a prototype can demand a
    frequency nobody posts: ``station`` wants a 0.258 turn fold where the
    tightest of 63 players folds 0.353.

    One factor for the whole vector, not a clamp per feature: a prototype is a
    shape, and clamping the features that stick out flattens what made it
    distinctive. Player-blind -- the factor comes from the pool's range and the
    prototype's traits, never from an individual.
    """
    if not PROTOTYPE_RESCALE or profile is None or not profile.priors:
        return 1.0
    scale = 1.0
    for feature, deviation in arch.traits.items():
        band = profile.priors.get(f"range:{feature}")
        if not band or not deviation:
            continue
        low, high = band
        pop = profile.population(feature)
        spread = spread_of(feature, profile.regime, profile.priors)
        step = deviation * spread
        if not step:
            continue
        edge = logit(high if deviation > 0 else low) - logit(pop)
        if edge / step <= 0:          # already pointing into the band
            continue
        scale = min(scale, edge / step)
    return max(min(scale, 1.0), MIN_PROTOTYPE_SCALE)


def target_frequency(arch: Archetype, feature: str, table_regime: str,
                     profile: Profile | None = None) -> float:
    """The frequency this archetype implies for a feature at this table size.

    Pass ``profile`` to measure against that player's fitted population rather
    than the built-in one. A prototype is a deviation *from the field*, so with
    a fitted pool the same deviation has to be applied to the fitted mean or
    the label is describing a different field than the numbers are.
    """
    pop = profile.population(feature) if profile is not None \
        else population_mean(feature, table_regime)
    spread = spread_of(feature, table_regime,
                       profile.priors if profile is not None else None)
    return sigmoid(logit(pop) + arch.deviation(feature) * spread
                   * prototype_scale(arch, profile))


# Two attempts at the reachability problem -- station, maniac, nit and trapper
# asking for frequencies nobody in the pool posts -- were measured against
# `villain validate` and both looked rejected. Recorded with the caveat that
# matters more than the numbers:
#
#   *** validate cannot judge a change to this function. ***
#
# `validate._best_supported` builds its ground truth by calling
# `target_frequency`, so a change here moves the label and the thing predicting
# it together. Its own docstring says the target "has to be independent of
# every constant being tuned, or the harness scores the tuning against
# itself" -- and then calls this. The numbers below are therefore evidence
# that these changes are self-consistent, not that they are wrong:
#
#                        log loss   accuracy   calibration   agreement
#   baseline                1.295      0.558         0.003       0.593
#   fitted spreads          1.362      0.558         0.015       0.611
#   clamp target to band    1.343      0.566         0.022       0.566
#
# The band is still fitted and stored, because the exploit thresholds use it to
# stay inside what players actually do -- it is only the archetype targets that
# are left alone, and only until the harness can judge them.
#
# What the dead prototypes actually need is not a different spread. Their
# traits were authored as "+/-2.2 spreads" without anyone checking the
# frequency that implies, and the implied frequencies sit outside real play:
#
#   station  fold_vs_bet:turn   needs 0.258   pool 0.353-0.639
#   maniac   aggression:turn    needs 0.435   pool 0.162-0.380
#   nit      vpip               needs 0.194   pool 0.235-0.723
#   trapper  check_raise:flop   needs 0.267   pool 0.021-0.167
#
# Rescaling each trait vector by the largest factor that keeps every target
# inside the observed band (station 0.60, trapper 0.56, limper 0.58, nit 0.84)
# revives station and trapper and takes calibration error 0.011 -> 0.001. Fix
# the harness first; then this is a poker judgement about what "station"
# means, not a statistics problem.
#
# EVIDENCE_CAP is the one lever validate *can* judge honestly, because it does
# not touch this function. Swept against a fixed target it does nothing at all:
# log loss 1.295 -> 1.298 at a cap of 400, and agreement pinned at 0.593 for
# every cap from 2000 down to 80. The opportunity-count imbalance is real and
# this is not the way to correct it.


#: Opportunities past which a single feature stops accumulating evidence.
#: ``None`` restores the old behavior, where the commonest spot wins.
#: Swept against ``villain validate``, never against a label.
EVIDENCE_CAP: int | None = None


def match(profile: Profile) -> tuple[str, float, list[tuple[str, float]]]:
    """Best-fitting archetype, its posterior probability, and the full mix.

    The returned confidence *is* the posterior -- no scaling afterwards. With
    no hands it equals the population prior; with a real sample it concentrates
    on whichever plan the counts support. Two archetypes that fit equally well
    produce two middling numbers, which is the honest answer: players do sit
    between buckets, and a forced label invites a plan the evidence cannot
    carry.
    """
    # One shared support set: every feature the player has real data on.
    observed = []
    for feature in PROFILE_FEATURES:
        est = profile.stats.get(feature)
        if est is not None and est.opps > 0 and est.raw is not None:
            # Score what was actually observed at this player's own table
            # size. `opps` also carries borrowed cross-regime pseudo-counts
            # whose rate was already shrunk toward the prior, so scoring those
            # as if they were observations counts the same uncertainty twice --
            # the exact failure this module's docstring warns about. On a real
            # pool 24% of the counts were borrowed, and the players the matcher
            # got most confidently wrong were the ones borrowing most.
            n = est.native_opps or est.opps
            # Cap how much evidence one feature may contribute. The
            # log-likelihood grows with the opportunity count, and those are
            # unequal by *where the spot occurs* rather than by how much the
            # feature says -- preflop every hand, a river fold only when the
            # hand gets there. Uncapped, preflop play decided the archetype
            # 108% of the way and outvoted the postflop evidence.
            if EVIDENCE_CAP and n > EVIDENCE_CAP:
                observed.append((feature, est.raw * EVIDENCE_CAP, float(EVIDENCE_CAP)))
            else:
                observed.append((feature, est.raw * n, n))

    fold_accuracy = profile.fold_accuracy()

    log_posterior = {}
    for arch in ARCHETYPES:
        total = 0.0
        for feature, hits, opps in observed:
            p = target_frequency(arch, feature, profile.regime, profile)
            total += (IMPORTANCE.get(feature, DEFAULT_IMPORTANCE)
                      * _log_beta_binomial(hits, opps, p))
        log_posterior[arch.name] = (
            CORRELATION_DISCOUNT * total + math.log(POPULATION_MIX.get(arch.name, 0.05))
        )
        # "Folds about right" is not expressible as a signed deviation -- see
        # Profile.fold_accuracy. Scored separately from the correlated feature
        # product above (so it is not discounted alongside them) and kept at a
        # weight that cannot on its own move a player the features disagree
        # with; see DISCIPLINE_WEIGHT.
        if fold_accuracy is not None and arch.name in DISCIPLINE:
            log_posterior[arch.name] += DISCIPLINE_WEIGHT * _log_gaussian(
                fold_accuracy, DISCIPLINE[arch.name], DISCIPLINE_SPREAD)

    if not log_posterior:
        return "unknown", 0.0, []
    peak = max(log_posterior.values())
    weights = {name: math.exp(lp - peak) for name, lp in log_posterior.items()}
    total = sum(weights.values())
    mix = sorted(((n, w / total) for n, w in weights.items()), key=lambda kv: -kv[1])
    name, share = mix[0]
    return name, round(share, 3), [(n, round(s, 3)) for n, s in mix]


def _log_beta_binomial(hits: float, opps: float, mean: float,
                       concentration: float | None = None) -> float:
    """Log marginal likelihood of ``hits``/``opps`` under a Beta(mean) prior.

    The binomial coefficient is dropped: it is identical across archetypes and
    only the differences matter.
    """
    # Read the module constant at call time: binding it as a default would
    # freeze it at import, silently ignoring anything that tries to fit it.
    if concentration is None:
        concentration = CONCENTRATION
    a = mean * concentration
    b = (1 - mean) * concentration
    return _log_beta(a + hits, b + opps - hits) - _log_beta(a, b)


def _log_beta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _log_gaussian(value: float, mean: float, spread: float) -> float:
    """Unnormalised log density -- the normalizer is the same for every
    archetype (fixed ``spread``) so dropping it changes nothing about which
    archetype wins."""
    z = (value - mean) / spread
    return -0.5 * z * z


def describe(name: str) -> Archetype | None:
    return ARCHETYPE_BY_NAME.get(name)
