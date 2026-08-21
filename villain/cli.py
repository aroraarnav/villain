"""Command line interface: a data door, not a second screen.

The app is the product. These are the operations the browser cannot do for
you -- getting hands in, getting a database out, and repairing identity -- plus
``--json`` for anyone who wants the read as data rather than as a page.

    villain import <files...>        read hand histories into the database
    villain players                  who is in the database
    villain profile <name> --json    the full read, as data
    villain link --suggest           find accounts that may be one person
    villain link <keep> <absorb>     merge two accounts belonging to one person
    villain unlink <id> <site> <acct> undo a merge for one alias
    villain note <name> <text>       attach a note to a player
    villain rebuild                  recompute every profile from stored hands
    villain export <file>            write every hand to a portable archive
    villain import-db <file>         merge an archive from another machine
    villain test                     run the web app locally against your database
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analyze import as_dict
from .db import DEFAULT_PATH, ImportReport, Store
from .identity import suggest_links
from .parsers import parse_paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="villain", description="Profile poker opponents from hand histories.")
    parser.add_argument("--db", type=Path, default=DEFAULT_PATH,
                        help=f"database path (default {DEFAULT_PATH})")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import", help="read hand histories into the database")
    p.add_argument("paths", nargs="+", type=Path)
    p.add_argument("--quiet", action="store_true")

    p = sub.add_parser("players", help="list known players")
    p.add_argument("--min-hands", type=int, default=1)

    # No text rendering: the profile screen is the app's, and a second one in
    # here drifted from it every time a leak changed how it reads.
    p = sub.add_parser("profile", help="the full read on a player, as JSON")
    p.add_argument("name")
    p.add_argument("--by-table", action="store_true",
                   help="split the profile by table size instead of pooling it")
    p.add_argument("--regime", help="with --by-table: hu, 3max, 6max or full")

    p = sub.add_parser("link", help="merge two accounts belonging to one person")
    p.add_argument("keep", nargs="?", help="player id to keep")
    p.add_argument("absorb", nargs="?", help="player id to fold into it")
    p.add_argument("--suggest", action="store_true")

    p = sub.add_parser("unlink", help="split one alias back onto its own player")
    p.add_argument("player_id", type=int, help="player who currently owns the alias")
    p.add_argument("site", help="site of the alias (e.g. pokernow)")
    p.add_argument("account", help="site account id to split off")

    # The only writer for something the profile page displays.
    p = sub.add_parser("note", help="attach a note to a player")
    p.add_argument("name")
    p.add_argument("body", nargs="+")

    sub.add_parser("rebuild", help="recompute all profiles from stored hands")

    p = sub.add_parser("export", help="write every hand to a portable archive")
    p.add_argument("path", type=Path)

    p = sub.add_parser("import-db", help="merge an archive from another machine")
    p.add_argument("path", type=Path)

    # The web app is the product and it is served from the browser; this runs
    # the same thing on a loopback socket against your own database, which is
    # how you try a change before it ships. Not a second interface -- the same
    # one, with its transport swapped.
    p = sub.add_parser("test", help="run the web app locally against your database")
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--no-browser", action="store_true")

    args = parser.parse_args(argv)
    handler = globals()[f"_cmd_{args.command.replace('-', '_')}"]
    return handler(args)


# ---------------------------------------------------------------------------


def _cmd_import(args) -> int:
    from .identity import askable_questions, session_questions

    report = ImportReport()
    batch: list = []
    with Store(args.db) as store:
        for path, hands in parse_paths(args.paths):
            report.files += 1
            if not args.quiet:
                print(f"  {path.name}: {len(hands)} hands")
            batch.extend(hands)
        # Identity before storage, and over the whole batch rather than per
        # file. A site that issues a fresh account id per session turns one
        # regular into dozens of accounts; importing them as-is made 343
        # players out of 175 real ones, and every one of those samples is
        # split. Only the runs the tool is sure of are applied -- the same
        # never-dealt-in-together evidence it treats as decisive elsewhere.
        questions = session_questions(store, batch)
        runs = [q for q in questions if q.auto and q.members]
        if runs and not args.quiet:
            accounts = sum(len(q.members) for q in runs)
            print(f"  merging {accounts} accounts into {len(runs)} players "
                  f"(same name, never dealt in together)")
        # Defer: rebuilding after each file re-reads every hand those
        # players appear in, so a directory of N files cost N full passes.
        store.add_hands(batch, report, defer_rebuild=True)
        merged = _apply_runs(store, runs)
        report.merged_accounts = merged
        left = askable_questions(questions)
        if left and not args.quiet:
            print(f"  {len(left)} pair(s) need you: villain link --suggest")
        if not args.quiet and report.hands_new:
            print("  building profiles...", flush=True)
        store.rebuild_pending()
        # Fit the population from this pool, after the books exist. A home
        # game measured against online norms is wrong by the gap between
        # them, and here that gap is a VPIP of 0.42 against 0.24.
        fitted = store.fit_priors()
        if fitted and not args.quiet:
            print(f"  priors fitted from your own pool ({sum(fitted.values())} stats)")
    if report.files == 0:
        print("No file matched a known format.", file=sys.stderr)
        return 1
    print(report)
    return 0


def _apply_runs(store, runs) -> int:
    """Fold each reconnect run onto its busiest account. Returns links made."""
    merged = 0
    for question in runs:
        ids = []
        for side in question.members:
            row = store.conn.execute(
                "SELECT player_id FROM aliases WHERE site = ? AND account = ?",
                (side["site"], side["account"])).fetchone()
            if row is not None:
                ids.append(int(row["player_id"]))
        keep, seen = None, set()
        for pid in ids:
            if pid in seen:
                continue
            seen.add(pid)
            if keep is None:
                keep = pid
                continue
            try:
                store.link(keep, pid, rebuild=False)
                merged += 1
            except ValueError:
                pass                      # co-occurrence refused it; leave apart
        if keep is not None:
            store.conn.execute("UPDATE players SET display_name = ? WHERE id = ?",
                               (question.default_name, keep))
    store.conn.commit()
    return merged


def _cmd_export(args) -> int:
    from .portable import export_hands
    with Store(args.db) as store:
        print(export_hands(store, args.path))
    return 0


def _cmd_import_db(args) -> int:
    from .portable import UnreadableExport, import_export
    if not args.path.exists():
        print(f"No such file: {args.path}", file=sys.stderr)
        return 1
    with Store(args.db) as store:
        try:
            report = import_export(store, args.path)
        except UnreadableExport as exc:
            print(exc, file=sys.stderr)
            return 1
    print(report)
    return 0


def _cmd_players(args) -> int:
    """The index. Without it there is no way to find the ids link/unlink take."""
    with Store(args.db) as store:
        rows = [r for r in store.players() if (r["hands"] or 0) >= args.min_hands]
        if not rows:
            print("No players yet. Start with: villain import <file>")
            return 0
        print(f"{'id':>4s}  {'player':18s} {'hands':>6s}  aliases")
        for row in rows:
            print(f"{row['id']:>4d}  {row['display_name'][:18]:18s} "
                  f"{row['hands'] or 0:6d}  {row['aliases'] or ''}")
    return 0


def _cmd_profile(args) -> int:
    with Store(args.db) as store:
        matches = store.find_player(args.name)
        if not matches:
            print(f"No player matching {args.name!r}.", file=sys.stderr)
            return 1
        if len(matches) > 1:
            print("Several players match:", file=sys.stderr)
            for row in matches:
                print(f"  {row['id']}  {row['display_name']}", file=sys.stderr)
            return 1
        player_id = int(matches[0]["id"])
        if args.by_table or args.regime:
            profiles = store.profiles(player_id)
            if args.regime:
                profiles = [p for p in profiles if p.regime == args.regime]
        else:
            unified = store.profile(player_id)
            profiles = [unified] if unified else []
        if not profiles:
            print("No hands recorded for that player and table size.", file=sys.stderr)
            return 1
        payload = [as_dict(p) for p in profiles]
        notes = [n["body"] for n in store.notes(player_id)]
        if notes:
            for entry in payload:
                entry["notes"] = notes
        print(json.dumps(payload, indent=2))
    return 0


def _cmd_link(args) -> int:
    with Store(args.db) as store:
        if args.suggest or not (args.keep and args.absorb):
            suggestions = suggest_links(store)
            if not suggestions:
                print("No candidate merges.")
                return 0
            print("Candidate merges (nothing is applied automatically):")
            for s in suggestions:
                print(f"  villain link {s.keep} {s.absorb}"
                      f"   # {s.absorb_name} -> {s.keep_name}, "
                      f"confidence {s.confidence:.0%}")
                print(f"      {s.reason}")
            return 0
        store.link(int(args.keep), int(args.absorb))
        print(f"Merged {args.absorb} into {args.keep} and rebuilt their profile.")
    return 0


def _cmd_unlink(args) -> int:
    with Store(args.db) as store:
        new_id = store.unlink(args.player_id, args.site, args.account)
        print(f"Split {args.site}/{args.account} onto new player {new_id}; "
              f"rebuilt {args.player_id} and {new_id}.")
    return 0


def _cmd_rebuild(args) -> int:
    with Store(args.db) as store:
        print(f"Rebuilt {store.rebuild()} player profile(s) from stored hands.")
    return 0


def _cmd_test(args) -> int:
    from .webapp import serve
    serve(db=args.db, port=args.port, open_browser=not args.no_browser)
    return 0


def _cmd_note(args) -> int:
    with Store(args.db) as store:
        matches = store.find_player(args.name)
        if len(matches) != 1:
            print(f"Need exactly one match for {args.name!r}.", file=sys.stderr)
            return 1
        store.add_note(int(matches[0]["id"]), " ".join(args.body))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
