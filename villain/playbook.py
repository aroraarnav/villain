"""What each leak means, in words a player can act on.

:mod:`villain.exploits` decides *whether* a tendency is exploitable. This says
what to do about it. The words live in ``copy/playbook.toml``; this module
loads them and answers one question: which entry covers a leak id.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

COPY = Path(__file__).parent / "copy" / "playbook.toml"

with COPY.open("rb") as _fh:
    _COPY = tomllib.load(_fh)


@dataclass(frozen=True)
class Entry:
    behavior: str
    why: str
    do: str
    dont: str


#: Keyed by ``Rule.id`` in :mod:`villain.exploits`.
PLAYBOOK: dict[str, Entry] = {
    leak_id: Entry(**fields) for leak_id, fields in _COPY["playbook"].items()
}


def entry_for(leak_id: str) -> Entry | None:
    if leak_id in PLAYBOOK:
        return PLAYBOOK[leak_id]
    # Size-bucket / street variants reuse the parent street entry.
    for prefix in ("overfold_flop", "overfold_turn", "overfold_river",
                   "tank_folds", "snap_calls"):
        if leak_id.startswith(prefix + "_"):
            return PLAYBOOK.get(prefix)
    return None
