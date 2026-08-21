from pathlib import Path

import numpy as np
import pytest

from villain.archetypes import ARCHETYPE_BY_NAME, target_frequency
from villain.parsers import parse_file
from villain.profile import PROFILE_FEATURES, build_profile
from villain.stats import StatBook

FIXTURE = Path(__file__).parent / "data" / "pokernow_sample.json"


@pytest.fixture(autouse=True)
def _reset_db_module_hooks():
    """A definitions rebuild sets a process-wide dirty flag the hosted page
    consumes as `wrote`. Left set, a later GET /api/roster test would claim a
    read had written."""
    from villain import db
    db._CACHE_DIRTY = False
    db.PROGRESS_HOOK = None
    yield
    db._CACHE_DIRTY = False
    db.PROGRESS_HOOK = None


@pytest.fixture(scope="session")
def hands():
    return parse_file(FIXTURE)


@pytest.fixture
def synth_profile():
    """A player who plays exactly like a named archetype, at a given sample size."""
    def build(archetype: str, regime: str = "6max", opps: int = 60, noise: float = 0.0,
              seed: int = 0):
        arch = ARCHETYPE_BY_NAME[archetype]
        rng = np.random.default_rng(seed)
        book = StatBook(player_id=f"synth-{archetype}", name=archetype,
                        regime=regime, hands=opps * 3)
        for feature in PROFILE_FEATURES:
            p = target_frequency(arch, feature, regime)
            if noise:
                p = float(np.clip(p + rng.normal(0, noise), 0.02, 0.97))
            book.ratios[feature].hits = round(p * opps)
            book.ratios[feature].opps = opps
        book.meters["table_size"].add({"hu": 2, "3max": 3, "6max": 6, "full": 9}[regime], 1)
        return build_profile(book)
    return build
