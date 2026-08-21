"""Plain-language definitions for everything the interface shows.

A profiler is useless if the reader has to already know what "fold vs turn bet,
47%, breakeven 40%" implies. The words live in ``copy/glossary.toml`` so the
same ones appear in the UI, in exports and in any future surface, and so a copy
edit is a copy diff. This module loads them and looks them up.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

COPY = Path(__file__).parent / "copy" / "glossary.toml"

with COPY.open("rb") as _fh:
    _COPY = tomllib.load(_fh)

#: stat -> what it counts, what high means, what low means.
STATS: dict[str, dict[str, str]] = _COPY["stats"]

#: Words the interface uses that carry a specific meaning.
TERMS: dict[str, str] = _COPY["terms"]

#: What each rating component measures, and what a good or bad score means.
COMPONENTS: dict[str, dict] = _COPY["components"]

#: How the table-size split is explained.
REGIMES: dict[str, str] = _COPY["regimes"]

#: The same statistics said as something one player does to another.
VERSUS_BEHAVIOR: dict[str, str] = _COPY["versus_behavior"]

#: Above this a component reads as a strength rather than a weakness.
COMPONENT_STRONG = 78.0


def component_entry(name: str) -> dict | None:
    return COMPONENTS.get(name)


def component_reading(name: str, score: float) -> str:
    """The explanation that matches which way the score actually went."""
    entry = COMPONENTS.get(name)
    if not entry:
        return ""
    return entry["high"] if score >= COMPONENT_STRONG else entry["low"]


def component_stats(name: str) -> list[str]:
    entry = COMPONENTS.get(name)
    return list(entry["stats"]) if entry else []


def component_help(name: str) -> str | None:
    """The weakness reading. Kept for the callers that only show weak spots."""
    entry = COMPONENTS.get(name)
    return entry["low"] if entry else None


def stat_help(stat: str) -> dict[str, str] | None:
    """Explanation for a stat, falling back to the street-agnostic version."""
    if stat in STATS:
        return STATS[stat]
    base = stat.rsplit(":", 1)[0]
    return STATS.get(base)


def versus_behavior(stat: str) -> str:
    """How to say ``stat`` as a thing this player does to you."""
    base, _, street = stat.partition(":")
    phrase = VERSUS_BEHAVIOR.get(base)
    if phrase is None:
        return stat
    return phrase.format(street=street) if street else phrase


def payload() -> dict:
    return {"stats": STATS, "terms": TERMS, "regimes": REGIMES}
