"""Every known player, ranked."""

from __future__ import annotations

from ..db import Store
from .payloads import roster_payload


def leaderboard_payload(store: Store) -> dict:
    """Every known player, ranked.

    Two orderings matter and they are not the same question. Skill answers
    "who is dangerous"; attackable bb/100 answers "who is worth sitting with".
    A competent player with one exploitable habit can be worth more to you than
    a weak player you have barely seen, so both are shown and the table sorts
    on either.
    """
    ranked = roster_payload(store)
    return {"players": sorted(
        ranked, key=lambda r: (0 if r.get("skill_measured") else 1,
                               -(r["skill"] or 0)))}


# ---------------------------------------------------------------------------
# hero: what only your own hand history can show
# ---------------------------------------------------------------------------
# Grading every fold means fitting the population hand-strength model first
# (villain.reads.fit) and walking hero's several thousand hands through the
# 7-card evaluator -- tens of seconds on a database this size, and unchanged
# from one request to the next unless new hands were imported. An in-memory
# cache alone only pays that once *per running server*, and this UI gets
# stopped and restarted often -- so the finished payload (JSON-safe: no
# sklearn object in it) is also persisted next to the database, keyed by hand
# count rather than time, the same as the in-memory layer. The model itself
# is cheap to refit inside one process and expensive to pickle safely across
# versions, so only the memory layer holds it.
