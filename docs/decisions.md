# Decisions

Why some of the numbers and shapes in this codebase are what they are.

This file exists so the code does not have to carry its own changelog. A
comment should say what the rule is; when the rule is only defensible because
of something that went wrong, the incident lives here and the comment points
at it. Entries are append-only — a superseded one gets a note, not a deletion.

---

## Storage

### `SPURIOUS_OVERLAP = 10`
Shared hands below this are waved away as one person on two accounts
(reconnect, phone); above it they really sat together and cannot merge.
Fitted from 1,588 co-seated pairs: 83% share 26 or more hands, and the thin
tail below ten is double-seating. It was 2, which refused ordinary mid-orbit
reconnects.

### A definitions bump refits the priors as well as the books
The fitted population is a cache too, and it was not covered by the
definitions stamp. A bump that added a feature refreshed the stat books but
left the priors without it, so the new feature silently fell back to the
built-in online default — measuring a home game against a field it does not
play in. Adding `raise_share` that way changed 26 of 68 real labels before
anyone ran a fit. Refitting alone is enough, because the prior is applied when
a profile is read rather than stored in the books.

### `link()` re-points distinctness constraints row by row
The `distinct_pairs` table's whole contract is that `a < b` — `mark_distinct`
inserts sorted and `shared_hands` looks up sorted. A bare `UPDATE` renaming one
column of a sorted pair can invert it, and an inverted row is invisible to
`shared_hands`, which silently drops the constraint. That is exactly how two
accounts dealt into the same hand became mergeable. `UPDATE OR IGNORE` also
discarded rather than summed the overlap when both players already had a row.

### Ingest accumulates in memory and writes once
Per-seat and per-hand statements meant roughly two million writes for an
80k-hand import — an alias `UPDATE` for every seat of every hand, and a
`distinct_pairs` upsert for every *pair* of seats — each walking an index that
grows as it goes. Counting players once before and after the batch rather than
per seat removed roughly 900k queries on a 70k-hand import. The totals are
identical either way; only the round trips to SQLite change.

### `rebuild()` narrows through a temp table, not an `IN` clause
A bulk import touches every player, so the id list is the whole hands table,
and SQLite caps how many variables one statement may bind. The `IN` form failed
outright with "too many SQL variables" on exactly the large imports that most
need the narrowing.

### Bumping `DEFINITIONS_VERSION` is not free
`_ensure_definitions` runs the rebuild inline and the web layer opens a `Store`
per request, so on a real database (71k hands, about a minute) every page sits
behind a full rebuild showing the generic spinner. Bump it only for a change
that makes stored counters *wrong*. A change that merely adds new counters does
not need it: every reader already falls back when a key is missing.

---

## Learned layers

### `_BOARD_CACHE_MAX = 3000`
Entries, not bytes — but each is about 15 KB, so this is a memory budget in
disguise: roughly 45 MB. It was 40,000, which is 1.2 GB. Measured over a
71,456-hand database that bought a 2.1% hit rate: boards barely repeat (72,289
distinct across 102,184 lookups, about 1.4x reuse), so no reachable cache size
makes this cheap. Paying over a gigabyte for it is what a browser cannot
survive, and the tool runs in one.

### Hero fitting is serialised behind one lock
The server handles requests on their own thread, so two Hero tab loads landing
close together each started their own fit. Two ~40s fits running at once pegged
every core for minutes and starved every other tab's requests. The cache
re-check after acquiring the lock means the second request pays nothing once
the first finishes.

---

## Interface

### The stylesheet's font `url()`s are bare filenames
A relative `url()` resolves against the stylesheet's own base, and this
stylesheet has two: served locally it is linked from `/static/app.css`, so a
bare name resolves to `/static/<name>`; on the hosted build the boot page
strips the `<link>` and injects the CSS into a `<style>`, whose base is the
*document*, so a bare name resolves to `<page-dir>/<name>`. A leading
`static/` satisfies the second and breaks the first into `/static/static/`,
which 404s and falls back silently to the platform sans. A leading slash
breaks the second instead, because Pages serves from a repository subpath.
Bare is the only form both bases agree on, which is why `web/build.py` copies
the faces to the root of `dist/`.

