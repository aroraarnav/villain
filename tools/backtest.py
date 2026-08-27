"""Walk-forward scoring of the tool's own leak predictions.

Every other test in this project asks whether the *classifier* is honest.
This one asks whether the *exploits* are: split a player's hands
chronologically, build a profile and find leaks from the earlier half alone,
then check whether the later half -- hands the tool never saw -- actually
kept doing what the leak said it would. That turns "trust the read" from an
argument about the maths into a scoreboard, and it needs no new modeling:
:func:`villain.exploits.find_leaks` and the stored hand history are all it
uses.

The split is chronological, not interleaved like :mod:`villain.validate`'s.
Interleaving is right for the classifier, which claims nothing about time;
a leak claims something will still be true *later*, so testing it on a
random half of the same stretch of play would not be a walk-forward test at
all.

**The one confound this cannot rule out.** If you act on a flagged leak, the
opponent may adjust. A read that stops holding in the back half can mean the
read was wrong, or it can mean it was right and it worked -- the exploit
taught them to stop leaking. This method cannot tell those apart, and any
report of the numbers has to carry the caveat.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from villain.exploits import find_leaks
from villain.features import record_hands
from villain.profile import build_profile

#: Chronological split point. Walk-forward, not interleaved: a leak claims
#: something about the future, so it has to be tested against it.
TRAIN_SHARE = 0.6

#: Below this many hands in a half, neither building a profile nor checking
#: whether a leak held up means anything.
MIN_HALF_HANDS = 60

#: A held-out street needs at least this many opportunities before "did the
#: leak hold" is worth asking. Lower than MIN_OPPS deliberately -- this is
#: confirmation of an existing read, not the bar for raising one.
MIN_TEST_OPPS = 5

TIER_ORDER = ("strong", "likely", "tentative")


@dataclass
class TierResult:
    tier: str
    hits: int = 0
    total: int = 0

    @property
    def rate(self) -> float | None:
        return self.hits / self.total if self.total else None


@dataclass
class BacktestResult:
    players: int
    tiers: dict[str, TierResult] = field(default_factory=dict)

    def __str__(self) -> str:
        lines = [f"{self.players} players walked forward"]
        for tier in TIER_ORDER:
            t = self.tiers.get(tier)
            if not t or not t.total:
                continue
            lines.append(f"  {tier:10s} {t.hits:3d}/{t.total:<3d}  ({t.rate:.0%} held up)")
        lines.append("")
        lines.append("  A leak 'holds' when the held-out hands still clear the same")
        lines.append("  breakeven line in the same direction. If you played differently")
        lines.append("  because of a leak, a read that stops holding can mean it was")
        lines.append("  wrong -- or that it worked and they adjusted. This cannot tell")
        lines.append("  those apart.")
        return "\n".join(lines)


def _split(hands: list) -> tuple[list, list]:
    hands = sorted(hands, key=lambda h: h.started_at)
    cut = int(len(hands) * TRAIN_SHARE)
    return hands[:cut], hands[cut:]


def score(store, min_hands: int = 2 * MIN_HALF_HANDS) -> BacktestResult | None:
    """Walk every player forward: find leaks from the early hands, check them
    against the late ones."""
    tiers: dict[str, TierResult] = {t: TierResult(t) for t in TIER_ORDER}
    scored_players = 0

    for row in store.players():
        player_id = int(row["id"])
        hands = store.player_hands(player_id)
        if len(hands) < min_hands:
            continue
        train_hands, test_hands = _split(hands)
        if len(train_hands) < MIN_HALF_HANDS or len(test_hands) < MIN_HALF_HANDS:
            continue

        key = str(player_id)
        train_by = record_hands(train_hands).get(key)
        test_by = record_hands(test_hands).get(key)
        if not train_by or not test_by:
            continue
        train_regime, train_book = max(train_by.items(), key=lambda kv: kv[1].hands)
        test_book = test_by.get(train_regime)
        if test_book is None or test_book.hands < MIN_HALF_HANDS:
            continue

        priors = store.fitted_priors(train_regime) or None
        train_profile = build_profile(train_book, priors=priors)
        leaks = find_leaks(train_profile, dedupe=True)
        if not leaks:
            continue

        test_profile = build_profile(test_book, priors=priors)
        scored_this_player = False
        for leak in leaks:
            actual = test_profile.stats.get(leak.stat)
            if actual is None or actual.raw is None or actual.opps < MIN_TEST_OPPS:
                continue
            held = (actual.raw > leak.threshold if leak.direction == "high"
                    else actual.raw < leak.threshold)
            result = tiers.setdefault(leak.tier, TierResult(leak.tier))
            result.total += 1
            result.hits += int(held)
            scored_this_player = True
        scored_players += int(scored_this_player)

    if scored_players == 0:
        return None
    return BacktestResult(players=scored_players, tiers=tiers)


def main(argv: list[str] | None = None) -> int:
    """Walk leaks forward: found early, checked late.

    A research instrument, not part of the product. It lives outside the
    package so it does not ride into the browser inside the wheel."""
    import argparse

    from villain.db import DEFAULT_PATH, Store

    parser = argparse.ArgumentParser(prog="tools/backtest.py", description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args(argv)

    with Store(args.db) as store:
        result = score(store)
    if result is None:
        print("Not enough hands on any player to walk forward. Import more first.")
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
