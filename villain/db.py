"""Persistence: hands in, profiles out, forever.

Two decisions shape this module.

**Hands are the source of truth, statistics are a cache.** Stat definitions
change -- a c-bet gets redefined, a new leak rule needs a counter nobody was
recording -- so every hand is stored in canonical form and ``rebuild()``
recomputes every book from scratch. Without that, a definition change leaves
old players wrong until they happen to sit down again.

**Identity is separate from account.** The same human is ``PlayerK`` at one
table and ``PlayerK2`` at the next, and the profile is worthless if it
restarts each time. So site accounts are *aliases* pointing at an internal
player, aliases can be merged, and merging is guarded by co-occurrence: two
accounts dealt into the same hand are provably different people and can never
be linked, however similar their names look.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .dynamics import adjustments, versus_read
from .features import record_hands
from .model import Hand, hand_from_dict, hand_to_dict
from .stats import VS_HERO, Meter, Ratio, StatBook

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT NOT NULL,
    created_at   INTEGER NOT NULL,
    notes        TEXT DEFAULT ''
);
-- One row per site account. Several may point at the same player.
-- ``account`` is normally the site's own player id. When the user says that
-- one account id is being shared by two different people -- a seat handed
-- over, a renamed guest -- the key becomes "<account>#<name>" so the two get
-- separate identities without losing the hands already attributed.
CREATE TABLE IF NOT EXISTS aliases (
    site       TEXT NOT NULL,
    account    TEXT NOT NULL,
    name       TEXT NOT NULL,
    player_id  INTEGER NOT NULL REFERENCES players(id),
    hands      INTEGER NOT NULL DEFAULT 0,
    last_seen  INTEGER,
    PRIMARY KEY (site, account)
);
CREATE INDEX IF NOT EXISTS aliases_player ON aliases(player_id);

-- Accounts dealt into the same hand. Normally proof of two different people,
-- but the count matters: a reconnect can leave a stale seat for a hand or two,
-- so a single overlap across hundreds of hands is usually an artifact rather
-- than evidence. See SPURIOUS_OVERLAP.
CREATE TABLE IF NOT EXISTS distinct_pairs (
    a INTEGER NOT NULL, b INTEGER NOT NULL,
    hands INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (a, b)
);

CREATE TABLE IF NOT EXISTS hands (
    hand_id    TEXT PRIMARY KEY,
    site       TEXT NOT NULL,
    table_id   TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    players    INTEGER NOT NULL,
    payload    BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS hands_time ON hands(started_at);

-- Which accounts sat in which hand. Without it a targeted rebuild still had to
-- gunzip and JSON-decode every hand in the database to find out whether it
-- involved the players being rebuilt, so the cost of importing scaled with the
-- size of the database rather than the size of the import.
CREATE TABLE IF NOT EXISTS hand_seats (
    hand_id  TEXT NOT NULL,
    site     TEXT NOT NULL,
    account  TEXT NOT NULL,
    name     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (hand_id, account)
);
CREATE INDEX IF NOT EXISTS hand_seats_account ON hand_seats(site, account);

CREATE TABLE IF NOT EXISTS books (
    player_id  INTEGER NOT NULL REFERENCES players(id),
    regime     TEXT NOT NULL,
    hands      INTEGER NOT NULL DEFAULT 0,
    first_seen INTEGER, last_seen INTEGER,
    PRIMARY KEY (player_id, regime)
);
CREATE TABLE IF NOT EXISTS ratios (
    player_id INTEGER NOT NULL, regime TEXT NOT NULL, stat TEXT NOT NULL,
    hits REAL NOT NULL, opps REAL NOT NULL,
    PRIMARY KEY (player_id, regime, stat)
);
CREATE TABLE IF NOT EXISTS meters (
    player_id INTEGER NOT NULL, regime TEXT NOT NULL, stat TEXT NOT NULL,
    n REAL NOT NULL, total REAL NOT NULL, sumsq REAL NOT NULL,
    PRIMARY KEY (player_id, regime, stat)
);
-- Priors re-estimated from this database's own players, which is what makes a
-- home game stop being measured against an online population.
CREATE TABLE IF NOT EXISTS fitted_priors (
    regime TEXT NOT NULL, stat TEXT NOT NULL,
    mean REAL NOT NULL, strength REAL NOT NULL, players INTEGER NOT NULL,
    fitted_at INTEGER NOT NULL,
    -- Between-player spread of this stat in log-odds, measured directly from
    -- how far apart the players actually are. The mean was fitted per regime
    -- while the spread an archetype's deviation is multiplied by stayed a
    -- built-in constant, and on a real pool the two disagree by 2x on every
    -- postflop feature -- which put station, maniac, nit and trapper outside
    -- anything a human in the pool does.
    spread REAL,
    -- The range players actually occupy, as robust percentiles. An
    -- archetype target outside it asks for a frequency nobody posts,
    -- which is how station, maniac, nit and trapper became unreachable.
    floor REAL, ceiling REAL,
    PRIMARY KEY (regime, stat)
);
CREATE TABLE IF NOT EXISTS notes (
    player_id INTEGER NOT NULL REFERENCES players(id),
    created_at INTEGER NOT NULL,
    body TEXT NOT NULL
);
-- Bumped whenever feature extraction or displayed-stat definitions change so
-- open() can rebuild stale caches instead of serving silent holes.
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DEFAULT_PATH = Path.home() / ".villain" / "villain.db"

#: Shared hands that can be waved away as one person on two accounts -- a
#: reconnect leaving a stale seat, or somebody sitting down again from a phone
#: for a few hands. Above this, two accounts really were at the table together
#: and cannot be one person. At or below it the merge is offered, with the
#: overlap stated, and a merge is never applied without being asked for.
#:
#: Fitted to a real pool rather than assumed. Over 1,588 pairs of players who
#: were ever dealt in together, the shared-hand counts are:
#:
#:     1 hand    37 pairs        11-25    143
#:     2         9               26-100   547
#:     3-5      34               100+     769
#:     6-10     49
#:
#: 83% of pairs share 26 or more hands, which is a session played together and
#: nothing else. The thin tail below ten is where brief double-seating lives,
#: so the line goes there. It was 2, which refused the ordinary case of one
#: person reconnecting mid-orbit and playing a few hands as both accounts.
#:
#: Raising it is also safer than it was: a merge can now be undone from the
#: player page one account at a time, so a wrong answer is no longer permanent.
SPURIOUS_OVERLAP = 10

#: Feature / display-stat definition stamp. Bump when ``rebuild`` is required
#: for existing databases to grow new counters or fix old ones.
DEFINITIONS_VERSION = "2026-08-19.after-call-and-board-height"


#: Prefix for a seat whose account resolves to no player -- the person was
#: deleted, but the hand they sat in is still the source of truth for everyone
#: else at that table. A site account can be any string, so the marker has to be
#: something a real (integer) player id can never start with.
UNATTRIBUTED = "?"


def split_key(account: str, name: str) -> str:
    """Alias key for an account id the user has split between two people."""
    return f"{account}#{name.strip().lower()}"


def alias_key(site: str, account: str, name: str,
              name_splits: set[tuple[str, str, str]]) -> str:
    return split_key(account, name) if (site, account, name) in name_splits else account


@dataclass
class ImportReport:
    files: int = 0
    hands_seen: int = 0
    hands_new: int = 0
    duplicates: int = 0
    unusable: int = 0            # stored, but no statistics could be extracted
    players_new: int = 0
    #: Accounts folded into another player as reconnects of one person.
    merged_accounts: int = 0
    players: dict[str, int] = field(default_factory=dict)   # display name -> hands added

    def __str__(self) -> str:
        text = (f"{self.hands_new} new hands from {self.files} file(s) "
                f"({self.duplicates} already known), {self.players_new} new player(s)")
        if self.merged_accounts:
            text += f", {self.merged_accounts} account(s) merged as reconnects"
        if self.unusable:
            # An import that yields no statistics must not look like a success.
            text += (f"\n  {self.unusable} hand(s) could not be read and "
                     "produced no statistics")
        return text


class Store:
    """A villain database. Safe to open repeatedly; migrations are idempotent."""

    def __init__(self, path: Path | str = DEFAULT_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        # WAL lets the CLI and UI read while the other writes; without it a
        # concurrent open surfaces as a bare "database is locked" 500.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        self._pending = set()
        self._migrate()
        self._ensure_definitions()
        self.conn.commit()

    #: Players whose books a deferred import still owes a rebuild for.
    _pending: set[int]

    def _migrate(self) -> None:
        """Idempotent schema catch-up for databases made by earlier versions."""
        columns = {r["name"] for r in
                   self.conn.execute("PRAGMA table_info(distinct_pairs)")}
        if "hands" not in columns:
            # Old rows recorded only that a pair had met, not how often. One is
            # the honest floor: it is the least the overlap can have been.
            self.conn.execute(
                "ALTER TABLE distinct_pairs ADD COLUMN hands INTEGER NOT NULL DEFAULT 1")
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(fitted_priors)")}
        for col in ("spread", "floor", "ceiling"):
            if col not in cols:
                self.conn.execute(f"ALTER TABLE fitted_priors ADD COLUMN {col} REAL")
        self._repair_distinct_pairs()
        self._backfill_hand_seats()

    def _backfill_hand_seats(self) -> None:
        """Populate the seat index for hands stored before it existed."""
        have = self.conn.execute("SELECT COUNT(*) c FROM hand_seats").fetchone()["c"]
        if have:
            return
        rows = []
        for row in self.conn.execute("SELECT hand_id, site, payload FROM hands"):
            data = json.loads(gzip.decompress(row["payload"]))
            for seat in data.get("seats") or []:
                rows.append((row["hand_id"], row["site"],
                             seat.get("player_id") or "", seat.get("name") or ""))
        if rows:
            self.conn.executemany(
                "INSERT OR IGNORE INTO hand_seats (hand_id, site, account, name)"
                " VALUES (?, ?, ?, ?)", rows)

    def _ensure_definitions(self) -> None:
        """Rebuild cached books when feature definitions moved under them."""
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = 'definitions_version'").fetchone()
        current = row["value"] if row else None
        if current == DEFINITIONS_VERSION:
            return
        n_hands = self.conn.execute("SELECT COUNT(*) AS c FROM hands").fetchone()["c"]
        if n_hands:
            self.rebuild()
            # The fitted population is a cache too, and it was not covered here.
            # A definitions bump that adds a feature refreshed the stat books
            # but left the priors without it, so the new feature silently fell
            # back to the built-in online default -- measuring a home game
            # against a field it does not play in. Adding raise_share this way
            # changed 26 of 68 real labels before anyone ran `villain fit`.
            # Refitting alone is enough: the prior is applied when a profile is
            # read, not stored in the books, so no second rebuild is needed.
            self.fit_priors()
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('definitions_version', ?)",
            (DEFINITIONS_VERSION,))

    def _repair_distinct_pairs(self) -> None:
        """Restore the ``a < b`` invariant that a past merge could break.

        Earlier versions re-pointed these rows with a bare UPDATE, which could
        leave ``a > b``. Such a row is invisible to :meth:`shared_hands`, which
        looks the pair up sorted -- so a constraint saying two accounts were
        dealt in together silently stopped applying, and the merge it was there
        to prevent became possible. Rows left pointing at a player that no
        longer exists go too; they can never match anything and only confuse a
        later repair.
        """
        live = {r["id"] for r in self.conn.execute("SELECT id FROM players")}
        rows = self.conn.execute("SELECT a, b, hands FROM distinct_pairs").fetchall()
        fixed: dict[tuple[int, int], int] = {}
        for row in rows:
            a, b, hands = int(row["a"]), int(row["b"]), int(row["hands"])
            if a == b or a not in live or b not in live:
                continue
            key = (min(a, b), max(a, b))
            fixed[key] = fixed.get(key, 0) + hands
        if len(fixed) == len(rows) and all(
                int(r["a"]) < int(r["b"]) for r in rows):
            return                          # already clean; leave it alone
        self.conn.execute("DELETE FROM distinct_pairs")
        self.conn.executemany(
            "INSERT INTO distinct_pairs (a, b, hands) VALUES (?, ?, ?)",
            [(a, b, n) for (a, b), n in fixed.items()])

    def close(self) -> None:
        # The .db file is what we copy, export, and (in the browser) upload.
        # WAL left sitting beside it is a second file nobody copies, so a
        # checkpoint here is what makes the one file actually complete.
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Roll back on the way out of a failed block. Committing regardless
        # meant a parse failure halfway through ``villain import`` left the
        # files before it, and half of the one that broke, permanently stored.
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        self.close()

    # -- identity --------------------------------------------------------

    def player_for(self, site: str, account: str, name: str,
                   alias_key: str | None = None) -> int:
        """Internal player id for a site account, creating one if needed.

        ``alias_key`` overrides the storage key, which is how a shared account
        id gets split into two identities.
        """
        key = alias_key or account
        row = self.conn.execute(
            "SELECT player_id FROM aliases WHERE site = ? AND account = ?",
            (site, key),
        ).fetchone()
        if row:
            return int(row["player_id"])
        cur = self.conn.execute(
            "INSERT INTO players (display_name, created_at) VALUES (?, ?)",
            (name or account, int(time.time())),
        )
        player_id = int(cur.lastrowid)
        self.conn.execute(
            "INSERT INTO aliases (site, account, name, player_id) VALUES (?, ?, ?, ?)",
            (site, key, name or account, player_id),
        )
        return player_id

    def mark_distinct(self, player_ids: Iterable[int]) -> None:
        """Count a hand in which these players were seated together."""
        ids = sorted(set(player_ids))
        pairs = [(a, b) for i, a in enumerate(ids) for b in ids[i + 1:]]
        if pairs:
            self.conn.executemany(
                "INSERT INTO distinct_pairs (a, b, hands) VALUES (?, ?, 1) "
                "ON CONFLICT(a, b) DO UPDATE SET hands = hands + 1", pairs)

    def shared_hands(self, a: int, b: int) -> int:
        """How many hands these two were dealt into together."""
        lo, hi = sorted((a, b))
        row = self.conn.execute(
            "SELECT hands FROM distinct_pairs WHERE a = ? AND b = ?", (lo, hi)).fetchone()
        return int(row["hands"]) if row else 0

    def are_distinct(self, a: int, b: int) -> bool:
        """True when the overlap is too large to be a glitch."""
        return self.shared_hands(a, b) > SPURIOUS_OVERLAP

    def link(self, keep: int, absorb: int, rebuild: bool = True) -> None:
        """Declare two players the same human, folding ``absorb`` into ``keep``.

        ``rebuild=False`` skips recomputing the surviving player's books, for
        callers merging many accounts at once. One rebuild re-extracts every
        hand the player appears in, so doing it per link made a 36-account
        reconnect run re-read the same 7,000 hands thirty-five times; the
        caller is expected to rebuild once when it is done.
        """
        if keep == absorb:
            return
        overlap = self.shared_hands(keep, absorb)
        if overlap > SPURIOUS_OVERLAP:
            # By name, not by internal id. "players 41 and 118" told the reader
            # nothing about who the tool was refusing to merge, or why they
            # should believe it.
            names = {
                int(r["id"]): (r["display_name"] or f"player {r['id']}")
                for r in self.conn.execute(
                    "SELECT id, display_name FROM players WHERE id IN (?, ?)",
                    (keep, absorb))}
            raise ValueError(
                f"\u201c{names.get(keep, keep)}\u201d and "
                f"\u201c{names.get(absorb, absorb)}\u201d were dealt into "
                f"{overlap} hands together, so they are two different people")
        self.conn.execute("UPDATE aliases SET player_id = ? WHERE player_id = ?",
                          (keep, absorb))
        self.conn.execute("UPDATE notes SET player_id = ? WHERE player_id = ?",
                          (keep, absorb))
        for table in ("books", "ratios", "meters"):
            self.conn.execute(f"DELETE FROM {table} WHERE player_id IN (?, ?)",
                              (keep, absorb))
        self.conn.execute("DELETE FROM players WHERE id = ?", (absorb,))
        # Inherit the absorbed player's distinctness constraints.
        # Re-point every constraint the absorbed player carried onto ``keep``.
        # A bare UPDATE cannot do this: the table's whole contract is that ``a
        # < b`` (mark_distinct inserts sorted, shared_hands looks up sorted),
        # and renaming one column of a sorted pair can invert it. An inverted
        # row is invisible to shared_hands, which silently drops the constraint
        # -- and that is exactly how two accounts dealt into the same hand
        # became mergeable. UPDATE OR IGNORE also discarded, rather than
        # summed, the overlap when both players already had a row.
        rows = self.conn.execute(
            "SELECT a, b, hands FROM distinct_pairs WHERE a = ? OR b = ?",
            (absorb, absorb)).fetchall()
        self.conn.execute("DELETE FROM distinct_pairs WHERE a = ? OR b = ?",
                          (absorb, absorb))
        for row in rows:
            other = row["b"] if row["a"] == absorb else row["a"]
            if other == keep:
                continue                    # the pair being merged; not a constraint
            lo, hi = sorted((keep, other))
            self.conn.execute(
                "INSERT INTO distinct_pairs (a, b, hands) VALUES (?, ?, ?) "
                "ON CONFLICT(a, b) DO UPDATE SET hands = hands + excluded.hands",
                (lo, hi, int(row["hands"])))
        self.conn.execute("DELETE FROM distinct_pairs WHERE a = b")
        self.conn.commit()
        if rebuild:
            self.rebuild(only=[keep])
        else:
            self._pending.add(keep)

    def unlink(self, player_id: int, site: str, account: str) -> int:
        """Split one alias back onto its own player.

        Merges used to be one-way; undoing a bad link meant deleting the
        database. The hands stay put — only the alias pointer moves — then both
        profiles are rebuilt from the stored hand log.
        """
        row = self.conn.execute(
            "SELECT name FROM aliases WHERE site = ? AND account = ? AND player_id = ?",
            (site, account, player_id)).fetchone()
        if row is None:
            raise LookupError(f"alias {site}/{account} is not on player {player_id}")
        n_aliases = self.conn.execute(
            "SELECT COUNT(*) AS c FROM aliases WHERE player_id = ?",
            (player_id,)).fetchone()["c"]
        if n_aliases < 2:
            raise ValueError("cannot unlink a player's only alias")
        name = row["name"] or account
        cur = self.conn.execute(
            "INSERT INTO players (display_name, created_at) VALUES (?, ?)",
            (name, int(time.time())))
        new_id = int(cur.lastrowid)
        self.conn.execute(
            "UPDATE aliases SET player_id = ? WHERE site = ? AND account = ?",
            (new_id, site, account))
        # They were treated as one person; mark them distinct so a soft name
        # match cannot silently re-merge them without asking.
        lo, hi = sorted((player_id, new_id))
        self.conn.execute(
            "INSERT INTO distinct_pairs (a, b, hands) VALUES (?, ?, ?) "
            "ON CONFLICT(a, b) DO UPDATE SET hands = excluded.hands",
            (lo, hi, SPURIOUS_OVERLAP + 1))
        for table in ("books", "ratios", "meters"):
            self.conn.execute(f"DELETE FROM {table} WHERE player_id IN (?, ?)",
                              (player_id, new_id))
        self.conn.commit()
        self.rebuild(only=[player_id, new_id])
        return new_id

    def players(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT p.id, p.display_name, p.notes,
                      (SELECT group_concat(name, ', ') FROM aliases a
                        WHERE a.player_id = p.id) AS aliases,
                      (SELECT COALESCE(SUM(hands), 0) FROM books b
                        WHERE b.player_id = p.id) AS hands,
                      (SELECT MAX(last_seen) FROM books b
                        WHERE b.player_id = p.id) AS last_seen
                 FROM players p ORDER BY hands DESC"""
        ).fetchall()

    def find_player(self, needle: str) -> list[sqlite3.Row]:
        """Look a player up by display name or any alias, case-insensitively."""
        like = f"%{needle.lower()}%"
        return self.conn.execute(
            """SELECT DISTINCT p.id, p.display_name FROM players p
                 LEFT JOIN aliases a ON a.player_id = p.id
                WHERE LOWER(p.display_name) LIKE ? OR LOWER(a.name) LIKE ?
                   OR LOWER(a.account) = ?""",
            (like, like, needle.lower()),
        ).fetchall()

    # -- hands -----------------------------------------------------------

    def add_hands(self, hands: Iterable[Hand], report: ImportReport | None = None,
                  name_splits: set[tuple[str, str, str]] | None = None,
                  defer_rebuild: bool = False) -> ImportReport:
        """Store hands, skipping any already seen, and update the books.

        ``name_splits`` holds ``(site, account, name)`` triples the user has
        declared to be a *different* person from whoever already owns that
        account id.

        ``defer_rebuild`` records the players this batch touched and returns
        without rebuilding, leaving the caller to call :meth:`rebuild_pending`
        once. A rebuild costs a pass over every hand those players appear in,
        which on a real database is most of it -- so doing one per file turned
        an import of N files into N full rebuilds. Importing a directory is the
        normal case, not the exception.
        """
        report = report or ImportReport()
        fresh: list[Hand] = []
        for hand in hands:
            report.hands_seen += 1
            known = self.conn.execute(
                "SELECT 1 FROM hands WHERE hand_id = ?", (hand.hand_id,)).fetchone()
            if known:
                report.duplicates += 1
                continue
            payload = gzip.compress(json.dumps(hand_to_dict(hand)).encode())
            self.conn.execute(
                "INSERT INTO hands (hand_id, site, table_id, started_at, players, payload)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (hand.hand_id, hand.site, hand.table_id, hand.started_at,
                 len(hand.seats), payload),
            )
            self.conn.executemany(
                "INSERT OR IGNORE INTO hand_seats (hand_id, site, account, name)"
                " VALUES (?, ?, ?, ?)",
                [(hand.hand_id, hand.site, seat.player_id, seat.name or "")
                 for seat in hand.seats])
            fresh.append(hand)
            report.hands_new += 1
            if "pot_mismatch" in hand.flags or hand.big_blind <= 0:
                report.unusable += 1

        touched = self._ingest(fresh, report, name_splits or set())
        self.conn.commit()
        self._pending.update(touched)
        if not defer_rebuild:
            self.rebuild_pending()
        return report

    def rebuild_pending(self) -> int:
        """Rebuild the books for every player added since the last rebuild."""
        pending, self._pending = self._pending, set()
        if not pending:
            return 0
        return self.rebuild(only=sorted(pending))

    def _ingest(self, hands: list[Hand], report: ImportReport,
                name_splits: set[tuple[str, str, str]]) -> set[int]:
        """Register players and aliases for a batch of hands."""
        touched: set[int] = set()
        # One count before and after the whole batch, not two per seat. The
        # per-seat version issued two full-table counts for every seat of every
        # hand -- roughly 900k queries on a 70k-hand import -- to learn a number
        # the difference of two counts gives exactly.
        before_all = self.conn.execute("SELECT COUNT(*) c FROM players").fetchone()["c"]

        # Accumulate in memory and write once. Per-seat and per-hand statements
        # meant roughly two million writes for an 80k-hand import -- an alias
        # UPDATE for every seat of every hand, and a distinct_pairs upsert for
        # every *pair* of seats -- each one walking an index that grows as it
        # goes. The totals are the same either way; only the number of round
        # trips to SQLite changes, and that was the entire cost.
        known: dict[tuple[str, str], int] = {}
        alias_seen: dict[tuple[str, str], tuple[int, int, str]] = {}
        pair_counts: dict[tuple[int, int], int] = {}
        for hand in hands:
            ids = []
            for seat in hand.seats:
                key = alias_key(hand.site, seat.player_id, seat.name, name_splits)
                cache_key = (hand.site, key)
                pid = known.get(cache_key)
                if pid is None:
                    pid = self.player_for(hand.site, seat.player_id, seat.name,
                                          alias_key=key)
                    known[cache_key] = pid
                ids.append(pid)
                touched.add(pid)
                report.players[seat.name] = report.players.get(seat.name, 0) + 1
                count, last, _name = alias_seen.get(cache_key, (0, 0, ""))
                alias_seen[cache_key] = (
                    count + 1, max(last, hand.started_at or 0), seat.name)
            seats = sorted(set(ids))
            for i, a in enumerate(seats):
                for b in seats[i + 1:]:
                    pair_counts[(a, b)] = pair_counts.get((a, b), 0) + 1

        if alias_seen:
            self.conn.executemany(
                "UPDATE aliases SET hands = hands + ?,"
                " last_seen = MAX(COALESCE(last_seen, 0), ?), name = ?"
                " WHERE site = ? AND account = ?",
                [(count, last, name, site, key)
                 for (site, key), (count, last, name) in alias_seen.items()])
        if pair_counts:
            self.conn.executemany(
                "INSERT INTO distinct_pairs (a, b, hands) VALUES (?, ?, ?) "
                "ON CONFLICT(a, b) DO UPDATE SET hands = hands + excluded.hands",
                [(a, b, n) for (a, b), n in pair_counts.items()])

        after_all = self.conn.execute("SELECT COUNT(*) c FROM players").fetchone()["c"]
        report.players_new += after_all - before_all
        return touched

    def stored_hands(self, player_id: int | None = None) -> list[Hand]:
        """Stored hands exactly as recorded, with raw site account ids.

        Deliberately *not* re-keyed: the hands are the source of truth, so a
        merge must not rewrite what was recorded, and a test enforces it. Any
        caller that wants to line these up with per-player statistics has to
        resolve the ids itself -- :meth:`player_hands` does, and anything
        joining hands to a player id should follow it rather than this.
        """
        if player_id is None:
            rows = self.conn.execute(
                "SELECT payload FROM hands ORDER BY started_at").fetchall()
        else:
            accounts = self.conn.execute(
                "SELECT site, account FROM aliases WHERE player_id = ?", (player_id,)
            ).fetchall()
            keys = {(r["site"], r["account"]) for r in accounts}
            rows = [
                r for r in self.conn.execute(
                    "SELECT site, payload FROM hands ORDER BY started_at").fetchall()
                if any((r["site"], seat["player_id"]) in keys
                       or (r["site"], split_key(seat["player_id"], seat["name"])) in keys
                       for seat in json.loads(gzip.decompress(r["payload"]))["seats"])
            ]
        return [hand_from_dict(json.loads(gzip.decompress(r["payload"]))) for r in rows]

    # -- books -----------------------------------------------------------

    def rebuild(self, only: list[int] | None = None) -> int:
        """Recompute books from stored hands. The cache is always disposable.

        When ``only`` is set, hands that never seat those players are skipped so
        a single-player rebuild (e.g. after a merge) does not rescan the whole
        database through the feature pipeline.
        """
        alias_rows = list(self.conn.execute(
            "SELECT site, account, player_id FROM aliases"))
        alias_map = {(r["site"], r["account"]): int(r["player_id"]) for r in alias_rows}

        def resolve(site: str, account: str, name: str) -> int | None:
            """Split key first, then the bare account.

            Order matters: ``"<account>#<name>"`` is the more specific claim.
            Checking the bare account first would hand every split hand back to
            whoever owned the account originally, which is exactly the merge
            the user declined.
            """
            hit = alias_map.get((site, split_key(account, name)))
            if hit is not None:
                return hit
            return alias_map.get((site, account))

        wanted = {int(p) for p in only} if only else None
        wanted_keys = None
        if wanted is not None:
            wanted_keys = {(r["site"], r["account"]) for r in alias_rows
                           if int(r["player_id"]) in wanted}

        names: dict[str, str] = {}
        hands: list[Hand] = []
        query = "SELECT site, payload FROM hands ORDER BY started_at"
        params: tuple = ()
        if wanted_keys is not None:
            # Pick the hands out of the index first. The seat table is small and
            # uncompressed, so this replaces a full-database decompression with
            # one cheap scan plus a lookup of just the hands that matter.
            wanted_ids = {
                row["hand_id"]
                for row in self.conn.execute(
                    "SELECT hand_id, site, account, name FROM hand_seats")
                if (row["site"], row["account"]) in wanted_keys
                or (row["site"], split_key(row["account"], row["name"])) in wanted_keys
            }
            if not wanted_ids:
                return 0
            # Via a temp table, not an IN clause. A bulk import touches every
            # player, so the id list is the whole hands table -- and SQLite
            # caps how many variables one statement may bind, so the IN form
            # failed outright with "too many SQL variables" on exactly the
            # large imports that most need the narrowing.
            self.conn.execute("DROP TABLE IF EXISTS temp.rebuild_ids")
            self.conn.execute(
                "CREATE TEMP TABLE rebuild_ids (hand_id TEXT PRIMARY KEY)")
            self.conn.executemany(
                "INSERT OR IGNORE INTO temp.rebuild_ids (hand_id) VALUES (?)",
                [(h,) for h in wanted_ids])
            query = ("SELECT h.site, h.payload FROM hands h"
                     " JOIN temp.rebuild_ids r ON r.hand_id = h.hand_id"
                     " ORDER BY h.started_at")
        for row in self.conn.execute(query, params):
            data = json.loads(gzip.decompress(row["payload"]))
            hand = hand_from_dict(data)
            # Re-key seats onto internal player ids so merged aliases pool.
            for seat in hand.seats:
                pid = resolve(hand.site, seat.player_id, seat.name)
                if pid is None:
                    # A seat whose account maps to nobody -- the player it
                    # belonged to was deleted. It has to stay in the hand:
                    # everybody else's read depends on how many were dealt in
                    # and what this seat did. It just gets booked to nobody, so
                    # it is keyed with a prefix no player id can collide with
                    # and dropped below. Leaving the raw account here instead
                    # booked stats under a site account string and then blew up
                    # on int() at the write.
                    seat.player_id = UNATTRIBUTED + str(seat.player_id)
                    continue
                names[str(pid)] = seat.name or names.get(str(pid), "")
                seat.player_id = str(pid)
            hands.append(hand)
        # Two-pass timing: freeze each player's snap/tank cutoffs from the
        # full sample, then tag every hand with those same thresholds.
        books = record_hands(hands)

        wanted_str = {str(p) for p in wanted} if wanted is not None else None
        written = 0
        for pid, by_regime in books.items():
            if pid.startswith(UNATTRIBUTED):
                continue                      # seated, counted for others, owned by nobody
            if wanted_str is not None and pid not in wanted_str:
                continue
            self._write_books(int(pid), by_regime, names.get(pid, ""))
            written += 1
        self.conn.commit()
        return written

    def _write_books(self, player_id: int, by_regime: dict[str, StatBook],
                     name: str) -> None:
        for table in ("books", "ratios", "meters"):
            self.conn.execute(f"DELETE FROM {table} WHERE player_id = ?", (player_id,))
        for regime, book in by_regime.items():
            self.conn.execute(
                "INSERT INTO books (player_id, regime, hands, first_seen, last_seen)"
                " VALUES (?, ?, ?, ?, ?)",
                (player_id, regime, book.hands, book.first_seen, book.last_seen))
            self.conn.executemany(
                "INSERT INTO ratios (player_id, regime, stat, hits, opps)"
                " VALUES (?, ?, ?, ?, ?)",
                [(player_id, regime, stat, r.hits, r.opps)
                 for stat, r in book.ratios.items()])
            self.conn.executemany(
                "INSERT INTO meters (player_id, regime, stat, n, total, sumsq)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [(player_id, regime, stat, m.n, m.total, m.sumsq)
                 for stat, m in book.meters.items()])
        if name:
            self.conn.execute(
                "UPDATE players SET display_name = ? WHERE id = ? AND display_name = ''",
                (name, player_id))

    def books_missing(self) -> int:
        """Hands stored with no books built from them.

        Hands are committed before the books are computed, so a rebuild that
        is interrupted -- or an import killed for being slow -- leaves a
        database full of hands that every profile query reads as "no such
        player". That rendered as an empty roster with no error at all, which
        is the worst way for it to present: indistinguishable from a database
        nobody has imported into yet. Callers surface this instead of guessing.
        """
        hands = self.conn.execute("SELECT COUNT(*) c FROM hands").fetchone()["c"]
        if not hands:
            return 0
        books = self.conn.execute("SELECT COUNT(*) c FROM books").fetchone()["c"]
        return hands if not books else 0

    def books(self, player_id: int) -> dict[str, StatBook]:
        """Every regime book for a player, read back out of the cache."""
        name_row = self.conn.execute(
            "SELECT display_name FROM players WHERE id = ?", (player_id,)).fetchone()
        name = name_row["display_name"] if name_row else str(player_id)
        out: dict[str, StatBook] = {}
        for row in self.conn.execute(
                "SELECT regime, hands, first_seen, last_seen FROM books WHERE player_id = ?",
                (player_id,)):
            out[row["regime"]] = StatBook(
                player_id=str(player_id), name=name, regime=row["regime"],
                hands=row["hands"], first_seen=row["first_seen"], last_seen=row["last_seen"])
        for row in self.conn.execute(
                "SELECT regime, stat, hits, opps FROM ratios WHERE player_id = ?",
                (player_id,)):
            book = out.get(row["regime"])
            if book is not None:
                book.ratios[row["stat"]] = Ratio(row["hits"], row["opps"])
        for row in self.conn.execute(
                "SELECT regime, stat, n, total, sumsq FROM meters WHERE player_id = ?",
                (player_id,)):
            book = out.get(row["regime"])
            if book is not None:
                book.meters[row["stat"]] = Meter(row["n"], row["total"], row["sumsq"])
        return out

    def population_samples(self, stat_filter=None) -> dict[str, dict[str, list[tuple[float, float]]]]:
        """Every player's (hits, opps) per stat, per regime -- input to a prior fit."""
        from .profile import DERIVED
        out: dict[str, dict[str, list[tuple[float, float]]]] = defaultdict(lambda: defaultdict(list))
        raw: dict[tuple[str, int], dict[str, tuple[float, float]]] = defaultdict(dict)
        for row in self.conn.execute(
                "SELECT regime, player_id, stat, hits, opps FROM ratios"):
            raw[(row["regime"], row["player_id"])][row["stat"]] = (row["hits"], row["opps"])
            # A vs: counter is one player's behavior against one opponent, so
            # the spread across players measures the opponent as much as the
            # pool. Fitting a population from it would feed that back into
            # everyone's shrinkage.
            if row["stat"].startswith(VS_HERO):
                continue
            if stat_filter and not stat_filter(row["stat"]):
                continue
            out[row["regime"]][row["stat"]].append((row["hits"], row["opps"]))
        # Derived features are assembled from raw action counters and never
        # stored as their own ratio rows, so a prior fit over the ratios table
        # never saw them -- leaving aggression:* (combined importance 4.0, the
        # heaviest block in the matcher) measured against the built-in online
        # defaults no matter how much of your own pool there was to fit.
        for (regime, _pid), stats in raw.items():
            for stat, (num_keys, den_keys) in DERIVED.items():
                if stat_filter and not stat_filter(stat):
                    continue
                den = sum(stats.get(k, (0.0, 0.0))[0] for k in den_keys)
                if den <= 0:
                    continue
                num = sum(stats.get(k, (0.0, 0.0))[0] for k in num_keys)
                out[regime][stat].append((num, den))
        return {r: dict(v) for r, v in out.items()}

    # -- sessions ---------------------------------------------------------

    #: A gap this long between hands starts a new session. Home games run for
    #: hours and reconvene days later, so anything from a couple of hours up
    #: separates them cleanly; four is comfortably inside that margin.
    SESSION_GAP_MS = 4 * 60 * 60 * 1000

    def sessions(self) -> list[dict]:
        """Sittings, derived from gaps between hands rather than stored.

        Nothing records a session id -- the hands are the source of truth and a
        sitting is just a run of them close together in time. Deriving it means
        no migration and no second thing to keep correct.
        """
        rows = list(self.conn.execute(
            "SELECT hand_id, started_at FROM hands ORDER BY started_at"))
        out: list[dict] = []
        for row in rows:
            at = int(row["started_at"] or 0)
            if out and at - out[-1]["ended_at"] <= self.SESSION_GAP_MS:
                cur = out[-1]
                cur["ended_at"] = at
                cur["hands"] += 1
                cur["hand_ids"].append(row["hand_id"])
            else:
                out.append({"started_at": at, "ended_at": at, "hands": 1,
                            "hand_ids": [row["hand_id"]]})
        seats: dict[str, set] = {}
        tables: dict[str, str] = {}
        for row in self.conn.execute(
                "SELECT h.hand_id, h.table_id, s.account FROM hands h"
                " LEFT JOIN hand_seats s ON s.hand_id = h.hand_id"):
            seats.setdefault(row["hand_id"], set()).add(row["account"])
            tables[row["hand_id"]] = row["table_id"]
        for i, sess in enumerate(out):
            sess["id"] = i + 1
            who: set = set()
            for hid in sess["hand_ids"]:
                who |= seats.get(hid, set())
            sess["players"] = len([w for w in who if w])
            sess["table_id"] = tables.get(sess["hand_ids"][0], "")
            sess["minutes"] = round((sess["ended_at"] - sess["started_at"]) / 60000)
        out.reverse()                      # most recent first
        return out

    def session_books(self, hand_ids: list[str]) -> dict:
        """Books built from one sitting's hands only."""
        from .features import record_hands
        from .model import hand_from_dict
        if not hand_ids:
            return {}
        alias_map = {(r["site"], r["account"]): int(r["player_id"])
                     for r in self.conn.execute(
                         "SELECT site, account, player_id FROM aliases")}
        hands = []
        marks = ",".join("?" * len(hand_ids))
        for row in self.conn.execute(
                f"SELECT site, payload FROM hands WHERE hand_id IN ({marks})"
                " ORDER BY started_at", tuple(hand_ids)):
            hand = hand_from_dict(json.loads(gzip.decompress(row["payload"])))
            for seat in hand.seats:
                pid = alias_map.get((hand.site, split_key(seat.player_id, seat.name))) \
                    or alias_map.get((hand.site, seat.player_id))
                if pid is not None:
                    seat.player_id = str(pid)
            hands.append(hand)
        return record_hands(hands)

    #: Only statistics with a per-*hand* denominator are compared session to
    #: session. Street-conditioned ones (fold vs turn bet, say) give a handful
    #: of observations in one sitting, which is enough for a story and not for
    #: a finding.
    SESSION_STATS = ("vpip", "pfr", "three_bet", "limp",
                     "aggression:flop", "aggression:turn", "wtsd")

    #: A sitting needs this many opportunities before it is compared at all,
    #: and the baseline needs this many on top of it.
    SESSION_MIN_OPPS = 20
    SESSION_MIN_BASELINE = 60

    def session_detail(self, session: dict) -> list[dict]:
        """Per player: what they did in this sitting, against their own norm.

        The baseline is the player's other hands, not this sitting's -- comparing
        a session against a total that contains it shrinks every difference
        toward nothing, and the more of their history this sitting is, the more
        it hides.
        """
        from .priors import REGIME_LABELS
        books = self.session_books(session["hand_ids"])
        names = {str(r["id"]): r["display_name"] for r in self.players()}
        out = []
        for pid, by_regime in books.items():
            hands = sum(b.hands for b in by_regime.values())
            # Summed across every table size played this sitting -- unlike
            # the deltas below, a result does not need a same-regime baseline
            # to mean something, it just needs adding up.
            net_bb = sum(b.meters["net_bb"].total for b in by_regime.values()
                        if "net_bb" in b.meters)
            stored = self.books(int(pid))
            deltas = []
            # Compared inside a table size, never across one. 55% VPIP is tight
            # heads-up and wild at a full ring, so pooling the two and reporting
            # the difference measures which table they sat at, not how they
            # played.
            for regime, book in by_regime.items():
                baseline = stored.get(regime)
                if baseline is None:
                    continue
                for stat, ratio in sorted(book.ratios.items()):
                    if stat not in self.SESSION_STATS or not ratio.opps:
                        continue
                    total = baseline.ratios.get(stat)
                    if total is None or ratio.opps < self.SESSION_MIN_OPPS:
                        continue
                    rest_hits = total.hits - ratio.hits
                    rest_opps = total.opps - ratio.opps
                    if rest_opps < self.SESSION_MIN_BASELINE:
                        continue      # no history at this table size to differ from
                    here, usual = ratio.hits / ratio.opps, rest_hits / rest_opps
                    if abs(here - usual) < 0.03:
                        continue      # not a difference, just arithmetic noise
                    deltas.append({"stat": stat, "regime": regime,
                                   "regime_label": REGIME_LABELS.get(regime, regime),
                                   "session": round(here, 4),
                                   "usual": round(usual, 4),
                                   "delta": round(here - usual, 4),
                                   "opps": round(ratio.opps, 1)})
            deltas.sort(key=lambda d: -abs(d["delta"]))
            # A read built from this sitting alone, so the trends sit next to
            # what the player looked like while they were producing them.
            from .archetypes import match
            from .profile import build_profile
            from .skill import rate
            primary = max(by_regime.items(), key=lambda kv: kv[1].hands)[1]
            snap = build_profile(primary, by_regime,
                                 priors=self.fitted_priors(primary.regime) or None)
            snap.skill = rate(snap)
            arch, conf, _mix = match(snap)
            out.append({"player_id": int(pid), "name": names.get(pid, pid),
                        "hands": hands, "net_bb": round(net_bb, 1), "deltas": deltas,
                        "regimes": sorted(by_regime),
                        "archetype": arch, "confidence": round(conf, 3),
                        "skill": round(snap.skill.score, 1),
                        "skill_tier": snap.skill.tier,
                        "sample_quality": snap.sample_quality,
                        "regime_label": snap.regime_label})
        out.sort(key=lambda p: -p["hands"])
        return out

    # -- fitted priors ----------------------------------------------------

    #: Players needed before a spread is trusted, and the range it is held to.
    #: A degenerate fit must not be able to amplify every deviation at once --
    #: the failure that made fitting the spread from a Beta strength unusable.
    MIN_SPREAD_PLAYERS = 12
    SPREAD_BOUNDS = (0.15, 1.60)

    def fitted_spreads(self, regime: str) -> dict[str, float]:
        return {
            r["stat"]: float(r["spread"])
            for r in self.conn.execute(
                "SELECT stat, spread FROM fitted_priors"
                " WHERE regime = ? AND spread IS NOT NULL", (regime,))
        }

    @staticmethod
    def _logit_noise(rate: float, opps: float) -> float:
        """Variance a *single* player's log-odds carries purely from sampling.

        The delta-method variance of ``logit(k/n)`` is ``1 / (n p (1-p))``.
        Subtracting its average is what turns raw scatter into a spread.
        """
        p = min(max(rate, 0.02), 0.98)
        return 1.0 / max(opps * p * (1.0 - p), 1e-9)

    def _spread_samples(self) -> dict[str, dict[str, float]]:
        """Between-player sd of each stat, in log-odds, per regime.

        Measured from the players themselves rather than derived from a fitted
        Beta: where the pool cannot be separated a Beta returns a large
        strength, implying a tiny spread, and a tiny spread amplifies every
        deviation measured against it. The observed scatter has no such
        failure mode -- when players really are alike, it is simply small.

        **The scatter is not the spread.** What a pool shows is the true
        between-player spread *plus* the sampling noise in each player's own
        estimate, and those add in variance: ``observed = true + noise``. The
        noise term is ``1 / (n p (1-p))`` per player, so it is largest exactly
        where opportunities are scarcest -- a river fold at n=60 carries an sd
        of 0.27 from nothing but the coin flips, against a raw scatter of 0.29.
        Skipping the subtraction therefore inflates every thin postflop
        feature and leaves them looking far more separable than they are,
        which is the same preflop-versus-postflop imbalance this measurement
        exists to remove, reintroduced one level down. Subtract it, and what
        is left is how much players genuinely differ.
        """
        import math
        import statistics

        from .priors import logit
        from .profile import DERIVED
        lo, hi = self.SPREAD_BOUNDS
        by: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        noise: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        raw: dict[tuple[str, int], dict[str, tuple[float, float]]] = {}
        for row in self.conn.execute(
                "SELECT regime, player_id, stat, hits, opps FROM ratios WHERE opps >= 40"):
            raw.setdefault((row["regime"], row["player_id"]), {})[row["stat"]] = (
                row["hits"], row["opps"])
            if row["stat"].startswith(("seat:", "saw:", "act:", VS_HERO)):
                continue
            rate = row["hits"] / row["opps"]
            by[row["regime"]][row["stat"]].append(logit(min(max(rate, 0.005), 0.995)))
            noise[row["regime"]][row["stat"]].append(
                self._logit_noise(rate, row["opps"]))
        # aggression:* is assembled from the raw action counters and never
        # stored as a ratio row of its own, so scanning this table alone left
        # it the one feature block with no fitted spread -- and therefore the
        # one block still measured in the built-in constant, roughly twice the
        # scatter this pool actually shows. `maniac`, whose entire identity is
        # aggression, is the archetype that paid for it. Same assembly as
        # :meth:`_observed_ranges` and :meth:`population_samples`.
        for (regime, _pid), stats in raw.items():
            for stat, (num_keys, den_keys) in DERIVED.items():
                den = sum(stats.get(k, (0.0, 0.0))[0] for k in den_keys)
                if den < 40:
                    continue
                num = sum(stats.get(k, (0.0, 0.0))[0] for k in num_keys)
                by[regime][stat].append(logit(min(max(num / den, 0.005), 0.995)))
                noise[regime][stat].append(self._logit_noise(num / den, den))
        out: dict[str, dict[str, float]] = {}
        for regime, stats in by.items():
            for stat, vals in stats.items():
                if len(vals) < self.MIN_SPREAD_PLAYERS:
                    continue
                var = statistics.pvariance(vals) - statistics.fmean(noise[regime][stat])
                if var <= 0:
                    continue
                out.setdefault(regime, {})[stat] = min(max(math.sqrt(var), lo), hi)
        return out

    def _observed_ranges(self) -> dict[str, dict[str, tuple[float, float]]]:
        """Per stat, the band of frequencies players in this pool occupy.

        Robust percentiles rather than min and max: one player on a thin
        sample should not be able to stretch the band, and the point of the
        band is to say what is *normal* here, not what is possible.
        """
        from .profile import DERIVED

        by: dict[str, dict[str, list[float]]] = {}
        raw: dict[tuple[str, int], dict[str, tuple[float, float]]] = {}
        for row in self.conn.execute(
                "SELECT regime, player_id, stat, hits, opps FROM ratios WHERE opps >= 40"):
            raw.setdefault((row["regime"], row["player_id"]), {})[row["stat"]] = (
                row["hits"], row["opps"])
            if row["stat"].startswith(("seat:", "saw:", "act:", VS_HERO)):
                continue
            by.setdefault(row["regime"], {}).setdefault(row["stat"], []).append(
                row["hits"] / row["opps"])
        # aggression:* is assembled from raw action counters and never stored
        # as a ratio of its own, so a band read off this table alone had no
        # entry for it -- and `maniac`, whose whole identity is aggression,
        # was the archetype that needed one most.
        for (regime, _pid), stats in raw.items():
            for stat, (num_keys, den_keys) in DERIVED.items():
                den = sum(stats.get(k, (0.0, 0.0))[0] for k in den_keys)
                if den < 40:
                    continue
                num = sum(stats.get(k, (0.0, 0.0))[0] for k in num_keys)
                by.setdefault(regime, {}).setdefault(stat, []).append(num / den)
        out: dict[str, dict[str, tuple[float, float]]] = {}
        for regime, stats in by.items():
            for stat, vals in stats.items():
                if len(vals) < self.MIN_SPREAD_PLAYERS:
                    continue
                vals.sort()
                lo = vals[max(0, int(0.02 * (len(vals) - 1)))]
                hi = vals[min(len(vals) - 1, int(round(0.98 * (len(vals) - 1))))]
                if hi > lo:
                    out.setdefault(regime, {})[stat] = (lo, hi)
        return out

    def fit_priors(self, min_players: int = 8) -> dict[str, int]:
        """Re-estimate population priors from the players in this database."""
        from .priors import fit_empirical
        fitted: dict[str, int] = {}
        now = int(time.time())
        all_spreads = self._spread_samples()
        all_ranges = self._observed_ranges()
        for regime, samples in self.population_samples().items():
            result = fit_empirical(samples, min_players=min_players)
            if not result:
                continue
            players = self.conn.execute(
                "SELECT COUNT(DISTINCT player_id) c FROM books WHERE regime = ?",
                (regime,)).fetchone()["c"]
            spreads = all_spreads.get(regime, {})
            ranges = all_ranges.get(regime, {})
            self.conn.executemany(
                "INSERT OR REPLACE INTO fitted_priors"
                " (regime, stat, mean, strength, players, fitted_at, spread, floor, ceiling)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(regime, stat, mean, strength, players, now, spreads.get(stat),
                  ranges.get(stat, (None, None))[0], ranges.get(stat, (None, None))[1])
                 for stat, (mean, strength) in result.items()])
            fitted[regime] = len(result)
        self.conn.commit()
        return fitted

    def fitted_priors(self, regime: str) -> dict:
        """``stat -> (mean, strength)``, plus ``spread:<stat> -> sd`` entries.

        One dict because it travels as one thing all the way to
        :func:`villain.priors.spread_of`; the prefix keeps the two kinds of
        entry apart without a second parameter on every call in between.
        """
        out: dict = {}
        for row in self.conn.execute(
                "SELECT stat, mean, strength, spread, floor, ceiling"
                " FROM fitted_priors WHERE regime = ?",
                (regime,)):
            out[row["stat"]] = (row["mean"], row["strength"])
            if row["spread"] is not None:
                out[f"spread:{row['stat']}"] = float(row["spread"])
            if row["floor"] is not None and row["ceiling"] is not None:
                out[f"range:{row['stat']}"] = (float(row["floor"]), float(row["ceiling"]))
        return out

    def profiles(self, player_id: int, min_hands: int = 1) -> list:
        """One profile per table size. The detailed view, not the default."""
        from .profile import build_profiles
        books = self.books(player_id)
        if not books:
            return []
        regime = max(books.values(), key=lambda b: b.hands).regime
        priors = self.fitted_priors(regime) or None
        built = build_profiles(books, min_hands=min_hands, priors=priors)
        for profile in built:
            # This view is split by table size, so each profile gets only its
            # own -- pooling here would undo the split it exists to show.
            profile.adjustments = adjustments(
                {profile.regime: books[profile.regime]}, priors=priors)
            profile.versus = versus_read({profile.regime: books[profile.regime]},
                                         priors=priors)
        return built

    def profile(self, player_id: int):
        """The single profile for a player, pooled across table sizes.

        This is the default everywhere. Splitting by table size is how the
        statistics stay meaningful, not how anybody wants to read them.
        """
        from .profile import build_unified, primary_regime
        books = self.books(player_id)
        if not books:
            return None
        priors = self.fitted_priors(primary_regime(books)) or None
        profile = build_unified(books, priors=priors)
        if profile is not None:
            profile.adjustments = adjustments(books, priors=priors)
            profile.versus = versus_read(books, priors=priors)
        return profile

    def player_hands(self, player_id: int | None = None, progress=None) -> list[Hand]:
        """Stored hands, keyed to internal ids.

        The same re-keying ``rebuild`` does, so anything computed from these
        hands lines up with the statistics computed from them. With
        ``player_id`` set, only the hands that player was dealt into; with it
        omitted, every hand -- for callers (like the hand-strength model) that
        need every seat resolved to the id used elsewhere, not just one
        player's. Prefer this over :meth:`stored_hands`, whose ids are the raw
        site accounts on purpose.
        """
        accounts = {
            (r["site"], r["account"]): int(r["player_id"])
            for r in self.conn.execute("SELECT site, account, player_id FROM aliases")
        }

        def resolve(site, account, name):
            return (accounts.get((site, split_key(account, name)))
                    or accounts.get((site, account)))

        query = "SELECT site, payload FROM hands ORDER BY started_at"
        if player_id is not None:
            # Pick the hand ids out of the seat index first. Asking for one
            # player used to decompress and parse every hand in the database
            # and throw almost all of them away -- and `villain validate` does
            # that once per player, which on this database was eight million
            # hand parses and eleven minutes. The seat table is small and
            # uncompressed; `rebuild` has always narrowed this way.
            mine = {key for key, pid in accounts.items() if pid == player_id}
            wanted = {
                row["hand_id"]
                for row in self.conn.execute(
                    "SELECT hand_id, site, account, name FROM hand_seats")
                if (row["site"], row["account"]) in mine
                or (row["site"], split_key(row["account"], row["name"])) in mine
            }
            if not wanted:
                return []
            self.conn.execute("DROP TABLE IF EXISTS temp.player_hand_ids")
            self.conn.execute(
                "CREATE TEMP TABLE player_hand_ids (hand_id TEXT PRIMARY KEY)")
            self.conn.executemany(
                "INSERT OR IGNORE INTO temp.player_hand_ids (hand_id) VALUES (?)",
                [(h,) for h in wanted])
            query = ("SELECT h.site, h.payload FROM hands h"
                     " JOIN temp.player_hand_ids w ON w.hand_id = h.hand_id"
                     " ORDER BY h.started_at")

        # Counted, because this is not a quick read: every hand is decompressed
        # and parsed, and on a large database that is a minute of silence before
        # the caller's own work even starts. A caller with a progress bar was
        # left with nothing to put in it for the longest part of the wait.
        total = (len(wanted) if player_id is not None else
                 self.conn.execute("SELECT COUNT(*) c FROM hands").fetchone()["c"])
        out = []
        # Report after the work, not before it. Calling progress at at=0 and
        # then again at total once the loop returns made a bar that jumped to
        # full the moment the last row was fetched, while gzip+parse of that
        # last batch -- and everything the caller does next -- still had to
        # run. Every 200, after decompressing, tracks the wait itself.
        if progress is not None:
            progress(0, total)
        for at, row in enumerate(self.conn.execute(query)):
            data = json.loads(gzip.decompress(row["payload"]))
            hand = hand_from_dict(data)
            for seat in hand.seats:
                pid = resolve(hand.site, seat.player_id, seat.name)
                seat.player_id = str(pid) if pid is not None else seat.player_id
            out.append(hand)
            if progress is not None and (at + 1) % 200 == 0:
                progress(at + 1, total)
        if progress is not None:
            progress(total, total)
        return out

    def delete_player(self, player_id: int) -> dict:
        """Forget one person. Their hands stay exactly where they are.

        A hand belongs to a table, not to a player: five other people sat in it
        and their samples are built from the same rows. So this deletes the
        identity and everything derived from it -- the player, the accounts
        pointing at them, their notes, their books -- and leaves the hand log
        untouched. Their seats simply stop resolving to anybody.

        That is not a leak. :meth:`rebuild` maps seats through ``aliases`` and
        returns None for an account it does not know, so a deleted player does
        not reappear on the next rebuild. Importing hands with that account
        again *does* create a fresh player, which is the right answer: you told
        the tool to forget them, not to refuse to ever see them again.

        Raises LookupError if there is no such player.
        """
        row = self.conn.execute(
            "SELECT display_name FROM players WHERE id = ?", (player_id,)).fetchone()
        if row is None:
            raise LookupError(f"no player {player_id}")
        hands = self.conn.execute(
            "SELECT COALESCE(SUM(hands), 0) AS n FROM aliases WHERE player_id = ?",
            (player_id,)).fetchone()["n"]
        accounts = self.conn.execute(
            "SELECT COUNT(*) AS c FROM aliases WHERE player_id = ?",
            (player_id,)).fetchone()["c"]
        for table in ("ratios", "meters", "books", "notes", "aliases"):
            self.conn.execute(f"DELETE FROM {table} WHERE player_id = ?", (player_id,))
        # Both columns: the pair is stored sorted, so the id can be on either side.
        self.conn.execute("DELETE FROM distinct_pairs WHERE a = ? OR b = ?",
                          (player_id, player_id))
        self.conn.execute("DELETE FROM players WHERE id = ?", (player_id,))
        self.conn.commit()
        return {"player_id": player_id, "name": row["display_name"],
                "hands": int(hands), "accounts": int(accounts)}

    def reset(self) -> dict[str, int]:
        """Empty the database, keeping the file and its schema.

        There is no undo. Hands are the source of truth for everything else, so
        once they are gone every profile, alias and merge decision goes with
        them -- re-importing the original exports rebuilds the statistics, but
        not the identity decisions made along the way.
        """
        counts = {
            "hands": self.conn.execute("SELECT COUNT(*) c FROM hands").fetchone()["c"],
            "players": self.conn.execute("SELECT COUNT(*) c FROM players").fetchone()["c"],
        }
        for table in ("ratios", "meters", "books", "notes", "distinct_pairs", "hand_seats",
                      "aliases", "fitted_priors", "hands", "players"):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.execute("DELETE FROM sqlite_sequence WHERE name = 'players'")
        self.conn.commit()
        self.conn.execute("VACUUM")
        return counts

    # -- notes -----------------------------------------------------------

    def add_note(self, player_id: int, body: str) -> None:
        self.conn.execute(
            "INSERT INTO notes (player_id, created_at, body) VALUES (?, ?, ?)",
            (player_id, int(time.time()), body))
        self.conn.commit()

    def notes(self, player_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT created_at, body FROM notes WHERE player_id = ? ORDER BY created_at",
            (player_id,)).fetchall()
