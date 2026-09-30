# How Villain works

The long version of the short explanation in the [README](../README.md):
what a read contains, and the statistics behind it.

## Reading a read

The screen is the app's; what follows is the same read as data, which is what
`villain profile NAME` prints and what every tab is rendered from.

```json
{
  "name": "seat 4",
  "hands": 183,
  "sample_quality": "usable",
  "regime": "hu",
  "archetype": "tag",
  "archetype_confidence": 0.51,
  "archetype_mix": [["tag", 0.51], ["trapper", 0.20], ["station", 0.15]],
  "leaks": [
    {
      "headline": "Folds too often to river bets",
      "severity_bb100": 2.3,
      "tier": "tentative",
      "value": 0.51, "breakeven": 0.40, "population": 0.45, "sample": 16,
      "behavior": "...", "why": "...", "do": "...", "dont": "..."
    }
  ],
  "watchlist": [], "adjustments": [],
  "skill": {
    "score": 69, "tier": "strong", "confidence": 0.54,
    "observed_bb100": -1.0, "adjusted_bb100": -0.2,
    "exploitability_bb100": 2.3
  },
  "skill_components": [...], "weak_spots": [...], "stats": {...}
}
```

Note what it does *not* claim: 183 hands buy a bucket at 51% confidence and one
leak still labeled tentative, which is what a session this size contains. The
full object also carries `table_mix`, `contributions`, `plan` and the
per-statistic `stats` block with an interval on every frequency.

Each exploit answers four questions — what they are doing (as behavior, not as
a statistic), why it is exploitable (the breakeven arithmetic), what to do, and
the counter-mistake, which matters most because nearly every way of losing
money to a correct read is an over-adjustment.

Leaks are sorted by **bb/100**, what one is worth per 100 hands if you attack
it every time, and labeled `tentative`, `likely` or `strong` by how much comes
from this player's hands rather than from the prior. No leaks usually means not
enough hands yet, and the tool says so rather than inventing something.

## Checking a read

Every exploit and every rating component carries a **see the hands** button —
the board as cards, what they did, what it cost — and any of them replays street
by street. It opens on the hands where the thing actually happened, with the
rest a click away, and says in a line what the rate means: *4 of 2,945 — 0%,
almost never. Normal, good players rarely limp.* A statistic with thousands of
instances gets no button, because a list of every hand somebody played is a
denominator rather than evidence. Nothing
extra is stored: contributing hands are found by replaying each hand through
the same extraction the statistics use, so the evidence cannot drift from the
number, being the same code. It is also the fastest way to catch the tool being
wrong; building it surfaced VPIP counting once per preflop *decision* instead
of once per hand.

## Small samples

**The whole problem is small samples.** A home game session is about 200 hands
— 20 to 40 observations of any postflop statistic. A tracker will tell you the
villain folds to 100% of turn bets because he folded the only three he faced,
and betting every turn on that basis is how you donate to a normal player. The
hard part is not computing statistics but knowing which of them mean anything
yet, and everything below follows from that: this is built for a couple of
hundred hands, not a couple of hundred thousand.

**Everything is shrunk toward a population prior and carries its own
uncertainty.** A frequency is a Beta posterior, not a fraction. A prior is a
mean `m` and a strength `s`; `h` hits in `n` chances give
`Beta(m·s + h, (1−m)·s + n − h)`, so the estimate is `(m·s + h) / (s + n)` and
the interval comes out of the same posterior. Three observations barely move
it, three hundred make the prior irrelevant, and nothing has to decide when a
sample became "enough" — the arithmetic does that continuously.

**The prior is fitted from your own pool, not assumed.** With eight or more
players who have five or more chances at a statistic, a Beta-Binomial
method-of-moments fit replaces the built-in online numbers. The mean is the
pool average; the strength comes from the *spread between players*, taking the
total variance and subtracting the binomial sampling noise `m(1−m)/n̄`. What is
left is genuine player-to-player variation, and `s = m(1−m)/between − 1`. If
everyone in your game folds to c-bets at about the same rate, three
observations of a new player should barely move them; if the spread is wide,
the same three carry real information. The fitted population then feeds the
archetype label and the exploit thresholds as well as the shrinkage — measuring
a home game against a generic online field otherwise makes every deviation
wrong by the gap between the two.

**Table size is part of a player's identity.** 55% VPIP is a nit heads-up, a
normal three-handed player and a maniac at a full ring, so statistics are
bucketed by table size as they are *collected*. Reporting them that way is
unreadable, so each player still gets **one** profile, pooled in log-odds: each
table's counts become a deviation from that table's own population, translated
onto the scale of the table they play most, then discounted, because related
games are not the same game. `--by-table` shows the split.

