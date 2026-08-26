"""Skill rating.

Results are almost all luck in a few hundred hands, so they carry the
smallest weight (and only after all-in equity). The rest is fundamentals --
distance from competent play -- and exploitability in bb/100. Low confidence
means "unknown", not "average".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .archetypes import ARCHETYPE_BY_NAME, target_frequency
from .exploits import MIN_CONFIDENCE, Leak, find_leaks, leak_family
from .profile import Profile

TIERS = [
    (82, "expert", "Frequencies hold up under pressure; no cheap edge available."),
    (68, "strong", "Solid and hard to attack, with only narrow leaks to work on."),
    (54, "competent", "Understands the game; exploitable in specific, fixable spots."),
    (38, "developing", "Recognizable strategy with leaks that cost real money."),
    (22, "recreational", "Plays by feel; several large and reliable leaks."),
    (0, "beginner", "Fundamental errors on most streets."),
]


def deduped_exploitability(leaks: list[Leak]) -> float:
    """Sum severities after taking the max within each overlapping leak family."""
    best: dict[str, float] = {}
    for leak in leaks:
        key = leak_family(leak.id)
        best[key] = max(best.get(key, 0.0), leak.severity)
    return sum(best.values())


@dataclass
class Component:
    name: str
    score: float          # 0-100
    weight: float
    note: str = ""


@dataclass
class Skill:
    score: float
    tier: str
    blurb: str
    confidence: float
    exploitability: float           # bb/100 available against them
    components: list[Component] = field(default_factory=list)
    winrate_bb100: float | None = None
    adjusted_bb100: float | None = None
    base: float = 50.0              # unshrunk mix, before the sample-size lid
    measured: bool = False          # False below 150 hands -- not a comparison

    @property
    def label(self) -> str:
        if not self.measured:
            return "unknown"
        return f"{self.tier} ({self.score:.0f}/100)"


#: Hands before a rating is allowed to call itself a skill comparison.
#: Below this the number is 50 plus a sample-size lid, and ranking a roster
#: by it ranks who has been measured. Same cliff as ``sample_quality``.
MEASURED_HANDS = 150


def rate(profile: Profile) -> Skill:
    # Same bar the profile list uses. A lower one here made "worth bb/100" on
    # the roster disagree with the exploits on the page for the same player.
    exploitability = deduped_exploitability(find_leaks(profile, min_confidence=MIN_CONFIDENCE))
    components = [
        _preflop_selection(profile),
        _preflop_aggression(profile),
        _postflop_aggression(profile),
        _discipline(profile),
        _showdown_judgement(profile),
        _sizing(profile),
        _exploitability_component(exploitability, profile),
    ]
    components = [c for c in components if c is not None]
    total_weight = sum(c.weight for c in components)
    base = (sum(c.score * c.weight for c in components) / total_weight) if total_weight else 50.0

    adjusted = _adjusted_winrate(profile)
    if adjusted is not None:
        # Results get a light touch and only after equity adjustment: enough to
        # separate two players whose frequencies look identical, never enough
        # to crown whoever ran hottest.
        base = 0.88 * base + 0.12 * _winrate_score(adjusted)

    confidence = _confidence(profile)
    # With little evidence, pull toward the middle rather than announcing that
    # a 20-hand sample plays like an expert. The pulled number is not a skill
    # comparison -- corr(hands, displayed score) was 0.49 on a real pool, and
    # almost all of that was this lid. Below MEASURED_HANDS we refuse the
    # tier rather than printing "competent (52)".
    measured = profile.hands >= MEASURED_HANDS
    score = 50.0 + (base - 50.0) * confidence
    if measured:
        tier, blurb = _tier(score)
    else:
        tier, blurb = ("unknown",
                       "Not enough hands to rate. Pulled to the middle on "
                       "purpose -- this is not a skill comparison.")
    return Skill(
        score=round(score, 1), tier=tier, blurb=blurb, confidence=round(confidence, 2),
        exploitability=round(exploitability, 2), components=components,
        winrate_bb100=profile.winrate_bb100, adjusted_bb100=adjusted,
        base=round(base, 1), measured=measured,
    )


# -- components ----------------------------------------------------------------

def _solid(profile: Profile, feature: str) -> float:
    """What a competent player does with this stat at this table size.

    TAG against *this* field, not the built-in online one. A 28% VPIP in a
    42% home game is a TAG; scoring it against a 15% online target called
    that hand selection bad and dragged every looser regular's rating down
    with it.
    """
    return target_frequency(ARCHETYPE_BY_NAME["tag"], feature, profile.regime, profile)


#: A tolerance may tighten to the pool at most this far. A degenerate band --
#: a stat everyone in a small database happens to agree on -- must not be able
#: to score the whole table zero.
MIN_TOLERANCE_SHARE = 0.35


def _pool_tolerance(profile, stat: str, builtin: float) -> float:
    """The built-in band, tightened to the one players actually occupy.

    The bands were set from poker theory and are two to four times wider than
    the pool's real spread, so a player a full standard deviation from solid
    still scored 96 and every component saturated: postflop aggression had a
    standard deviation of 3.7 around a median of 98, which is not a measure of
    anything. Distance from solid play is still the thing being scored -- this
    only stops the ruler being longer than the room.

    Tightening only. It never widens past the theory band, so nobody is scored
    more leniently than the absolute standard, and it falls back to the built
    in number when there is no pool to ask.
    """
    band = profile.priors.get(f"range:{stat}") if profile is not None else None
    if not band:
        return builtin
    low, high = band
    if high <= low:
        return builtin
    # p2..p98 spans about four standard deviations.
    sd = (high - low) / 4.0
    return max(min(builtin, 2.0 * sd), builtin * MIN_TOLERANCE_SHARE)


def _band_score(value: float, target: float, tolerance: float,
                loose_tolerance: float | None = None) -> float:
    """100 at the target, decaying with distance in units of ``tolerance``.

    ``loose_tolerance`` widens the band on the high side. Several poker errors
    are asymmetric and scoring them symmetrically misreads solid players: being
    tighter than the field costs a little value, while being looser than it
    costs a lot, and the same is not true in reverse.
    """
    span = tolerance if value < target else (loose_tolerance or tolerance)
    z = abs(value - target) / span
    return max(0.0, 100.0 * (1.0 - 0.5 * z * z)) if z < 1.4 else max(0.0, 100.0 - 45.0 * z)


def _preflop_selection(profile: Profile) -> Component | None:
    vpip = profile.get("vpip")
    if vpip is None:
        return None
    # Tighter than the field is a mild error and loose is a large one, so the
    # band is generous below the target and strict above it. A player who folds
    # too much leaves value behind; one who plays everything bleeds it.
    target = _solid(profile, "vpip")
    score = _band_score(vpip, target,
                        tolerance=_pool_tolerance(profile, "vpip", 0.22),
                        loose_tolerance=0.13)
    limp = profile.get("limp")
    note = ""
    if limp is not None and profile.opps("limp") >= 10 and limp > 0.15:
        # Limping is a pure error at every table size, unlike a loose range,
        # which is only an error if it is not backed up after the flop.
        score *= max(0.35, 1.0 - 1.6 * (limp - 0.10))
        note = f"limps {100 * limp:.0f}% of first-in spots"
    return Component("Hand selection", score, 1.1, note)


def _preflop_aggression(profile: Profile) -> Component | None:
    vpip, pfr = profile.get("vpip"), profile.get("pfr")
    if not vpip or pfr is None:
        return None
    ratio = pfr / vpip
    # Solid players raise most of the hands they play. Passive entry is the
    # single most reliable marker of a weak player, and raising *everything*
    # you play is barely an error at all, so the band is one-sided.
    score = _band_score(min(ratio, 1.0), 0.80, tolerance=0.30, loose_tolerance=0.60)
    three_bet = profile.get("three_bet")
    note = f"raises {100 * ratio:.0f}% of hands played"
    if three_bet is not None and profile.opps("three_bet") >= 12 and three_bet < 0.04:
        score *= 0.75
        note += ", almost never three-bets"
    return Component("Preflop aggression", score, 1.2, note)


def _postflop_aggression(profile: Profile) -> Component | None:
    scores, weights = [], []
    for street in ("flop", "turn", "river"):
        value = profile.get(f"aggression:{street}")
        if value is None or profile.opps(f"aggression:{street}") < 8:
            continue
        scores.append(_band_score(
            value, _solid(profile, f"aggression:{street}"),
            _pool_tolerance(profile, f"aggression:{street}", 0.18)))
        weights.append(1.0)
    if not scores:
        return None
    score = sum(s * w for s, w in zip(scores, weights)) / sum(weights)
    return Component("Postflop aggression", score, 1.2)


def _discipline(profile: Profile) -> Component | None:
    """Folding correctly: both over- and under-folding are errors."""
    scores = []
    for street in ("flop", "turn", "river"):
        value = profile.get(f"fold_vs_bet:{street}")
        if value is None or profile.opps(f"fold_vs_bet:{street}") < 8:
            continue
        # Near the breakeven fold frequency is where an opponent has no cheap
        # exploit in either direction.
        scores.append(_band_score(
            value, 0.44, _pool_tolerance(profile, f"fold_vs_bet:{street}", 0.16)))
    if not scores:
        return None
    return Component("Discipline vs bets", sum(scores) / len(scores), 1.4)


def _showdown_judgement(profile: Profile) -> Component | None:
    wsd, wtsd = profile.get("wsd"), profile.get("wtsd")
    if wsd is None or profile.opps("wsd") < 8:
        return None
    # Winning most of the showdowns you reach means you got there with the
    # right hands. Reaching very few is its own error, so wtsd is banded.
    score = _band_score(wsd, _solid(profile, "wsd") + 0.04,
                        _pool_tolerance(profile, "wsd", 0.16))
    if wtsd is not None and profile.opps("wtsd") >= 12:
        score = 0.65 * score + 0.35 * _band_score(
            wtsd, _solid(profile, "wtsd"), _pool_tolerance(profile, "wtsd", 0.10))
    return Component("Showdown judgment", score, 1.0)


def _sizing(profile: Profile) -> Component | None:
    scores, notes = [], []
    open_bb = profile.means.get("open_bb")
    if open_bb and profile.means.get("open_bb#n", 0) >= 8:
        scores.append(_band_score(open_bb, 2.7, 1.0))
        if open_bb > 4.0:
            notes.append(f"opens {open_bb:.1f}bb")
    for street in ("flop", "turn", "river"):
        sd = profile.means.get(f"bet_size:{street}#sd")
        n = profile.means.get(f"bet_size:{street}#n", 0)
        if sd is not None and n >= 8:
            # One size for every situation is readable; some spread is a sign
            # the player is choosing sizes for a reason.
            scores.append(min(100.0, 40.0 + 400.0 * sd))
            if sd < 0.05:
                notes.append(f"one size on the {street}")
    if not scores:
        return None
    return Component("Bet sizing", sum(scores) / len(scores), 0.7, ", ".join(notes))


def _exploitability_component(exploitability: float, profile: Profile) -> Component:
    """Money available against them, mapped onto the same 0-100 scale.

    Weighted by sample size, because "no leaks found" and "no leaks yet
    findable" are the same number here and only one of them is a compliment.
    """
    # The divisor is calibrated against real bb/100: a leak worth ~3bb/100 (the
    # "big" band in exploits.py) lands near 75, ~9 near 50, ~30 near 25. It was
    # 4.0 when severities were accidentally computed as bb per *hand*, i.e. a
    # hundred times too small, which pinned essentially every player at 100.
    score = 100.0 / (1.0 + max(0.0, exploitability) / 9.0)
    # Deliberately no longer the heaviest component. With severities computed
    # correctly this score swings the full 0-100 while the fundamentals sit in
    # a 35-100 band, so equal weight already gives it more pull -- and its
    # coverage is one-sided: the rules that price *passive* errors clear the
    # evidence bar far more easily than the ones that price aggression, whose
    # stats are thinner (two are showdown-only). A measure that can see one
    # kind of mistake better than the other should inform the rating, not
    # decide it.
    weight = 1.2 * min(1.0, profile.hands / 150.0)
    if exploitability:
        note = f"~{exploitability:.1f} bb/100 available against them"
    else:
        note = "no leak clears the evidence bar yet"
    return Component("Resistance to exploitation", score, round(weight, 2), note)


# -- results and confidence ----------------------------------------------------

def _adjusted_winrate(profile: Profile) -> float | None:
    """Winrate in bb/100 with all-in pots scored by equity, heavily shrunk."""
    net = profile.means.get("net_bb")
    hands = profile.means.get("net_bb#n", 0)
    if net is None or hands < 20:
        return None
    total = net * hands
    ev_n = profile.means.get("ev_net_bb#n", 0)
    if ev_n:
        # Swap the realized result of all-in pots for their equity, so a
        # cooler and a punt stop looking alike.
        realized = profile.means.get("allin_realised_bb", 0.0) * ev_n
        expected = profile.means.get("ev_net_bb", 0.0) * ev_n
        total += expected - realized
    # Shrink hard toward breakeven: 200 hands says almost nothing about winrate.
    prior_hands = 800.0
    return round(100.0 * total / (hands + prior_hands), 2)


def _winrate_score(bb100: float) -> float:
    return max(0.0, min(100.0, 50.0 + 2.5 * bb100))


def _confidence(profile: Profile) -> float:
    """0-1, from sample size and how much of the profile is real data."""
    from .profile import PROFILE_FEATURES
    measured = [profile.stats[f].weight for f in PROFILE_FEATURES if f in profile.stats]
    coverage = sum(measured) / len(PROFILE_FEATURES) if measured else 0.0
    volume = profile.hands / (profile.hands + 250.0)
    return round(min(1.0, 0.45 * coverage + 0.55 * volume) ** 0.75, 3)


def _tier(score: float) -> tuple[str, str]:
    for threshold, name, blurb in TIERS:
        if score >= threshold:
            return name, blurb
    return TIERS[-1][1], TIERS[-1][2]


#: A component this far below the middle is worth naming even when no
#: statistical test clears. It is not a read, it is a description of where
#: their game is thinnest, and it is what the rating is already built on.
WEAK_COMPONENT = 78.0


def weaknesses(skill: Skill, limit: int = 3) -> list[Component]:
    """The weakest parts of a player's game, weakest first.

    Separate from leaks on purpose. A leak is a frequency you can attack for a
    known price; this is where somebody is simply worse, which may or may not
    be exploitable but is always the reason their rating is what it is. Without
    it a player rated 68 with no leaks listed looks identical to one rated 90.
    """
    rated = [c for c in skill.components
             if c.weight > 0 and c.name != "Resistance to exploitation"]
    weak = [c for c in sorted(rated, key=lambda c: c.score) if c.score < WEAK_COMPONENT]
    return weak[:limit]


def leaderboard(profiles: list[Profile]) -> list[Profile]:
    """Players sorted by the unshrunk rating, unmeasured samples last.

    Ranking by the displayed (pulled) score ranked sample size. Ranking by
    ``base`` once there are enough hands is the comparison the number claims
    to be; everyone below :data:`MEASURED_HANDS` goes to the bottom rather
    than clustering at 50.
    """
    for p in profiles:
        if p.skill is None:
            p.skill = rate(p)
    return sorted(profiles, key=lambda p: (
        0 if p.skill.measured else 1, -p.skill.base, -p.skill.confidence))
