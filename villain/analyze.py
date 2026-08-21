"""One place where a profile becomes a finished read.

``build_profile`` produces numbers; the archetype, the leaks and the rating are
separate steps that every consumer needs and that each used to run on its own.
Doing it here means the terminal report, the JSON export and anything written
later cannot disagree about what a player is.
"""

from __future__ import annotations

from .archetypes import ARCHETYPE_BY_NAME, match
from .exploits import find_leaks, find_watchlist
from .glossary import component_entry, component_help, component_reading, component_stats, versus_behavior
from .playbook import combinations_for
from .priors import REGIME_LABELS
from .profile import Profile
from .skill import WEAK_COMPONENT, rate, weaknesses
from .stats import VS_HERO

#: Internal counters. They exist so aggression frequencies can be derived from
#: raw action mixes; as standalone frequencies they mean nothing, so they are
#: kept out of anything user-facing. Timing shares/outcomes are rendered by
#: the timing-tell grid, not the raw stats table.
INTERNAL_PREFIXES = ("act:", "seat:", "saw:", "pace:", "timed:", "after:")


def enrich(profile: Profile) -> Profile:
    """Attach archetype, leaks and rating. Idempotent."""
    if profile.archetype == "unknown":
        profile.archetype, profile.archetype_confidence, profile.archetype_mix = match(profile)
    if not profile.tags:
        # Collapse overlapping families so the UI does not stack overfold_flop
        # with overfold_flop_big and overfold_cbet as three separate leaks.
        profile.tags = find_leaks(profile, dedupe=True)
    if profile.skill is None:
        profile.skill = rate(profile)
    return profile


def is_public(stat: str) -> bool:
    return not stat.startswith(INTERNAL_PREFIXES)


