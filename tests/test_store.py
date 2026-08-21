"""Persistence, identity resolution and the command line."""

import json

import pytest

from villain.cli import main
from villain.db import Store
from villain.identity import name_similarity, normalize, suggest_links


@pytest.fixture
def store(tmp_path, hands):
    with Store(tmp_path / "v.db") as s:
        s.add_hands(hands)
        yield s


def test_import_is_idempotent(tmp_path, hands):
    with Store(tmp_path / "v.db") as s:
        first = s.add_hands(hands)
        second = s.add_hands(hands)
    assert first.hands_new == len(hands)
    assert second.hands_new == 0
    assert second.duplicates == len(hands)


def test_players_accumulate_hands(store, hands):
    rows = store.players()
    assert rows
    total = sum(r["hands"] or 0 for r in rows)
    assert total == sum(len(h.seats) for h in hands)


def test_books_are_split_by_table_size(store):
    busiest = max(store.players(), key=lambda r: r["hands"] or 0)
    books = store.books(int(busiest["id"]))
    assert books
    assert all(book.regime == key for key, book in books.items())


def test_hands_are_the_source_of_truth(store):
    """Books are a cache: deleting and rebuilding them must change nothing."""
    player = max(store.players(), key=lambda r: r["hands"] or 0)
    before = {r: b.hands for r, b in store.books(int(player["id"])).items()}
    store.conn.execute("DELETE FROM ratios")
    store.conn.execute("DELETE FROM books")
    store.rebuild()
    after = {r: b.hands for r, b in store.books(int(player["id"])).items()}
    assert before == after


def test_regular_opponents_can_never_be_merged(store, hands):
    """Two people who play each other constantly are two people.

    The fixture is twenty hands and its busiest pair shares ten of them --
    exactly ``SPURIOUS_OVERLAP``, and so still mergeable by design. Copying the
    batch under fresh hand ids puts them clearly past it, which is the state
    this is about: not a reconnect, two people at one table.
    """
    import copy
    from collections import Counter

    from villain.db import SPURIOUS_OVERLAP

    again = []
    for hand in hands:
        twin = copy.deepcopy(hand)
        twin.hand_id = f"{hand.hand_id}-again"
        again.append(twin)
    store.add_hands(again)

    pairs = Counter()
    for hand in list(hands) + again:
        ids = sorted({store.player_for(hand.site, s.player_id, s.name)
                      for s in hand.seats})
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                pairs[(a, b)] += 1
    (a, b), shared = pairs.most_common(1)[0]
    assert shared > SPURIOUS_OVERLAP, "fixture should contain two regular opponents"
    assert store.shared_hands(a, b) == shared
    assert store.are_distinct(a, b)
    with pytest.raises(ValueError, match="hands together"):
        store.link(a, b)


def test_a_brief_double_seating_stays_mergeable(store, hands):
    """The case the old threshold of 2 refused: one person reconnecting from a
    second account and playing a few hands as both. Ten shared hands out of a
    real history is that, not two people who play together."""
    from collections import Counter

    from villain.db import SPURIOUS_OVERLAP

    pairs = Counter()
    for hand in hands:
        ids = sorted({store.player_for(hand.site, s.player_id, s.name)
                      for s in hand.seats})
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                pairs[(a, b)] += 1
    (a, b), shared = pairs.most_common(1)[0]
    assert shared <= SPURIOUS_OVERLAP
    assert not store.are_distinct(a, b)
    store.link(a, b)          # allowed, and undoable from the player page


def test_a_one_hand_overlap_does_not_block_a_merge(tmp_path, hands):
    """A reconnect can leave a stale seat for a hand. Treating that as proof
    makes a legitimate merge permanently impossible, which is worse than
    asking."""
    from villain.db import SPURIOUS_OVERLAP
    with Store(tmp_path / "v.db") as store:
        a = store.player_for("test", "acct-a", "Dave")
        b = store.player_for("test", "acct-b", "Dvae")
        store.mark_distinct([a, b])
        assert store.shared_hands(a, b) == 1
        assert not store.are_distinct(a, b)
        for _ in range(SPURIOUS_OVERLAP):
            store.mark_distinct([a, b])
        assert store.are_distinct(a, b), "a real overlap still blocks"


