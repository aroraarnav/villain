# Working in this repo

Villain reads real poker hand histories and profiles the people who played
them: archetypes, priced leaks, a skill rating, an against-you read. See
`README.md` for what it does and `CONTRIBUTING.md` for dev setup, the test
layout, and how to add a parser. This file is the checklist to run through
before any change leaves this repo — read it before opening a PR, not after.

## Orientation

- `villain/` — the library. `db.py` (storage + rebuild), `identity.py`
  (merging accounts into players), `stats.py`/`profile.py` (counters →
  ratios), `archetypes.py` (the ten player types), `exploits.py`/`dynamics.py`
  (priced leaks and the against-you read), `sim.py`/`botplay.py`/`holdem.py`
  (the practice simulator), `glossary.py` (every stat's definition — required
  for anything that reaches the UI), `cli.py` (the `villain` command).
- `villain/webapp/` — the UI's Python side. `server.py` owns the routes and
  says which of them write (`WRITING_POST_ROUTES`); `browser.py` runs the same
  handler under Pyodide for the hosted app.
- `web/` — the hosted app around that: `index.html` is the boot screen,
  `app-shell.js` the auth/sync/database lifecycle, `sync.js` the Supabase
  client, `worker.js` the Pyodide thread, `build.py` assembles `web/dist/`.
- `villain/parsers/` — one module per poker site; `pokernow.py` is the only
  one today. New sites plug in without touching anything downstream.
- `tests/` — plain `pytest`, config in `pyproject.toml`. Run it before and
  after any change: `pytest -q`.
- `HANDOFF.md` is gitignored working notes, not part of the project — it can
  and does contain real names. Nothing from it should ever be copied verbatim
  into a file that gets committed.

## Before opening a PR — the checklist

1. **`pytest -q` and `ruff check .` both clean.** Non-negotiable; CI runs the
   same on 3.11–3.13.
2. **No real player names anywhere in what you're about to commit** — code,
   comments, docstrings, tests, commit messages, PR title and body. This repo
   analyzes real people from a real home game; nothing that identifies one of
   them leaves the local database. See "Player anonymity" in
   `CONTRIBUTING.md`. Before committing, skim your own diff for capitalized
   tokens that look like a screen name rather than a variable — if one came
   from a real session or a real read you were investigating, replace it with
   a fictional placeholder (`player1`, `PlayerA`, `Ghost`) before it goes in.
   If the example needs to trigger specific matching behavior (shared suffix,
   dropped vowels, digit stripping), verify the placeholder actually does
   that with `villain.identity.name_similarity` rather than assuming it does.
3. **American spelling.** `behavior` not `behaviour`, `normalize` not
   `normalise`, `color` not `colour`. Nothing enforces this automatically —
   ruff checks correctness, not spelling — so it's a manual check on your own
   diff, not an assumption.
4. **No invented figures.** The project's one hard rule (`CONTRIBUTING.md`):
   every number that reaches the screen came out of the stored hands, and
   every statistic shown to a user has a `glossary.py` entry. If you add a
   number, add its glossary entry in the same change — a test fails
   otherwise.
5. **Docstrings say why, not what.** State the trade-off or the failure being
   guarded against. If a docstring needs an example name, it needs a
   *fictional* one (see #2) — don't reach for whatever real player happens to
   illustrate the point best.
6. **PR description matches the house style**, visible in the existing merged
   PRs (`gh pr list --state merged`): lead with what changed and why in plain
   language, back claims with real measurements from a run you actually did
   (before/after numbers, test counts), and call out what you deliberately
   left alone and why. Terse, technical, no filler.

## After a PR merges

The repo has `delete_branch_on_merge` on, so a merged PR's head branch is
gone from GitHub automatically — don't delete it by hand mid-review, GitHub
does it the moment the merge lands. That only cleans the remote side: also
run `git fetch --prune` and `git branch -d <branch>` (or `checkout main &&
git branch -d <branch>` if it's the branch you're on) locally so old
branches don't pile up in your own clone.