def as_dict(profile: Profile) -> dict:
    """Machine-readable profile, for feeding a solver or a spreadsheet."""
    enrich(profile)
    return {
        "player_id": profile.player_id,
        "name": profile.name,
        "regime": profile.regime,
        "table_mix": profile.table_mix,
        "contributions": profile.contributions,
        "hands": profile.hands,
        "sample_quality": profile.sample_quality,
        "first_seen": profile.first_seen,
        "last_seen": profile.last_seen,
        "archetype": profile.archetype,
        "archetype_confidence": profile.archetype_confidence,
        "archetype_mix": profile.archetype_mix,
        "skill": {
            "score": profile.skill.score,
            "base": profile.skill.base,
            "measured": profile.skill.measured,
            "tier": profile.skill.tier,
            "confidence": profile.skill.confidence,
            "exploitability_bb100": profile.skill.exploitability,
            "components": [
                {"name": c.name, "score": round(c.score, 1), "weight": c.weight,
                 "note": c.note}
                for c in profile.skill.components
            ],
            "observed_bb100": profile.winrate_bb100,
            "adjusted_bb100": profile.skill.adjusted_bb100,
        },
        # What they do well, so a report is not purely a list of faults.
        # Taken from rating components that scored highly.
        "strengths": [
            f"{c.name} scores {c.score:.0f} out of 100"
            + (f" ({c.note})" if c.note else "")
            for c in sorted(profile.skill.components, key=lambda c: -c.score)
            if c.score >= 70 and c.name != "Resistance to exploitation"
        ][:4],
        "stats": {
            stat: {"value": round(est.value, 4), "opportunities": est.opps,
                   "raw": None if est.raw is None else round(est.raw, 4)}
            for stat, est in sorted(profile.stats.items()) if is_public(stat)
        },
        "leaks": [
            {"id": l.id, "headline": l.headline, "severity_bb100": round(l.severity, 3),
             "confidence": round(l.confidence, 3), "tier": l.tier,
             "value": round(l.value, 4), "breakeven": round(l.threshold, 4),
             "sample": l.opps, "advice": l.advice, "stat": l.stat,
             "direction": l.direction,
             # the plain-language layer
             "behavior": l.behavior, "why": l.why, "do": l.do, "dont": l.dont,
             "size": l.size, "priority": l.priority, "pressure": l.pressure,
             "in_words": l.in_words}
            for l in profile.tags
        ],
        # Where they treat you differently from everyone else. Never priced:
        # what an adjustment is worth depends on how you were playing when
        # they made it, which is not in the hand history.
        # Who they are on the hands they played against *you*, at the table
        # size the two of you share most. Deliberately absent from the roster:
        # a list of everybody is a list of how they play the field, and mixing
        # the two references in one column is how the field read stopped
        # meaning anything.
        "versus": ({
            "archetype": profile.versus.archetype,
            "confidence": round(profile.versus.confidence, 3),
            "regime": profile.versus.regime,
            "regime_label": profile.versus.regime_label,
            "decisions": round(profile.versus.decisions),
            "mix": [{"archetype": k, "share": round(v, 3)}
                    for k, v in profile.versus.mix[:3]],
        } if getattr(profile, "versus", None) else None),
        "adjustments": [
            {"stat": a.stat, "behavior": versus_behavior(a.stat),
             # The counter the evidence panel opens on: the against-you slice,
             # not its parent, so the hands shown are the ones being described.
             "evidence_stat": VS_HERO + a.stat,
             "versus": round(a.versus, 4), "baseline": round(a.baseline, 4),
             "gap": round(a.gap, 4), "direction": a.direction,
             # Which table size this holds at. A read can run one way heads-up
             # and the other way six-handed, so an unlabeled pair of them reads
             # as the tool contradicting itself.
             "regime": a.regime, "regime_label": REGIME_LABELS.get(a.regime, a.regime),
             "sample": a.opps, "baseline_sample": a.baseline_opps,
             "confidence": round(a.confidence, 3)}
            for a in getattr(profile, "adjustments", [])
        ],
        # Deviations that are probably real but not yet worth acting on.
        # Never priced: not confident enough to say what they are worth.
        "watchlist": [
            {"id": l.id, "headline": l.headline, "value": round(l.value, 4),
             "breakeven": round(l.threshold, 4), "sample": l.opps,
             "confidence": round(l.confidence, 3), "in_words": l.in_words,
             "do": l.do, "stat": l.stat, "confirms_in": l.confirms_in}
            for l in find_watchlist(profile)
        ],
        # Where their game is thinnest. This is what the rating is built on,
        # so a player rated poorly always has something to show even when no
        # frequency clears a statistical test.
        # Every component carries what it measures and the figure behind it, so
        # the breakdown can explain itself in place instead of showing seven
        # bars whose numbers all sit between 77 and 100.
        "skill_components": [
            {"name": c.name, "score": round(c.score, 1), "note": c.note,
             "weight": c.weight,
             "measures": (component_entry(c.name) or {}).get("measures", ""),
             "meaning": component_reading(c.name, c.score),
             # Only stats the evidence viewer can actually resolve. Aggression
             # is derived from the raw action mix rather than stored as its own
             # ratio, so replaying hands for it finds nothing and the panel
             # opens on "0 of 0".
             # Offer the link only when there is something behind it: the
             # opportunity count is not enough, because a stat the player never
             # once did opens an empty panel.
             "stats": [st for st in component_stats(c.name)
                       if profile.opps(st) and not st.startswith("aggression:")
                       # At least a handful of actual instances: PlayerG limps
                       # 4 times in 1841 hands, which is nonzero and is not
                       # evidence of anything.
                       # Between a handful and a reviewable list. VPIP has
                       # thousands of instances and no single hand tells you
                       # anything: "here are 1,053 hands where they played" is
                       # not evidence, it is the denominator.
                       # At least one real instance. The bar used to be five,
                       # which hid exactly the informative cases -- 4 limps in
                       # 2,945 hands is a read ("essentially never"), and the
                       # panel now says so in words. The upper cap stays: a
                       # list of 2,000 hands is a denominator, not evidence.
                       and 1 <= (profile.stats[st].raw or 0) * profile.opps(st) <= 150],
             "weak": c.score < WEAK_COMPONENT and c.name != "Resistance to exploitation"}
            for c in profile.skill.components
        ],
        "weak_spots": [
            {"name": c.name, "score": round(c.score, 1), "note": c.note,
             "meaning": component_help(c.name) or ""}
            for c in weaknesses(profile.skill)
        ],
        # Leaks that compound. Two tendencies pointing the same way call for a
        # more aggressive adjustment than either would on its own.
        "combinations": [
            {"headline": c.headline, "body": c.body, "leaks": sorted(c.leaks)}
            for c in combinations_for(l.id for l in profile.tags)
        ],
        "plan": (ARCHETYPE_BY_NAME[profile.archetype].plan
                 if profile.archetype in ARCHETYPE_BY_NAME else ""),
    }
