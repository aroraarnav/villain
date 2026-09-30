"""Whether a player treats you differently from everybody else.

The prior for "against me" is the player, not the field: subtract the
against-you slice out of their pooled counter, shrink the slice toward that
baseline, and report the deviation. Per table size, then translated onto the
primary table -- pooling raw counts invents an adjustment that is just HU vs
6-max.
"""

from __future__ import annotations

from dataclasses import dataclass

from .priors import Estimate, logit, prior_for, shrink, sigmoid
from .profile import CROSS_REGIME_DISCOUNT, primary_regime
from .stats import VS_HERO, StatBook

#: How strongly to believe, before seeing any of it, that he plays you the way
#: he plays everybody -- in opportunities. Nothing in a database can fit this
#: (it needs pairwise samples across many players; a home game has one pair),
#: so it is a judgment call: raise it and an adjustment needs more evidence,
#: lower it and normal variation reads as a read.
ADJUSTMENT_PRIOR = 30.0

#: Against-you opportunities before an adjustment can be reported at all,
#: counted at the player's own table size and never including the pseudo-counts
#: borrowed from another one.
MIN_OPPS = 12

#: And decisions against *other* people, or there is no baseline to differ
#: from. In a heads-up database "against you" and "in general" are the same
#: hands, and their difference is a number subtracted from itself.
MIN_BASELINE_OPPS = 12

#: Posterior probability the shift has the sign it appears to have.
MIN_CONFIDENCE = 0.85

#: And a floor on the shift itself. A 3-point difference can be arbitrarily
#: certain given enough hands and still not change a single decision.
MIN_GAP = 0.08

#: Facing a bet a player folds, calls or raises: one decision, three counters
#: that add to one. A shift in any is the same shift from another side, so all
#: three would say one thing three times and sort it to the top by weight of
#: numbers. Only the largest is kept, as in `exploits.dedupe_leaks`.
ONE_DECISION = ("fold_vs_bet", "call_vs_bet", "raise_vs_bet")

#: Regularization on a borrowed table size's slice -- just enough to keep the
#: log-odds finite when somebody folded to none of thirty bets. Tiny on
#: purpose: :data:`ADJUSTMENT_PRIOR` already applies at the end, and applying
#: it twice shrinks the borrowed deviation away before the cross-regime
#: discount touches it.
TRANSLATION_SMOOTHING = 2.0


@dataclass(frozen=True)
class Adjustment:
    """One statistic on which a player treats you differently."""

    stat: str                # the pooled counter, e.g. "fold_vs_bet:river"
    regime: str              # the table size this is expressed on
    versus: float            # against-you frequency, shrunk toward the baseline
    baseline: float          # their frequency against everyone else
    opps: float              # against-you decisions actually observed
    borrowed_opps: float     # pseudo-counts carried from another table size
    baseline_opps: float     # decisions against everybody else
    confidence: float        # posterior probability of the direction
    estimate: Estimate       # the full posterior, for anything that wants it

    @property
    def gap(self) -> float:
        """Signed difference: positive means they do it *more* against you."""
        return self.versus - self.baseline

    @property
    def direction(self) -> str:
        return "more" if self.gap > 0 else "less"


def adjustments(by_regime: dict[str, StatBook],
                priors: dict[str, tuple[float, float]] | None = None,
                min_opps: float = MIN_OPPS,
                min_confidence: float = MIN_CONFIDENCE) -> list[Adjustment]:
    """Every statistic where the against-you slice moved off the baseline.

    Nothing rather than something weak: below the sample, confidence or size
    floors a statistic is simply absent. Having no read is the normal case at
    these sample sizes, and an empty list says so."""
    live = {r: b for r, b in by_regime.items() if b.hands > 0}
    if not live:
        return []
    home = primary_regime(live)
    priors = priors or {}

    out: list[Adjustment] = []
    for stat in _sliced_stats(live):
        # A read that holds at one table size is still a read, and folding
        # every regime into the home one loses the strongest cases: 23% vs 42%
        # fold-to-turn-bet heads-up, the gap running the other way 6-max --
        # averaged, they cancel. Where one table size carries it, name it.
        for regime, book in live.items():
            native = _adjustment_within(stat, book, regime, priors,
                                        REGIME_MIN_OPPS, min_confidence)
            if native is not None:
                out.append(native)
        found = _adjustment(stat, live, home, priors, min_opps, min_confidence)
        if found is not None:
            out.append(found)
    out.sort(key=lambda a: -abs(a.gap))
    return _one_per_decision(out)


#: Against-you decisions needed *at one table size* before that table size is
#: reported on its own. Higher than MIN_OPPS: a claim this specific ("heads-up
#: he does not fold to you") should not rest on a dozen hands.
REGIME_MIN_OPPS = 35


def _adjustment_within(stat: str, book: StatBook, regime: str,
                       priors: dict[str, tuple[float, float]],
                       min_opps: float, min_confidence: float) -> Adjustment | None:
    """The against-you read at one table size, on that table size's own hands.

    Nothing is translated or borrowed here, which is the point: this is the
    slice as observed, measured against how they play everybody else *at the
    same table size*."""
    slice_ = book.ratios.get(VS_HERO + stat)
    if slice_ is None or slice_.opps < min_opps:
        return None
    baseline = _baseline(stat, book, regime, priors)
    if baseline is None:
        return None
    estimate = shrink(slice_.hits, slice_.opps, baseline.value, ADJUSTMENT_PRIOR)
    gap = estimate.value - baseline.value
    if abs(gap) < MIN_GAP:
        return None
    confidence = (estimate.prob_above(baseline.value) if gap > 0
                  else estimate.prob_below(baseline.value))
    if confidence < min_confidence:
        return None
    return Adjustment(
        stat=stat, regime=regime, versus=estimate.value, baseline=baseline.value,
        opps=slice_.opps, borrowed_opps=0.0, baseline_opps=baseline.opps,
        confidence=confidence, estimate=estimate,
    )


