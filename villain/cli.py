"""Command line interface.

    villain import <files...>        read hand histories into the database
    villain players                  who is in the database
    villain profile <name>           the full read on somebody
    villain scout <file>             profile a file without storing it
    villain link --suggest           find accounts that may be one person
    villain unlink <id> <site> <acct> undo a merge for one alias
    villain test                     run the web app locally against your database
    villain fit                      re-estimate priors from your own games
    villain rebuild                  recompute every profile from stored hands
    villain validate                 score the classifier on hands it has not seen
    villain backtest                 walk leaks forward: found early, checked late
    villain hero                     what only your own hand history can show
    villain table <names...>         lineup briefing for who is sitting here
    villain export <file>            write every hand to a portable archive
    villain import-db <file>         merge an archive from another machine
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analyze import as_dict
from .db import DEFAULT_PATH, ImportReport, Store
from .features import record_hands
from .identity import suggest_links
from .parsers import parse_paths
from .profile import build_unified
from .report import profile_card, roster
from .skill import leaderboard


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

    p = sub.add_parser("profile", help="the full read on a player")
    p.add_argument("name")
    p.add_argument("--by-table", action="store_true",
                   help="split the profile by table size instead of pooling it")
    p.add_argument("--regime", help="with --by-table: hu, 3max, 6max or full")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--narrate", action="store_true",
                   help="add a plain-English summary from a local model "
                        "(needs VILLAIN_LLM_MODEL; see README)")

    p = sub.add_parser("scout", help="profile a file without storing it")
    p.add_argument("paths", nargs="+", type=Path)
    p.add_argument("--min-hands", type=int, default=20)
    p.add_argument("-v", "--verbose", action="store_true")

    p = sub.add_parser("link", help="merge two accounts belonging to one person")
    p.add_argument("keep", nargs="?", help="player id to keep")
    p.add_argument("absorb", nargs="?", help="player id to fold into it")
    p.add_argument("--suggest", action="store_true")

    p = sub.add_parser("unlink", help="split one alias back onto its own player")
    p.add_argument("player_id", type=int, help="player who currently owns the alias")
    p.add_argument("site", help="site of the alias (e.g. pokernow)")
    p.add_argument("account", help="site account id to split off")

    p = sub.add_parser("fit", help="learn from everything in the database")
    p.add_argument("--min-players", type=int, default=8)

    sub.add_parser("rebuild", help="recompute all profiles from stored hands")
    sub.add_parser("validate", help="score the classifier on hands it has not seen")
    sub.add_parser("backtest", help="walk leaks forward: found early, checked late")

    p = sub.add_parser("hero", help="what only your own hand history can show")
    p.add_argument("--player", help="hero's id or name, if auto-detection picks wrong")

    p = sub.add_parser("table", help="lineup briefing for who is sitting here")
    p.add_argument("names", nargs="+", help="the players at your table")

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

    p = sub.add_parser("note", help="attach a note to a player")
    p.add_argument("name")
    p.add_argument("body", nargs="+")

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


def _cmd_table(args) -> int:
    from .table import brief
    with Store(args.db) as store:
        print(brief(store, args.names))
    return 0


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
        if args.json:
            print(json.dumps([as_dict(p) for p in profiles], indent=2))
            return 0
        for profile in profiles:
            print(profile_card(profile, verbose=args.verbose))
            if args.narrate:
                print(_narration(profile))
        for note in store.notes(player_id):
            print(f"note: {note['body']}")
    return 0


def _narration(profile) -> str:
    """Plain-English summary, or the reason there isn't one.

    Failures are reported rather than swallowed: the usual cause is that no
    model is running, and saying so is more useful than silently printing
    nothing.
    """
    from .analyze import as_dict
    from .narrate import Unavailable, narrate
    try:
        result = narrate(as_dict(profile))
    except Unavailable as exc:
        return f"IN SHORT: unavailable -- {exc}\n"
    body = "\n".join(f"  {line}" for line in _wrap_plain(result.text, 74))
    return f"IN SHORT  ({result.model})\n{body}\n"


def _wrap_plain(text: str, width: int) -> list[str]:
    import textwrap
    return textwrap.wrap(text, width=width) or [""]


def _cmd_scout(args) -> int:
    """Read files straight through to a report, touching no database writes."""
    from .profile import primary_regime

    hands = [h for _, batch in parse_paths(args.paths) for h in batch]
    if not hands:
        print("No file matched a known format.", file=sys.stderr)
        return 1
    books = record_hands(hands)

    fitted: dict[str, dict[str, tuple[float, float]]] = {}
    if Path(args.db).exists():
        with Store(args.db) as store:
            for by in books.values():
                if not by:
                    continue
                home = primary_regime(by)
                if home not in fitted:
                    fitted[home] = store.fitted_priors(home)

    profiles = [
        p for p in (
            build_unified(by, priors=fitted.get(primary_regime(by)) or None,
                          populations=fitted or None)
            for by in books.values() if by
        )
        if p is not None and p.hands >= args.min_hands
    ]
    if not profiles:
        print(f"No player reached {args.min_hands} hands.", file=sys.stderr)
        return 1
    print(f"{len(hands)} hands, {len(profiles)} players\n")
    print(roster(leaderboard(profiles)))
    if args.verbose:
        for profile in profiles:
            print()
            print(profile_card(profile, verbose=True))
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


def _cmd_fit(args) -> int:
    """Learn what this database can support, and say what it cannot.

    Three models, each gated on having enough data. Reporting the refusals is
    the point: a clustering fitted to six players would look exactly as
    authoritative as one fitted to six hundred.
    """
    from .cluster import NotEnoughData as ClustersNeedMore
    from .cluster import fit_clusters
    from .reads import NotEnoughData as ReadsNeedMore
    from .reads import build_dataset
    from .reads import fit as fit_strength

    with Store(args.db) as store:
        print("priors")
        fitted = store.fit_priors(min_players=args.min_players)
        if fitted:
            for regime, count in sorted(fitted.items()):
                print(f"  {regime}: re-estimated {count} priors from your own games")
        else:
            print(f"  not enough players yet (need {args.min_players} per table size); "
                  "using the built-in population priors")

        print("clusters")
        profiles = [p for row in store.players()
                    for p in store.profiles(int(row["id"]))]
        try:
            model = fit_clusters(profiles)
            print(f"  {model.n_components} groups over {model.trained_on} profiles")
            for cluster in model.clusters:
                print(f"    {cluster.describe()}")
        except ClustersNeedMore as exc:
            print(f"  {exc}")

        print("hand strength")
        # player_hands(), not stored_hands(): the model's residuals are keyed
        # by player id, and stored_hands() deliberately keeps raw site
        # account ids, which never match store.players() ids at lookup time.
        rows = build_dataset(store.player_hands())
        try:
            strength = fit_strength(rows)
            print(f"  trained on {strength.rows} revealed decisions "
                  f"({strength.unbiased_rows} unbiased); "
                  f"out-of-fold error {strength.mae:.3f}")
            for row in store.players():
                read = strength.read(str(row["id"]))
                if read:
                    print(f"    {row['display_name']}: {read}")
        except ReadsNeedMore as exc:
            print(f"  {exc}")
    return 0


def _cmd_validate(args) -> int:
    from .validate import score
    with Store(args.db) as store:
        result = score(store)
    if result is None:
        print("Not enough hands on any player to split. Import more first.")
        return 1
    print(result)
    print("\n  Stated confidence should track accuracy; the gap between them is\n"
          "  the calibration error. Halves agreeing is reproducibility, not\n"
          "  correctness -- a player can be labeled the same way twice and be\n"
          "  wrong both times.")
    return 0


def _cmd_backtest(args) -> int:
    from .backtest import score
    with Store(args.db) as store:
        result = score(store)
    if result is None:
        print("Not enough hands on any player to walk forward. Import more first.")
        return 1
    print(result)
    return 0


def _cmd_hero(args) -> int:
    from .hero import (
        FoldReport,
        MissedValueReport,
        NotEnoughData,
        find_hero,
        fit_population_model,
        fold_grades,
        hero_visibility,
        missed_value,
        preflop_range,
        range_narrowing,
        sizing_tell,
        timing_tell,
    )
    from .report import hero_card

    with Store(args.db) as store:
        if args.player:
            matches = store.find_player(args.player)
            if len(matches) != 1:
                print(f"Need exactly one match for {args.player!r}.", file=sys.stderr)
                return 1
            hero_id = int(matches[0]["id"])
        else:
            hero_id = find_hero(store)
        if hero_id is None:
            print("Could not identify hero automatically -- no player has cards "
                  "known on enough of their own hands. Pass --player to name one.",
                  file=sys.stderr)
            return 1

        row = next(r for r in store.players() if int(r["id"]) == hero_id)
        hero_hands = store.player_hands(hero_id)
        ranges = preflop_range(hero_hands, hero_id)
        try:
            model = fit_population_model(store)
            report = fold_grades(hero_hands, hero_id, model)
            missed_report = missed_value(hero_hands, hero_id, model)
        except NotEnoughData as exc:
            print(f"{exc}\n(showing the preflop range only)", file=sys.stderr)
            report, missed_report = FoldReport(grades=[]), MissedValueReport(grades=[])
        sizing = sizing_tell(hero_hands, hero_id)
        timing = timing_tell(hero_hands, hero_id)
        narrowing = range_narrowing(hero_hands, hero_id)

        seen, total = hero_visibility(hero_hands, hero_id)
        visibility = seen / total if total else 0.0
        print(hero_card(row["display_name"], visibility, row["hands"] or 0, ranges,
                        report, missed_report, sizing, timing, narrowing))
    return 0


def _cmd_rebuild(args) -> int:
    with Store(args.db) as store:
        print(f"Rebuilt {store.rebuild()} player profile(s) from stored hands.")
    return 0


def _cmd_test(args) -> int:
    from .web import serve
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