**Reads are earned by data.** Some population frequencies already sit near an
exploit's breakeven point, so a rule firing on the estimate alone would flag
players nobody has observed. Every leak reports how far the evidence moved it
from the prior, and reads are graded rather than dropped.

## Buckets

Ten archetypes — `nit`, `station`, `overfolder`, `maniac`, `lag`, `tag`,
`tight passive`, `loose passive`, `limper`, `trapper` — each a *plan* rather
than a personality: "station" is the bucket whose plan is "value bet thin and
stop bluffing".

Prototypes are deviations from the population **in log-odds**, which is the
difference between working and not: frequencies are bounded, so points on a 70%
base are not the same change as points on 24%. In linear space the same "nit"
prototype produced a six-handed player who plays 2% of hands; in log-odds it
lands at 44% heads-up and 9.5% full ring, which is what "nit" meant in both.

## Matching is a likelihood, not a distance

Each archetype implies a frequency per feature: the population mean shifted by
that archetype's deviation, in log-odds, scaled by the feature's spread. Raw
counts are then scored against each implied frequency with an overdispersed
Beta-Binomial, and the archetype posterior is the product across features,
weighted by how much each feature identifies a plan and discounted because
those features are correlated — VPIP and PFR are not independent measurements.

Shrinking first and *then* measuring distance to a prototype would count the
uncertainty twice and collapse every thin sample onto whichever prototype sits
in the middle. That failure is worth naming because it recurs: a prototype
close to the population center wins every ambiguous player by default, so each
archetype needs a real identity, not just a name. All ten are scored over the
*same* features — one scored only on the features it mentions wins by
mentioning fewer. The bucket a player is "between" is reported rather than
hidden.

## Leaks are priced from pot odds, not from the field

A leak fires on an **absolute** threshold, because what makes a tendency
exploitable is arithmetic, not fashion:

* a bluff of size `f` breaks even at a fold frequency of `f / (1 + f)` — 33% at
  half pot, 40% at two thirds, 50% at pot;
* below a third folds, even the cheapest bluff worth making loses money, so the
  exploit inverts from pressure to thin value;
* a steal risking `r` to win `p` breaks even at `r / (r + p)`;
* facing a bet of size `s` a call needs `s / (1 + 2s)` equity, so a range that
  bets past `v / (1 − s/(1+2s))` — about 1.40× the field at two-thirds pot — is
  betting more than its value hands support, and calling down profits.

That last one is the mirror of the first, and it exists because pricing only
the passive errors made an over-aggressive player look unexploitable rather
than merely unmeasured.

Folding 55% to a two-thirds pot bet is exploitable whether or not the rest of
the pool folds 55% too; population comparisons only *identify* a player, and
appear as context. Severity is the estimated bb/100 from taking the exploit,
from that player's own pot sizes and how often the spot comes up, scaled by
`CAPTURE` in `exploits.py` to the share of spots you can realistically convert
— you cannot bluff a river you reached with the nuts. That constant is an
assumption stated in the source rather than buried, so severities are best read
as a ranking of what to attack first.

## Against you

Every frequency above is measured against a population: how they play
everybody. **Against you** is the same counter sliced to the decisions where
you were the one they were facing, and it is the number that says they have a
read on you rather than a tendency. Folding 70% to your river bets is not
interesting because 70% is high. It is interesting because they fold 45% to
everybody else's.

So the prior is not the field, which has never played you — it is the player.
Population, then the player, then the player against you, all the same
arithmetic one level deeper. The baseline **subtracts the slice out of the
pooled counter first**: the slice sits inside that total, so reading it against
the total compares a number with something that contains it, and the difference
vanishes exactly when the sample grows enough to be worth reading.

Table size is the confound this would otherwise invent. Somebody you have only
played heads-up folds far more there than at their six-handed table, and
pooling raw counts reports that as thirty points of adjustment when they are
merely playing a shorter table. Each table size's slice is therefore measured
against *that* table's baseline, and only the deviation is carried over,
re-expressed on the primary table's scale and discounted.

Two refusals worth knowing about. If every decision they made was against you —
a heads-up database — the baseline is a number subtracted from itself, and
nothing is reported rather than the population deviation twice. And a
difference has to be big enough to change a decision, not merely certain,
because enough hands make any difference certain. Most players, most of the
time, have no adjustment at all; that is the normal result.

Scoped to you and nobody else. Every pair would be quadratic in players and
starved in every cell, while you are the one opponent with enough shared hands
to say anything. Which seat is yours comes from the export itself — PokerNow
names the exporter — falling back to whose cards are visible without having
been shown, since an export shows you your own hand every time and everybody
else's only at a showdown.

## Reading a sitting

A profile is built from frequencies; a sitting is reviewed hand by hand,
because two hundred hands put three or four in the pots that decided the night
and a few hundred preflop decisions in spots with known answers.