def test_linking_pools_two_accounts(tmp_path, hands):
    """The same human under two account ids ends up with one merged profile."""
    with Store(tmp_path / "v.db") as s:
        s.add_hands(hands)
        # Forge a second account that never shares a hand with the original.
        original = max(s.players(), key=lambda r: r["hands"] or 0)
        s.conn.execute(
            "INSERT INTO players (display_name, created_at) VALUES ('Ghost', 0)")
        ghost = s.conn.execute("SELECT MAX(id) m FROM players").fetchone()["m"]
        before = sum(b.hands for b in s.books(int(original["id"])).values())
        s.link(int(original["id"]), int(ghost))
        after = sum(b.hands for b in s.books(int(original["id"])).values())
        remaining = [r["id"] for r in s.players()]
    assert after == before
    assert ghost not in remaining


def test_unlink_splits_an_alias_back_out(tmp_path, hands):
    with Store(tmp_path / "v.db") as s:
        s.add_hands(hands)
        original = max(s.players(), key=lambda r: r["hands"] or 0)
        pid = int(original["id"])
        s.conn.execute(
            "INSERT INTO aliases (site, account, name, player_id, hands) "
            "VALUES ('pokernow', 'ghost-acct', 'Ghost', ?, 5)", (pid,))
        s.conn.commit()
        new_id = s.unlink(pid, "pokernow", "ghost-acct")
        assert new_id != pid
        names = {r["display_name"] for r in s.players()}
        assert "Ghost" in names
        assert s.are_distinct(pid, new_id)


def test_delete_player_forgets_the_person_and_keeps_the_hands(tmp_path, hands):
    """The one thing delete must never do is take a hand with it.

    A hand seats several people; deleting one player's hands would silently
    shrink everybody else's sample. So the identity goes and the log stays.
    """
    with Store(tmp_path / "v.db") as s:
        s.add_hands(hands)
        before_hands = s.conn.execute("SELECT COUNT(*) c FROM hands").fetchone()["c"]
        before_seats = s.conn.execute("SELECT COUNT(*) c FROM hand_seats").fetchone()["c"]
        victim = max(s.players(), key=lambda r: r["hands"] or 0)
        pid = int(victim["id"])

        out = s.delete_player(pid)
        assert out["player_id"] == pid
        assert out["name"] == victim["display_name"]

        assert pid not in {int(r["id"]) for r in s.players()}
        for table in ("aliases", "books", "ratios", "meters", "notes"):
            left = s.conn.execute(
                f"SELECT COUNT(*) c FROM {table} WHERE player_id = ?", (pid,)).fetchone()["c"]
            assert left == 0, f"{table} still references the deleted player"
        pairs = s.conn.execute(
            "SELECT COUNT(*) c FROM distinct_pairs WHERE a = ? OR b = ?",
            (pid, pid)).fetchone()["c"]
        assert pairs == 0

        assert s.conn.execute("SELECT COUNT(*) c FROM hands").fetchone()["c"] == before_hands
        assert s.conn.execute(
            "SELECT COUNT(*) c FROM hand_seats").fetchone()["c"] == before_seats


def test_deleted_player_does_not_come_back_on_rebuild(tmp_path, hands):
    """rebuild() resolves seats through aliases and drops what it cannot map,
    so a forgotten player stays forgotten rather than being re-derived from the
    hand log the delete deliberately left alone."""
    with Store(tmp_path / "v.db") as s:
        s.add_hands(hands)
        pid = int(max(s.players(), key=lambda r: r["hands"] or 0)["id"])
        s.delete_player(pid)
        s.rebuild()
        assert pid not in {int(r["id"]) for r in s.players()}


def test_delete_player_rejects_an_unknown_id(tmp_path, hands):
    with Store(tmp_path / "v.db") as s:
        s.add_hands(hands)
        with pytest.raises(LookupError):
            s.delete_player(999999)


@pytest.mark.parametrize("a,b,expected", [
    ("PlayerG", "PlayerG2", 1.0),
    ("PlayerK", "PlayerK2", 1.0),
    ("player_one", "PlayerOne", 1.0),
])
def test_trailing_digits_and_punctuation_are_noise(a, b, expected):
    assert name_similarity(a, b) == expected