### Three warm hues, three jobs
`--red` is the app's one accent (nav, focus, armed controls, the wordmark),
`--warn` is a threshold you should notice but not act on (breakeven ticks,
timing flags), `--danger` is destructive and irreversible (reset). Collapsing
these to one hex is how a Reset button and the active tab ended up the same
colour. `--suit-red` is separate again: a red suit is a fact about the card,
never a claim about whose card it is, and inside `.hero-scope` `--red` becomes
the blue "you" accent — which was dealing blue diamonds and blue hearts.

---

## Packaging

### `web/build.py` clears `build/` before building the wheel
setuptools copies the package into `build/lib` and never removes what is no
longer there, so a module deleted since the last build is still sitting in that
directory and still goes into the wheel. Deleting six modules and finding all
six in the wheel afterwards is how this was found.

### Data files need a `package-data` line or they exist only locally
Both `villain/webapp/assets/` and `villain/copy/` are loaded by path relative
to their module. Miss the declaration and the source tree still has them, so
the suite passes and `villain test` works — it fails only in the browser, which
installs the wheel and has nowhere to show a traceback.

---

## Priors and profiles

### The between-player spread is fitted per regime, not a global constant
An archetype is a deviation from the field, and the spread is the unit that
deviation is measured in — so getting it wrong moves every prototype's target
without moving any player. It was a global constant while the population *mean*
was already fitted per regime, and the two disagree by up to 2x in **both**
directions on a real pool:

| stat | assumed | fitted | ratio |
| --- | --- | --- | --- |
| `fold_vs_bet:turn` | 0.48 | 0.20 | 0.42 |
| `fold_vs_bet:river` | 0.50 | 0.20 | 0.40 |
| `wwsf` | 0.32 | 0.15 | 0.47 |
| `raise_share` | 0.55 | 0.82 | 1.50 |
| `limp` | 1.00 | 1.60 | 1.60 |

Both directions compound into the same failure. Where the constant is too
*large* — every postflop feature — a trait of −2.0 spreads asks for a frequency
four or five real spreads out, which nobody posts, so `station`, `maniac` and
`nit` could not be anybody's label. Where it is too *small* — `raise_share`,
`limp`, `fold_to_three_bet` — the same trait lands barely one spread out, so
those features separated everybody cheaply and the preflop block decided the
archetype on its own. Fitting the spread narrows the postflop targets into
reachable territory and widens the preflop ones back to what the field really
is.

Fitted from the observed scatter between players with sampling noise
subtracted, not from a Beta strength. The Beta route inverts: where the pool
cannot be separated it returns a *high* strength, which reads as a *narrow*
spread, which amplifies every deviation at once.

### `_beta_prob` clamps away from α/β = 0
scipy's Beta is undefined there, and a 0-hit book (or a fitted prior of 0)
yields NaN. `json.dumps` writes that as the bare token `NaN`, which the browser
cannot parse — Database and Simulate both died that way on the demo roster.

### Each regime slice is shrunk toward *that* table's prior
Handing every slice the busiest-regime blob is how a heads-up book belonging to
a 6-max regular got measured against 6-max VPIP. 55% is a nit heads-up and a
maniac at that prior.

### `_translate_rate` shrinks before translating
An unshrunk 0% or 100% has no finite log-odds, and a tiny sample would
translate into an extreme claim. Source and target populations must be the same
field the rest of the pipeline uses: translating a home-game 6-max observation
against the online 24% VPIP table made it look like a huge heads-up deviation,
and shrinkage after the merge cannot undo it.

### `fold_accuracy` is measured against one fixed bar, not each player's own sizes
Pricing it per-player inverts the signal: somebody who calls too much gets
shown smaller bets, which lowers his own breakeven until he clears it. On real
players the personalised version rated two known-weak opponents as *more*
disciplined than two known-strong ones. The fixed bar is the frequency a
competent player defends at, and distance from it is the measure.
