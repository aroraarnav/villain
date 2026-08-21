"""Score the classifier against itself, on hands it has not seen.

The prototypes were tuned twice against a simulation that generated players
*from* the prototypes. In that world the model is correctly specified, so the
harness cannot detect prototype misfit and will always endorse more confidence
than the data supports. Hand-labeled players fail differently: six points
cannot constrain a hundred prototype constants, and once they are inside the
tuning loop they stop being a test.

This is the honest alternative and it needs no labels at all. Split a player's
hands into two disjoint halves, build a profile from each, and ask whether the
posterior computed from one half predicts what the other half actually
supports. The target is observable, the split is interleaved so a player who
drifts across sessions is not scored on the drift, and the scorer is a plain
unweighted likelihood so it can never become another tuned knob.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from villain.archetypes import ARCHETYPE_BY_NAME, ARCHETYPES, _log_beta_binomial, match, target_frequency
from villain.profile import PROFILE_FEATURES, build_profile

#: A half needs this many hands before it is worth scoring at all.
MIN_HALF_HANDS = 50

#: Fixed, and deliberately not the matcher's own value: the scorer must not
#: move when the thing being scored moves.
SCORER_CONCENTRATION = 30.0


@dataclass
class Score:
    players: int
    log_loss: float
    accuracy: float
    calibration_error: float
    mean_confidence: float
    agreement: float
    #: Negative log likelihood per opportunity of the held-out half's counts,
    #: under the read taken from the other half. The only figure here that is
    #: not scored against a ground truth built by the function under test.
    predictive_loss: float | None = None

    def __str__(self) -> str:
        return (f"{self.players} players scored on disjoint halves\n"
                f"  log loss                {self.log_loss:.3f}\n"
                f"  top-1 accuracy          {self.accuracy:.3f}\n"
                f"  calibration error       {self.calibration_error:.3f}\n"
                f"  mean stated confidence  {self.mean_confidence:.3f}\n"
                f"  halves agree            {self.agreement:.3f}"
                + (f"\n  held-out predictive loss {self.predictive_loss:.5f}"
                   if self.predictive_loss is not None else ""))


def _halves(store, player_id: int):
    """Two profiles from disjoint halves of a player's actual hands.

    Interleaved rather than cut down the middle, so a player who drifts across
    sessions is not scored on the drift. Splitting the *counts* instead would
    be far cheaper and completely useless: both halves would carry identical
    rates and agree with each other by construction.
    """
    from villain.features import record_hands
    hands = store.player_hands(player_id)
    if len(hands) < 2 * MIN_HALF_HANDS:
        return None
    key = str(player_id)
    out = []
    for parity in (0, 1):
        books = record_hands(hands[parity::2])
        by_regime = books.get(key)
        if not by_regime:
            return None
        regime, book = max(by_regime.items(), key=lambda kv: kv[1].hands)
        priors = store.fitted_priors(regime) or None
        out.append(build_profile(book, priors=priors))
    return out


def _best_supported(profile) -> str:
    """Which archetype this half's raw counts most plainly support.

    Unweighted and undiscounted on purpose. The target has to be independent of
    every constant being tuned, or the harness scores the tuning against itself.

    **It is not, and this is a live bug.** ``target_frequency`` is itself made
    of tuned constants -- the archetype traits and the spread they are
    multiplied by -- so a change to either moves this ground truth and the
    posterior being scored in the same direction. That makes the harness blind
    to exactly the changes it is most needed for: three separate attempts at
    the prototype-reachability problem were "rejected" by numbers that only
    show the two halves agreeing with each other.

    Weighting changes (``IMPORTANCE``, ``CORRELATION_DISCOUNT``,
    ``EVIDENCE_CAP``) do not touch ``target_frequency`` and are still scored
    honestly. Anything that moves a target is not.

    Fixing it needs a target that does not consult the thing being tuned --
    the raw counts against a fixed reference, or a held-out family of features
    scored on the other half.
    """
    best, best_ll = None, -math.inf
    for arch in ARCHETYPES:
        ll = 0.0
        for feature in PROFILE_FEATURES:
            est = profile.stats.get(feature)
            if est is None or not est.opps:
                continue
            target = target_frequency(arch, feature, profile.regime, profile)
            ll += _log_beta_binomial(est.raw * est.opps, est.opps, target,
                                     SCORER_CONCENTRATION)
        if ll > best_ll:
            best, best_ll = arch.name, ll
    return best or "unknown"


def predictive_loss(train, test) -> tuple[float, float]:
    """How well the read from one half predicts the other half's actual counts.

    The metric the label scores cannot be: the thing being predicted is
    observed data, not a label built by the function under test. A change to
    ``target_frequency`` moves the prediction, and the hands decide whether it
    moved the right way -- which is exactly what the label-based score is
    unable to say, since it moves its own ground truth in the same direction.

    Returns ``(negative log likelihood, opportunities)`` so the caller can
    weight per opportunity rather than per player; a player with ten times the
    data should not count ten times as much toward a mean.
    """
    _label, _confidence, mix = match(train)
    weights = [(ARCHETYPE_BY_NAME[name], w) for name, w in mix if w > 1e-4]
    if not weights:
        return 0.0, 0.0
    total, opportunities = 0.0, 0.0
    for feature in PROFILE_FEATURES:
        est = test.stats.get(feature)
        if est is None or not est.opps or est.raw is None:
            continue
        # The mixture's predicted frequency, not the winner's: a 55/45 read is
        # a claim about both, and scoring only the winner throws away the part
        # of the answer that says how sure it was.
        predicted = sum(w * target_frequency(arch, feature, test.regime, test)
                        for arch, w in weights)
        predicted = min(max(predicted, 1e-4), 1 - 1e-4)
        n = est.opps
        made = est.raw * n
        total -= made * math.log(predicted) + (n - made) * math.log(1 - predicted)
        opportunities += n
    return total, opportunities


def score(store, min_hands: int = 2 * MIN_HALF_HANDS) -> Score | None:
    """Score every player with enough hands to halve."""
    losses, hits, confs, agree = [], [], [], []
    pred_loss, pred_opps = 0.0, 0.0
    for row in store.players():
        pair = _halves(store, int(row["id"]))
        if pair is None:
            continue
        a, b = pair
        name, conf, mix = match(a)
        target = _best_supported(b)
        share = dict(mix).get(target, 1e-6)
        losses.append(-math.log(max(share, 1e-6)))
        hits.append(1.0 if name == target else 0.0)
        confs.append(conf)
        agree.append(1.0 if name == match(b)[0] else 0.0)
        for train, test in ((a, b), (b, a)):        # both directions, no waste
            loss, opps = predictive_loss(train, test)
            pred_loss += loss
            pred_opps += opps
    if not losses:
        return None
    n = len(losses)
    acc = sum(hits) / n
    mean_conf = sum(confs) / n
    return Score(players=n, log_loss=sum(losses) / n, accuracy=acc,
                 calibration_error=abs(mean_conf - acc),
                 mean_confidence=mean_conf, agreement=sum(agree) / n,
                 predictive_loss=(pred_loss / pred_opps) if pred_opps else None)


def main(argv: list[str] | None = None) -> int:
    """Score the classifier on hands it has not seen.

    A research instrument, not part of the product: it answers "is the
    labelling any good", which is a question about the tool rather than about
    a player. It lives outside the package so it does not ride into the
    browser inside the wheel.
    """
    import argparse

    from villain.db import DEFAULT_PATH, Store

    parser = argparse.ArgumentParser(prog="tools/validate.py", description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args(argv)

    with Store(args.db) as store:
        result = score(store)
    if result is None:
        print("Not enough hands on any player to split. Import more first.")
        return 1
    print(result)
    print("\n  Stated confidence should track accuracy; the gap between them is\n"
          "  the calibration error. Halves agreeing is reproducibility, not\n"
          "  correctness -- a player can be labeled the same way twice and be\n"
          "  wrong both times.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