def test_suggestion_reason_cites_the_matching_alias(tmp_path, hands):
    """Display names can drift; the reason must cite the aliases that scored."""
    from villain.db import Store
    db = tmp_path / "v.db"
    with Store(db) as store:
        store.add_hands(hands)
        # Plant a second player whose only alias collides with player1's name,
        # but whose display name is unrelated — the bug that made PlayerJHusband
        # look like a 100% match for PlayerE.
        store.conn.execute(
            "INSERT INTO players (display_name, created_at) VALUES ('OtherFace', 0)")
        pid = store.conn.execute("SELECT id FROM players WHERE display_name='OtherFace'"
                                 ).fetchone()["id"]
        store.conn.execute(
            "INSERT INTO aliases (site, account, name, player_id, hands) "
            "VALUES ('pokernow', 'ghost-acct', 'player1', ?, 10)", (pid,))
        store.conn.commit()
        hits = [s for s in suggest_links(store)
                if {s.keep_name, s.absorb_name} == {"player1", "OtherFace"}]
        assert hits, "same screen name on two accounts should surface"
        assert "player1" in hits[0].reason
        assert "100%" not in hits[0].reason or "appeared as" in hits[0].reason


def test_unrelated_names_stay_apart():
    assert name_similarity("Milo", "PlayerG2") < 0.8
    assert normalize("Bob!!") == "bob"


def test_suggestions_never_include_impossible_merges(store):
    for suggestion in suggest_links(store):
        assert not store.are_distinct(suggestion.keep, suggestion.absorb)


def test_fit_priors_refuses_a_thin_pool(store):
    assert store.fit_priors(min_players=8) == {}


def test_a_definitions_bump_rebuilds_once(tmp_path, hands, monkeypatch):
    """The stamp is what makes the second open free. Without it, every Store()
    walks every stored hand -- a minute on a real database, every request."""
    from villain.db import DEFINITIONS_VERSION, Store

    db = tmp_path / "v.db"
    with Store(db) as store:
        store.add_hands(hands)
        store.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('definitions_version', 'old')")
        store.conn.commit()

    calls = {"n": 0}
    real = Store.rebuild

    def counted(self, *args, **kwargs):
        calls["n"] += 1
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Store, "rebuild", counted)
    with Store(db) as store:
        stamped = store.conn.execute(
            "SELECT value FROM meta WHERE key = 'definitions_version'").fetchone()["value"]
    assert stamped == DEFINITIONS_VERSION
    assert calls["n"] == 1
    with Store(db):
        pass
    assert calls["n"] == 1


# -- CLI --------------------------------------------------------------------

def test_cli_import_and_profile(tmp_path, capsys):
    db = tmp_path / "cli.db"
    from tests.conftest import FIXTURE
    assert main(["--db", str(db), "import", str(FIXTURE)]) == 0
    assert "new hands" in capsys.readouterr().out

    assert main(["--db", str(db), "players"]) == 0
    listing = capsys.readouterr().out
    assert "player1" in listing

    assert main(["--db", str(db), "profile", "player1"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload and payload[0]["archetype"]


def test_cli_json_is_machine_readable(tmp_path, capsys):
    from tests.conftest import FIXTURE
    db = tmp_path / "cli.db"
    main(["--db", str(db), "import", str(FIXTURE)])
    capsys.readouterr()
    main(["--db", str(db), "profile", "player1"])
    payload = json.loads(capsys.readouterr().out)
    assert payload
    entry = payload[0]
    assert entry["archetype"] != "unknown"
    assert "skill" in entry and "leaks" in entry
    # Internal counters must not leak into the public export.
    assert not [k for k in entry["stats"] if k.startswith(("act:", "seat:", "saw:"))]


def test_cli_note_is_the_only_way_to_write_one(tmp_path, capsys):
    """The app renders notes and has no route that creates them, so this is
    the whole write path. Dropping it would leave the feature read-only."""
    from tests.conftest import FIXTURE
    db = tmp_path / "cli.db"
    main(["--db", str(db), "import", str(FIXTURE)])
    capsys.readouterr()
    assert main(["--db", str(db), "note", "player1", "tilts", "after", "a", "big", "pot"]) == 0
    main(["--db", str(db), "profile", "player1"])
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["notes"] == ["tilts after a big pot"]


def test_cli_rejects_unknown_files(tmp_path, capsys):
    junk = tmp_path / "notes.txt"
    junk.write_text("this is not a hand history")
    assert main(["--db", str(tmp_path / "v.db"), "import", str(junk)]) == 1
