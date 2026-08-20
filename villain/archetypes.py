"""Exploitative buckets.

An archetype here is not a personality, it is a *plan*. "Station" is the bucket
whose plan is "value bet thinner and stop bluffing"; "nit" is the bucket whose
plan is "open every button and believe their raises". So each prototype is
defined by the frequencies that determine that plan.

Prototypes are stored as **deviations from the population in log-odds**, not as
absolute frequencies. Two reasons, and the second is the one that bites.

First, table size: 55% VPIP is a nit heads-up, a normal three-handed player and
a maniac at a full ring. Storing "vpip: +1.2 spreads above the field" means one
prototype set works everywhere, measured against each table size's own
population.

Second, frequencies live on a bounded scale and do not shift linearly. Adding
the same number of percentage points to a 70% base and a 24% base is not the
same size of change, and doing it in linear space produces a six-handed "nit"
who plays 2% of hands and a heads-up "maniac" pinned at 97%. In log-odds the
same deviation lands on 44% and 9.5% respectively -- a heads-up nit and a
full-ring nit, which is what the prototype was always supposed to mean.

Matching is done by likelihood, not by distance to the shrunk numbers. The
difference matters: shrinking a stat toward the population and *then* measuring
its distance from a prototype counts the uncertainty twice, and every thin
sample collapses onto whichever prototype sits in the middle. Instead each
archetype implies a frequency for each feature, and the raw counts are scored
against it with a Beta-Binomial likelihood -- three observations move the
posterior a little, three hundred move it a lot, with no separate confidence
fudge factor needed.

Every archetype is scored over the *same* features, which is what makes the
comparison a comparison. A prototype that says nothing about check-raising is
not abstaining -- it is predicting the population frequency, and it takes the
same penalty as anyone else when the player check-raises three times as often.
Scoring each prototype over only the features it happens to mention would hand
the win to whichever one mentioned the fewest.

The likelihood is deliberately overdispersed (``CONCENTRATION``): an archetype
is a region of strategy space, not a point, and a station who folds to 26% of
turn bets rather than the prototype's 22% is still a station. Feature
importances are shared across archetypes for the same reason the feature set
is, and a global discount accounts for these features being correlated -- VPIP
and PFR are not independent measurements, and treating them as such would
manufacture certainty.

Clustering a database of four home game players discovers nothing, so
prototypes are the default; they work from the first hand and degrade
gracefully. Once the database holds enough players, :func:`fit_clusters` learns
the groupings actually present -- but the named plan still comes from the
prototypes, because a cluster id is not a strategy.
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
    # The preflop block is one axis measured four ways. `raise_share`, `pfr`
    # and `limp` all answer "do you enter the pot by raising", and they are
    # near-collinear on any real pool -- so the old weights (raise_share 2.6,
    # three_bet 2.2, vpip 2.0, pfr 1.3, limp 1.2 = 9.3) counted that single
    # question five times. Together with the opportunity gap -- a preflop spot
    # arrives every hand, a river fold only when the hand gets there, which is
    # n=1745 against n=263 on a real profile -- preflop decided the archetype
    # outright: on the worst-diagnosed player in the pool it contributed 108%
    # of the winning margin, the postflop evidence pointing the other way and
    # being outvoted. `vpip` keeps its weight because volume is a genuinely
    # separate axis from initiative; the rest of the block is halved.
    "vpip": 2.0, "pfr": 0.9, "raise_share": 1.4, "three_bet": 1.2,
    "fold_to_three_bet": 0.6, "limp": 0.7, "bb_defend": 1.0,
    # Postflop carries the half of the vocabulary that preflop cannot see --
    # station, maniac, trapper and the difference between a TAG and a
    # weak-tight reg are all decided after the flop. These are raised to match
    # what they are being asked to decide, which is also all that IMPORTANCE
    # was ever supposed to mean.
    "aggression:flop": 1.8, "aggression:turn": 1.8, "aggression:river": 1.5,
    "cbet:flop": 1.3, "cbet:turn": 1.1, "cbet:river": 0.8,
    "check_raise:flop": 1.6, "donk:flop": 0.8,
    # fold_vs_bet:river came down from 3.4. That value was swept honestly, but
    # against the old geometry, where every postflop target sat outside the
    # range players post and the river fold was one of the few postflop
    # features still saying anything -- so the sweep was measuring how much to
    # lean on the last working postflop feature, not how much a river fold is
    # worth. With the whole block reachable it no longer has to carry the
    # street on its own, and 3.4 on an n=263 counter is more variance than
    # signal. Re-swept in the new units; see the note under CONCENTRATION.
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
        # Tight *and* folding: without the fold family this is only a tighter
        # TAG, and the two were separated by volume alone. A nit's blind is
        # the tell -- bb_defend is the lowest claim any prototype makes.
        # pfr is well below field, not near it. VPIP and PFR are not free of
        # each other -- raise_share is very nearly their ratio -- and a
        # prototype that moves one without the other implies a player who
        # raises more hands than they play. It does so only under the built-in
        # fallback priors, where the population VPIP is an online 24% against
        # a home game's 42%, which is exactly the case a prototype has to
        # survive: it is the state of every database before `villain fit`.
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
        # The fold family *is* the identity, and it is what "station" has to
        # mean if the word is to keep any content: loose entry alone belongs
        # to "loose passive". Preflop volume is present but deliberately
        # secondary -- hanging half the label on VPIP made this a preflop-
        # looseness detector that threw out every tight player who will not
        # fold after the flop.
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
        # Same fold axis as nit, opposite end of the volume axis: a nit never
        # entered the pot, an overfolder entered and then could not continue.
        # Without the vpip split the two prototypes are the same claim twice.
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
        # Every aggression axis at once, and every value sits near the top of
        # what this pool actually posts rather than past it -- which is the
        # whole reason this prototype was nobody's label for three sessions.
        # It differs from lag in degree on aggression and in kind on the
        # blind: a maniac defends everything (bb_defend) and folds to nothing
        # (fold_to_three_bet), where a lag is selective about both.
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
        # Aggression that works, which is the only thing separating this from
        # maniac: positive wwsf against maniac's negative, and continuing
        # rather than folding when the aggression is turned back on them.
        # vpip is +0.1, not the +0.9 the name suggests, and that is a finding
        # rather than a compromise: the aggressive players in a home game pool
        # are not the wide ones. Measured, the pool's most aggressive regulars
        # run at or slightly below field VPIP and put their volume into raises
        # and later-street bets instead. Authoring the caricature -- loose
        # *and* aggressive -- made this prototype win on preflop volume
        # against TAG's tightness, which handed it 17 of 54 players on an axis
        # that has nothing to do with what "LAG" is supposed to mean.
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
        # This prototype's problem has always been that it makes the fewest
        # claims, so every player the other nine reject lands here by default
        # -- 41% of a 58-player pool at the last count. The fix is not to move
        # it further from the field for its own sake; it is to name the axis
        # it was silent on. A TAG keeps the initiative *after* the flop, and
        # that is exactly where the tight players who are not TAGs give up:
        # measured against known labels, the weak-tight regs sit a full spread
        # below on turn and river aggression while their preflop numbers are
        # indistinguishable. Stating aggression ~ field is a real prediction
        # and the one that finally separates them; see "tight passive".
        # cbet:flop is *negative* and that is not a typo. The tempting shape --
        # solid player, so bet more -- is contradicted by every known-good
        # player in the pool: they all continuation-bet the flop below field
        # and keep firing on the turn, which is the modern pattern of checking
        # back a wide range in position. Authoring the obvious sign here would
        # have put the TAGs' own numbers on the wrong side of their own label.
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
        # This prototype used to demand a low raise_share and a low PFR, which
        # made it a second "limper" -- and it left nowhere at all for the
        # commonest weak player in a home game: someone who opens raising like
        # a reg, tight, and then checks the turn back and calls a bet. Those
        # players read TAG at 95% confidence, because TAG owned the only
        # "tight and enters raising" corner the vocabulary had.
        #
        # So the preflop block here is now a deliberate *copy* of TAG's, and
        # the copying is the mechanism, not an oversight. Leaving it silent
        # was tried first and does not work: silence is a prediction of the
        # field, and on `limp` the field mean is a number almost nobody in a
        # home game posts -- most players never limp at all, a handful limp a
        # third of their hands, and the mean sits in the empty middle. A
        # prototype that declines to mention it is therefore not neutral, it
        # is wrong, and at n≈2900 it loses 2 nats for the privilege. Matching
        # TAG preflop makes the whole block cancel between the two, which is
        # what forces the label to be decided where the difference actually
        # is: the later streets. Capping the opportunity count was tried as
        # the alternative and does nothing -- see EVIDENCE_CAP.
        # The preflop half is character-for-character TAG's. Anything less than
        # an exact copy leaves a residue on the highest-opportunity features in
        # the tool, and that residue decides the label: a 0.2-spread difference
        # on `raise_share` at n=1745 outweighs the entire three-street
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
        # The limp demand is +0.9 rather than the old +2.5 because the unit
        # changed under it: limp's fitted spread is 1.60 against the assumed
        # 1.00, so +2.5 was asking for a frequency past anything in the pool
        # while *also* being the cheapest claim in the table to half-satisfy.
        # This bucket held 22% of the pool. +0.9 is the top of what players
        # here actually post.
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
        # The check-raise is the identity and wsd is the confirmation: a
        # trapper wins what they take to showdown, which is what separates
        # the slow-play from the tight passive player who simply cannot bet.
        #
        # The preflop block is TAG's, and so is "tight passive"'s: those three
        # share one preflop signature and are separated entirely by what
        # happens after the flop -- TAG keeps betting, tight passive gives up,
        # a trapper gives up *betting* and raises instead. That is the only
        # arrangement in which the check-raise gets to decide anything.
        #
        # Leaving preflop near-neutral was tried and is worse than either
        # extreme. This prototype then took 11% of a pool in which *every
        # single* player it claimed check-raised the flop **less** than the
        # field -- because near-neutral is what the average player posts, so
        # the bucket was being won on the claims that are not its identity
        # while the claim that is its identity argued against it.
        {"vpip": -1.0, "pfr": +0.1, "raise_share": +0.9, "three_bet": +0.2, "limp": -1.0,
         "check_raise:flop": +1.1, "donk:flop": -0.6,
         "cbet:flop": -1.2, "cbet:turn": -1.0,
         "aggression:flop": -0.8, "aggression:turn": -0.5,
         "wtsd": +0.5, "wsd": +0.8},
    ),
]


ARCHETYPE_BY_NAME = {a.name: a for a in ARCHETYPES}

#: How close to the correct fold frequency each plan implies its player sits,
#: and how hard that counts. This is the one axis the trait vectors cannot
#: express: every other trait is a *signed* deviation, so folding far too much
#: and folding far too little land on opposite ends, while both are the same
#: mistake. Measured on players with known labels, competent regulars sit at or
#: under 0.064 mean distance from the reference and weak ones at or over 0.069.
DISCIPLINE = {
    "tag": 0.045, "lag": 0.060, "nit": 0.110, "station": 0.130,
    "overfolder": 0.130, "maniac": 0.120, "tight passive": 0.100,
    "loose passive": 0.110, "limper": 0.110, "trapper": 0.080,
}
DISCIPLINE_SPREAD = 0.035

#: Wired into :func:`match` as an independent Gaussian term over
#: :meth:`Profile.fold_accuracy`, added after the correlation discount rather
#: than inside it -- it is not one of the correlated frequency features, so
#: discounting it alongside them would be double-counting the discount rather
#: than the evidence.
#:
#: Chosen by sweeping against villain.validate rather than the six labels, the
#: same standard CORRELATION_DISCOUNT was set by. Calibration error bottoms
#: out near 0.4 (0.041 -> 0.012), but every weight above 0.2 fails the
#: recovery test below: maniac's own trait vector implies a near-population
#: fold_vs_bet (it names no fold feature), while its measured DISCIPLINE entry
#: is 0.12, so a strong-enough weight moves synthetic maniac onto lag before
#: it moves any real player -- the exact forcing the guard exists to catch. At
#: 0.2 calibration error is still 0.041 -> 0.014, three times better, at a
#: smaller accuracy cost than 0.4 (0.556 vs 0.537) and with every prototype
#: still recovering its own frequencies.
DISCIPLINE_WEIGHT = 0.2

#: Beta-Binomial concentration. Low values mean an archetype tolerates a wide
#: band of frequencies; high values demand players hit the prototype exactly.
#: An archetype is a *region* of strategy space, not a point, and a station who
#: folds to 26% of turn bets rather than the prototype's 22% is still a
#: station. At 40 the implied tolerance is roughly one population spread.
#:
#: Making that tolerance *per feature* was tried after the spread was fitted,
#: and it does not pay -- recorded here because the argument for it is good
#: enough that somebody will try it again. A flat concentration is a fixed
#: tolerance in frequency points, which is a different region per feature once
#: the spreads are fitted from a real pool: five points is 0.3 spreads on
#: `limp` and 1.9 spreads on `fold_vs_bet:turn`. Setting concentration to
#: 1/(k*spread)^2/(p(1-p)) - 1 makes the tolerance one thing everywhere, which
#: is what the docstring above claims it already is.
#:
#: What that misses is that the narrowest-spread features are also the ones
#: closest to being results rather than style -- `wwsf` and `wsd` -- and it
#: hands them concentrations of 180 and 100 against 5 for `raise_share`. Held
#: out, the substitution is a clear regression (predictive loss 0.55366 ->
#: 0.55563, back to the pre-recalibration baseline, and halves-agree 0.522 ->
#: 0.442). Clamping the range recovers most of it but never beats flat: the
#: best clamp measured, [20, 45], is 0.55391, and it is only better than the
#: wider clamps because it is nearly flat already. Making this work would need
#: the importances re-derived alongside it, which is a larger change than the
#: evidence supports.
CONCENTRATION = 40.0

#: Features are correlated (VPIP with PFR, every fold stat with every other),
#: so the naive-Bayes product over-counts evidence. Discounting the total
#: log-likelihood keeps the posterior from reaching false certainty.
#: Measured rather than guessed. The value should be the effective number of
#: independent measurements divided by the total importance, and two methods
#: agree: the eigenvalue participation ratio of the importance-weighted
#: correlation matrix over a real pool gives n_eff 7.06 of 32.0 (0.221), and
#: held-out cross-validation -- fit on half a player's hands, score against the
#: other half -- minimizes log loss at 0.15-0.25.
#:
#: Earlier values were tuned against a simulation that generated players *from*
#: the prototypes. In that world the model is correctly specified, so no such
#: harness can ever detect prototype misfit, and it will always endorse too
#: much confidence. Against disjoint halves of real players, 0.55 produced a
#: calibration error of 0.275 and nine players at 1.00; 0.20 gives 0.092 and
#: none, at no cost in accuracy.
#:
#: Re-swept after the spread was fitted, because this constant trades against
#: the width of the unit the prototypes are written in and that width changed
#: by up to 2x per feature. 0.14 / 0.20 / 0.28 / 0.38 / 0.50 against held-out
#: predictive loss: 0.55457 / 0.55394 / 0.55372 / 0.55375 / 0.55387, flat from
#: 0.28 on and clearly worse below 0.20. 0.28 is also where calibration error
#: bottoms out (0.008) and top-1 accuracy peaks (0.469), so all three agree on
#: it. The eigenvalue argument above still gives 0.221 -- the extra room comes
#: from the prototypes no longer duplicating each other's claims across
#: independent axes, which is a real reduction in double counting.
CORRELATION_DISCOUNT = 0.28

#: How common each archetype is in the wild -- the prior the likelihood updates.
#: With no hands on a player, this is the answer.
#:
#: Close to flat, and deliberately flatter than the online-pool numbers it used
#: to hold. A prior is not free: it is added to every posterior, so a 2.5x gap
#: between two archetypes is 0.9 nats of permanent tilt, and on a real pool the
#: entire likelihood margin separating the most aggressive player in the
#: database from `lag` was 0.6 nats. The old mix had `maniac` at 0.04 against
#: `lag` at 0.10 and `nit` at 0.08 against `tag` at 0.16, so those buckets were
#: losing every close call before a single hand was counted -- which is a large
#: part of why they were never anybody's label.
#:
#: The numbers that produced those gaps described an online pool. A home game
#: is not one: it is looser and more aggressive, and there is no reason to
#: believe a maniac here is half as likely as a LAG. Where the honest answer is
#: "we do not know the mix of this particular game", a near-flat prior says so,
#: and lets the hands decide -- which is the whole point of the tool. The
#: residual ordering is only the little that is safe to assume anywhere: the
#: passive buckets are a touch commoner than the specialists.
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

    The traits were authored as multiples of a spread -- "folds 2.2 spreads
    less than the field" -- without checking the frequency that implies, and
    the spread constant is roughly twice the pool's real scatter on postflop
    features. The two errors compound: ``station`` demands a turn fold of
    0.258 where the tightest player in a 63-player pool folds 0.353, so nobody
    could be a station however they played.

    One factor for the whole vector, not a clamp per feature. A prototype is a
    shape -- which frequencies deviate, and how far relative to each other --
    and clamping the ones that stick out flattens exactly the features that
    made it distinctive. Scaling moves it toward the field while keeping it
    recognizably itself.

    Player-blind: the factor depends on the pool's observed range and the
    prototype's own traits, never on any individual's numbers.
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
            # beta-binomial log-likelihood grows with the opportunity count,
            # and opportunity counts are wildly unequal by *where the spot
            # occurs*, not by how much the feature tells you: a preflop
            # feature gets a spot every hand, a river fold only when the hand
            # gets there. On a real 13,888-hand profile that is raise_share at
            # n=1745 and limp at n=2901 against fold_vs_bet:river at n=263, so
            # preflop play decided the archetype 108% of the way -- the
            # postflop evidence pointed the other way and was outvoted.
            # IMPORTANCE is supposed to be what weights a feature; this stops
            # the sample size from overruling it.
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