**Preflop against a reference.** Only your own export shows every hand you
folded, so your first decision in each preflop spot -- first in by seat, the
big blind facing a raise, the small blind facing one, and your answer to a
3-bet -- is checked against a reference range, and the hands on the wrong side
of it are named. The references are hand-written simplified solver outlines,
each held by a test to the solver frequency the GTO table already cites for
that spot. They are precise enough to name a clear misfold and not precise
enough to argue about a hand at the edge. A spot is flagged only when the gap
is eight points or more *and* at least three named hands sit behind it: a
tight night dealt nothing but trash is not a leak.

**The big pots.** All-in equity splits each result into what the pot was worth
when the money went in and what the runout did. Two decision shapes are
flagged for another look, using the stored per-street hand strength: a big
turn or river bet with a hand in the bottom half of what the board allows,
after the opponent has already raised or called a raise; and a river raise
into a big bet with a hand that mostly beats bluffs. The rule finds the shape,
not the verdict.

**Folds by bet size**, heads-up, against the most a bet that size lets you
fold -- bet over pot-plus-bet. Multiway pots are left out: against two
players, folding more than one bettor's price allows is correct.

**Fix first** ranks every flagged finding by how many decisions it touched. A
misfold has no price -- that needs the value of the hand you did not play --
so the count is the honest scale, and a big-pot flag counts once per 20 big
blinds put in.

**Across sittings.** The same measurements over your last five sittings say
whether a leak is `new`, `persistent` or `fixed`: one bad night is the cards,
the same gap four nights running is how you play.

**Opponents.** Each opponent's tips come from tonight's hands and quote their
counts. Tonight is compared with the rest of their history inside one table
size, and reported only past a ten-point gap with a two-proportion z of 2. When
the change contradicts what their usual archetype's plan assumes -- a limper
who stopped limping -- the read is marked stale. A profile's **How to beat
them now** uses the last sitting they played the same way.

## Skill

Rating a player by results is rating their luck, so results carry the smallest
weight, and only after all-in pots are rescored by equity so a cooler and a
punt stop looking alike. The rest is **fundamentals** — distance from competent
play for that table size, penalized asymmetrically where the errors are — and
**resistance to exploitation**, the bb/100 the exploit layer can find, weighted
by sample size, because "no leaks found" and "no leaks yet findable" are the
same number and only one is a compliment.

## Remembering people

Hands are the source of truth and statistics are a disposable cache:
definitions change, and `villain rebuild` recomputes every profile from stored
hands rather than leaving old players wrong until they sit down again.

The same human is one screen name at one table and a near-miss of it at the next, so
accounts are aliases pointing at an internal player. Candidate merges come from
name similarity (case, punctuation and trailing digits stripped) and a Bayes
factor over their statistics — one player against two — which stops two tight
players being merged for nothing more than both being tight. Nothing merges
automatically, and accounts dealt into the same hand can never be linked.

## Learning from your own pool

Two models learn from your own database, and each refuses rather than returning
something authoritative-looking and wrong. **Priors** are re-estimated from your
own players by a Beta-Binomial moments fit, so a home game stops being measured
against an online population and the spread between your players sets how much a
new sample is trusted — fitted automatically on import, once eight players clear
the bar. **Hand strength** is gradient boosting from a line (street, action,
sizing, position, board texture, time taken) to the strength behind it, trained
on revealed cards and needing 300+ revealed decisions; the Hero tab fits it when
it needs it.

## Known limitations

* **Showdown data is biased.** Villains' cards are revealed only at showdown,
  and hands reaching showdown skew toward calling lines and away from the
  bluffs that took the pot down uncontested. The strength model therefore
  *underestimates* how weak betting ranges are. The exporting player's own
  cards are visible on every hand and those rows are marked unbiased, but the
  bias is reduced, not removed.
* **Severity constants are assumptions.** The breakeven thresholds are derived;
  the capture fractions are judgment calls. So is `ADJUSTMENT_PRIOR` in
  `dynamics.py`, which decides how much evidence it takes to believe somebody
  is playing you differently: fitting it would need pairwise samples across
  many players, and a home game has one pair worth counting.
* **Adjustments are never priced.** A leak is worth a stated bb/100; an
  against-you shift is not, because what it is worth depends on how you were
  playing when they made it, and that is not in the hand history.
* **Side pots are approximate.** Each player's equity is capped at the pot they
  were actually eligible for, so a short all-in is no longer credited with money
  it could never have won, but the split between layered pots is not modeled
  and those hands carry a `side_pot` flag.
* **Built-in priors are pool-agnostic.** They describe a generic online
  population until your own pool can replace them, which happens automatically
  on import once eight players clear the bar. The fitted population then feeds
  the archetype label and the exploit thresholds too, not just the shrinkage.
* **PokerNow is the only parser so far.** The format registry in
  `villain/parsers/` takes new sites without touching anything downstream.