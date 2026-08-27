"""Move a database between machines, or merge two of them.

The history is the one thing here that cannot be recreated. Everything else --
books, profiles, fitted priors, archetype labels -- is derived and disposable,
which is why a rebuild is always safe. So an export is the hands and nothing
else: replaying them through the ordinary import path reconstructs the rest
exactly, and reconstructing beats copying a cache whose format changes
whenever :data:`villain.db.DEFINITIONS_VERSION` does.

That choice is what makes merging fall out for free. Two people who play the
same home game can each export and import the other's file, and because the
import path is the one that already exists, they get the duplicate detection
(by hand id) and the identity matching (accounts that are one person) that a
normal import gets. Neither needs an account, a server, or to trust anyone
with hands naming players who never agreed to be in a database.

The format is deliberately dull: gzipped JSON lines, a manifest first and one
hand per line after it. It is greppable once decompressed, streams instead of
loading whole, and anything that can read a line of JSON can read it.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .model import Hand, hand_from_dict

#: Bumped only when a reader would get a hand *wrong*, not when one is added.
FORMAT_VERSION = 1

#: Hands per batch handed to the store. Large enough that the per-batch
#: overhead disappears, small enough that a 500k-hand file does not have to
#: fit in memory all at once.
BATCH = 5000


class UnreadableExport(Exception):
    """The file is not a villain export, or is a version we cannot read."""


@dataclass
class ExportReport:
    hands: int = 0
    path: Path | None = None

    def __str__(self) -> str:
        where = f" to {self.path}" if self.path else ""
        return f"Exported {self.hands} hands{where}."


def export_hands(store, path: Path) -> ExportReport:
    """Write every stored hand to ``path`` as a gzipped JSON-lines archive."""
    report = ExportReport(path=path)
    rows = store.conn.execute(
        "SELECT COUNT(*) c FROM hands").fetchone()["c"]
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "villain_export": FORMAT_VERSION,
            "hands": rows,
        }) + "\n")
        # Straight from the payload column: it is already the exact dict
        # hand_to_dict produced on the way in, so a round trip through Hand
        # would only add a chance to lose something.
        for row in store.conn.execute(
                "SELECT payload FROM hands ORDER BY started_at"):
            fh.write(json.dumps(
                json.loads(gzip.decompress(row["payload"]))) + "\n")
            report.hands += 1
    return report


def read_export(path: Path) -> tuple[dict, Iterator[Hand]]:
    """``(manifest, hands)``. The hands are a generator -- the file streams."""
    fh = gzip.open(path, "rt", encoding="utf-8")
    try:
        first = fh.readline()
        manifest = json.loads(first) if first else {}
    except (OSError, ValueError) as exc:
        fh.close()
        raise UnreadableExport(f"{path} is not a villain export") from exc
    if not isinstance(manifest, dict) or "villain_export" not in manifest:
        fh.close()
        raise UnreadableExport(f"{path} is not a villain export")
    version = manifest["villain_export"]
    if version > FORMAT_VERSION:
        fh.close()
        raise UnreadableExport(
            f"{path} was written by a newer villain (format {version}, "
            f"this one reads {FORMAT_VERSION})")

    def hands() -> Iterator[Hand]:
        try:
            for line in fh:
                line = line.strip()
                if line:
                    yield hand_from_dict(json.loads(line))
        finally:
            fh.close()

    return manifest, hands()


def import_export(store, path: Path, report=None):
    """Read an archive into ``store`` through the ordinary import path.

    Duplicate hands and same-person accounts are handled exactly as they are
    for a hand history off disk, because it is the same code doing it. The
    rebuild is deferred to the end: one pass over the merged database rather
    than one per batch."""
    from .db import ImportReport

    report = report or ImportReport()
    _manifest, hands = read_export(path)
    batch: list[Hand] = []
    for hand in hands:
        batch.append(hand)
        if len(batch) >= BATCH:
            store.add_hands(batch, report, defer_rebuild=True)
            batch = []
    if batch:
        store.add_hands(batch, report, defer_rebuild=True)
    store.rebuild_pending()
    return report