def _one_per_decision(found: list[Adjustment]) -> list[Adjustment]:
    """Keep the clearest view of each decision, drop the other sides of it.

    Keyed by table size as well as decision: "heads-up he will not fold to you"
    and "six-handed he folds normally" are two facts about one player, and
    collapsing them to one loses whichever is second."""
    seen: set[tuple[str, str, str]] = set()
    out = []
    for adjustment in found:                    # widest gap first
        decision, street = _decision(adjustment.stat)
        key = (decision, street, adjustment.regime)
        if key in seen:
            continue
        seen.add(key)
        out.append(adjustment)
    return out


def _decision(stat: str) -> tuple[str, str]:
    base, _, street = stat.partition(":")
    return ("vs_bet" if base in ONE_DECISION else base), street


def _sliced_stats(live: dict[str, StatBook]) -> list[str]:
    """The pooled counters that have an against-you slice anywhere."""
    stats = {
        stat[len(VS_HERO):]
        for book in live.values()
        for stat, ratio in book.ratios.items()
        if stat.startswith(VS_HERO) and ratio.opps > 0
    }
    return sorted(stats)


def _adjustment(stat: str, live: dict[str, StatBook], home: str,
                priors: dict[str, tuple[float, float]],
                min_opps: float, min_confidence: float) -> Adjustment | None:
    baseline = _baseline(stat, live.get(home), home, priors)
    if baseline is None:
        return None

    hits = opps = observed = borrowed = 0.0
    for regime, book in live.items():
        slice_ = book.ratios.get(VS_HERO + stat)
        if slice_ is None or slice_.opps <= 0:
            continue
        if regime == home:
            hits += slice_.hits
            opps += slice_.opps
            observed += slice_.opps
            continue
        # Another table size. Its rate does not transfer, but how far he sits
        # from his own baseline there does, so that is what is carried over --
        # discounted, because a related game is not the same game.
        other = _baseline(stat, book, regime, priors)
        if other is None:
            continue
        here = shrink(slice_.hits, slice_.opps, other.value, TRANSLATION_SMOOTHING)
        translated = sigmoid(logit(baseline.value) + logit(here.value) - logit(other.value))
        weight = CROSS_REGIME_DISCOUNT * slice_.opps
        hits += translated * weight
        opps += weight
        observed += slice_.opps
        borrowed += weight

    # Counted on decisions seen, never on the pseudo-counts borrowed from
    # another table size: those arrive already shrunk, and letting them clear
    # the floor would count the same uncertainty twice.
    if observed < min_opps:
        return None

    estimate = shrink(hits, opps, baseline.value, ADJUSTMENT_PRIOR)
    gap = estimate.value - baseline.value
    if abs(gap) < MIN_GAP:
        return None
    # The baseline is treated as fixed here rather than as its own posterior.
    # It is the far thicker sample of the two -- it is every decision the slice
    # is not -- so the uncertainty that decides this is the slice's.
    confidence = (estimate.prob_above(baseline.value) if gap > 0
                  else estimate.prob_below(baseline.value))
    if confidence < min_confidence:
        return None

    return Adjustment(
        stat=stat, regime=home, versus=estimate.value, baseline=baseline.value,
        opps=observed, borrowed_opps=borrowed, baseline_opps=baseline.opps,
        confidence=confidence, estimate=estimate,
    )


def _baseline(stat: str, book: StatBook | None, regime: str,
              priors: dict[str, tuple[float, float]]) -> Estimate | None:
    """Their rate at this table size against everyone who is not you.

    The against-you slice is subtracted out of the pooled counter, which is the
    whole reason this is a separate function and not ``profile.stats[stat]``."""
    if book is None:
        return None
    pooled = book.ratios.get(stat)
    if pooled is None:
        return None
    slice_ = book.ratios.get(VS_HERO + stat)
    hits = pooled.hits - (slice_.hits if slice_ else 0.0)
    opps = pooled.opps - (slice_.opps if slice_ else 0.0)
    if opps < MIN_BASELINE_OPPS:
        return None
    mean, strength = priors.get(stat) or prior_for(stat, regime)
    return shrink(hits, opps, mean, strength)


def unified_read(by_regime: dict[str, StatBook],
                 populations: dict[str, dict] | None = None):
    """A player's pooled profile with the against-you adjustments attached.

    Pooling, picking the prior to shrink against, and hanging the adjustments
    off the result are one sequence, and getting any step of it wrong produces
    a profile that still looks fine -- so it is written out once, here."""
    from .profile import build_unified, primary_regime
    populations = populations or None
    priors = (populations or {}).get(primary_regime(by_regime)) or None
    profile = build_unified(by_regime, priors=priors, populations=populations)
    if profile is not None:
        profile.adjustments = adjustments(by_regime, priors=priors)
    return profile
