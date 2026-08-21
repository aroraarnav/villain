"""What each leak means, in words a player can act on.

:mod:`villain.exploits` decides whether a tendency is exploitable. This module
says what to do. Four fields, in the order a player needs them: ``behavior``,
``why``, ``do``, ``dont`` (the counter-mistake -- usually an over-adjustment).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Entry:
    behavior: str
    why: str
    do: str
    dont: str


#: Keyed by ``Rule.id`` in :mod:`villain.exploits`.
PLAYBOOK: dict[str, Entry] = {
    "folds_blinds": Entry(
        behavior=(
            "They give up their blind rather than play out of position. When you "
            "raise from late position, they fold and wait for a better spot that "
            "mostly never comes."
        ),
        why="A raise risks a small amount to win the blinds.",
        do=(
            "Raise every button and small blind against them, regardless of what "
            "you hold. Keep the size small, around 2.2 to 2.5 big blinds: you are "
            "buying folds, and a bigger raise pays more when they do wake up with "
            "something."
        ),
        dont=(
            "Do not keep firing after they defend. A player who folds this often "
            "is only continuing with real hands, so their call means far more "
            "than a normal player's would."
        )
    ),
    "folds_to_three_bet": Entry(
        behavior=(
            "They open plenty of pots but abandon them the moment somebody "
            "re-raises. Their opening range is much wider than the range they "
            "will actually play a big pot with."
        ),
        why="They are raising a range they cannot defend.",
        do=(
            "Re-raise their opens light, especially in position. Hands that flop "
            "well but cannot call a raise -- suited connectors, suited aces, "
            "small pairs -- are ideal, since you win immediately most of the time "
            "and have a playable hand when you do not."
        ),
        dont=(
            "Do not re-raise into the same player twice in quick succession with "
            "nothing."
        )
    ),
    "no_three_bet": Entry(
        behavior=(
            "They almost never re-raise before the flop. They call with good "
            "hands rather than raising them, so their calls contain everything "
            "and their raises contain almost nothing."
        ),
        why=(
            "Two edges, in opposite directions. Their flat calls are capped, so "
            "you can open far wider and continue after the flop knowing they "
            "rarely have a monster."
        ),
        do=(
            "Open wider from every seat and bet the flop freely against their "
            "calls. When they finally re-raise, fold anything that is not "
            "premium; you are getting a free look at the top of their range."
        ),
        dont="Do not confuse their passivity with weakness after the flop."
    ),
    "limps": Entry(
        behavior=(
            "They enter pots by calling the big blind instead of raising. Their "
            "limping range is wide, weak, and full of hands they are hoping to "
            "see a cheap flop with."
        ),
        why=(
            "A limping range is capped -- strong hands would usually raise -- and "
            "it is built for seeing flops, not for playing big pots."
        ),
        do=(
            "Raise over their limps with a wide range, sized to around 4 big "
            "blinds plus one for each limper. Then bet the flop whether or not "
            "you connect -- they fold everything they miss, and they miss most of "
            "the time."
        ),
        dont=(
            "Do not automatically continue on the turn when they call the flop. A "
            "limp-caller who calls a flop bet usually has something real."
        )
    ),
    "no_defend": Entry(
        behavior=(
            "They surrender their big blind far too often, folding hands that are "
            "well worth a call against a late-position raise."
        ),
        why=(
            "The big blind is getting a better price than any other seat -- money "
            "is already in the pot on their behalf."
        ),
        do=(
            "Attack their big blind with any two cards from late position. A "
            "minimum raise is enough; you do not need a big size to win a pot "
            "nobody is defending."
        ),
        dont="Do not extend this to the times they do defend."
    ),
    "overfold_flop": Entry(
        behavior=(
            "They fold the flop whenever they miss. They call preflop hoping to "
            "connect, and when they do not, they are done with the hand."
        ),
        why=(
            "Any two cards miss the flop most of the time -- that is just how the "
            "deck works."
        ),
        do=(
            "Bet every flop against them, in position or out. Keep it small, "
            "around a third to half the pot: you are charging them for a fold, "
            "not building a pot, and a small bet risks less to win the same "
            "amount."
        ),
        dont=(
            "Do not fire a second barrel on the same automatic basis. Their flop "
            "call means they actually have something, and the turn is a different "
            "decision."
        )
    ),
    "overfold_turn": Entry(
        behavior=(
            "They call the flop with hands that hoped to improve, then fold the "
            "turn when the card does not help them."
        ),
        why=(
            "This is usually the most valuable leak on the list, because turn "
            "pots are big."
        ),
        do=(
            "Fire the second barrel with everything you bet the flop with. "
            "Two-thirds pot or larger -- the pot is big enough that the fold "
            "equity is worth buying, and your hand does not need to be part of "
            "the reason."
        ),
        dont="Do not keep going on the river automatically."
    ),
    "overfold_river": Entry(
        behavior=(
            "They get to the river and cannot call. They talk themselves out of "
            "hands they held all the way, and fold for the last bet."
        ),
        why=(
            "By the river their range is defined and often full of hands that "
            "were never going to be good."
        ),
        do=(
            "Bluff every river you reach with a hand that cannot win a showdown. "
            "Size up -- three-quarters pot or more. At this fold frequency they "
            "are folding to the decision, not to the price, so you may as well "
            "charge them properly for it."
        ),
        dont=(
            "Do not bluff the rivers where their hand is obviously strong -- when "
            "they raised the turn, or when the board completed the draw they were "
            "chasing."
        )
    ),
    "overfold_cbet": Entry(
        behavior=(
            "When the preflop raiser bets the flop, they get out of the way. They "
            "treat a continuation bet as information rather than as something to "
            "be tested."
        ),
        why=(
            "They are letting the preflop raiser's range represent more than it "
            "holds."
        ),
        do=(
            "Raise more hands before the flop against them and bet the flop every "
            "time. The two multiply: a wider raising range wins more pots "
            "outright because they will not defend against the bet that follows."
        ),
        dont="Do not read their flop call as weakness because they usually fold."
    ),
    "station_flop": Entry(
        behavior=(
            "They call flop bets with far more than the price justifies -- "
            "any pair, any overcard, any backdoor draw."
        ),
        why=(
            "Below the breakeven fold frequency for the size you are betting, "
            "so the bet shows a profit on its own. The larger effect is what it "
            "does to the streets after: they reach the turn and river holding "
            "hands their line says they folded."
        ),
        do=(
            "Bet every made hand and keep betting. Top pair is a three-street "
            "value hand, second pair is two. Size up as you go, because the "
            "calling range does not tighten the way a normal one does."
        ),
        dont=(
            "Do not bluff the flop expecting a fold, and do not slow down on "
            "the turn -- giving a free card to the widest range at the table "
            "is how the edge gets handed back."
        )
    ),
    "station_cbet": Entry(
        behavior=(
            "They defend against continuation bets far past the price, calling "
            "with hands that cannot continue on most turns."
        ),
        why=(
            "Their defense frequency is below what the size demands, so the "
            "c-bet is profitable with the whole range and the turn is where "
            "the mistake gets paid for."
        ),
        do=(
            "C-bet every flop and barrel the turn with anything that improved. "
            "Their turn range is stuffed with hands that should have folded."
        ),
        dont=(
            "Do not read a flop call as strength. It says almost nothing here."
        )
    ),
    "station_turn": Entry(
        behavior=(
            "They do not fold once they have any piece of the board. Middle pair, "
            "bottom pair, a gutshot -- if it can win, they are calling to find "
            "out."
        ),
        why="Bluffing needs folds to profit, and there are none here.",
        do=(
            "Value bet relentlessly and thinly. Top pair with a weak kicker is a "
            "three-street value hand against this player. Second pair is worth a "
            "bet. Size up -- they are not folding to the price, so charge them."
        ),
        dont=(
            "Do not bluff. Not on a scare card, not with a busted draw, not on "
            "the river because the board looks dangerous."
        )
    ),
    "station_river": Entry(
        behavior=(
            "They call the last bet with almost anything that beats a bluff. Ace "
            "high, bottom pair, a busted draw with a high card -- they want to "
            "see it."
        ),
        why=(
            "River calls are where most players are too tight and this one is too "
            "loose."
        ),
        do=(
            "Bet any made hand on the river for value, and size up. Hands you "
            "would normally check back -- third pair, ace high on a bricked board "
            "-- become bets, because their calling range is wide enough to "
            "contain worse."
        ),
        dont=(
            "Do not raise their river bets light. A player who calls this wide is "
            "not bluffing much themselves; when they lead into you on the river, "
            "they usually have it."
        )
    ),
    "shows_down": Entry(
        behavior=(
            "They see a lot of showdowns. They want to know what you had, and "
            "they will pay for the information."
        ),
        why=(
            "Reaching showdown that often means calling down with hands most "
            "players fold."
        ),
        do=(
            "Value bet thinner than feels comfortable on every street. Hands you "
            "would normally check back for showdown value should bet, because "
            "they will call with worse far more often than usual."
        ),
        dont=(
            "Do not try to blow them off a hand with a big bet. Size is not what "
            "makes them fold -- nothing is."
        )
    ),
    "no_showdown": Entry(
        behavior=(
            "They rarely reach a showdown. Somewhere on every street they find a "
            "reason to let the hand go."
        ),
        why=(
            "A player who folds somewhere in every hand is folding too often "
            "somewhere."
        ),
        do=(
            "Barrel. Pick the street where they seem least comfortable -- usually "
            "the turn -- and bet it consistently. Small sizes work fine; they are "
            "folding to the fact of the bet, not to its size."
        ),
        dont=(
            "Do not assume this means they never have a hand. Their rare "
            "showdowns are strong precisely because everything weak got folded "
            "along the way."
        )
    ),
    "cbets_always": Entry(
        behavior=(
            "They bet the flop every time they raised before it, whether or not "
            "the board helped them."
        ),
        why=(
            "Their betting range is their entire range, which means it is mostly "
            "air."
        ),
        do=(
            "Check-raise them wide on the flop, and float in position with any "
            "backdoor equity to take the pot on the turn when they give up. "
            "Marginal hands play better as raises than as calls here, because the "
            "fold equity is real."
        ),
        dont=(
            "Do not do this every single time. A player who bets that often "
            "usually has a strong hand somewhere in the range, and running the "
            "same play repeatedly is how you find it with the worst of it."
        )
    ),
    "cbets_never": Entry(
        behavior=(
            "They raise before the flop and then check when they miss. Their "
            "check is an announcement that the flop did not help."
        ),
        why="A preflop raiser who checks the flop has given up on the hand.",
        do=(
            "Bet whenever they check to you, in position or out, with any two "
            "cards. Keep it small -- a third of the pot is plenty against a range "
            "that has already surrendered."
        ),
        dont="Do not keep barrelling if they call the stab."
    ),
    "never_check_raises": Entry(
        behavior=(
            "They do not check-raise. When they check, they intend to call or "
            "fold, never to attack."
        ),
        why="This removes the only real risk of betting into them.",
        do=(
            "Bet every flop they check to you, including hands you would usually "
            "check back for pot control. You will get to see the turn on your "
            "terms every time."
        ),
        dont="Do not extend the assumption to their leads and turn raises."
    ),
    "barrels_flop": Entry(
        behavior=(
            "They bet and raise the flop far more often than the hands they "
            "could be holding justify. Every flop is a fight."
        ),
        why=(
            "A flop range that bets this often is mostly hands that missed. "
            "You are being priced out of pots you are winning."
        ),
        do=(
            "Call with any pair and most draws, and float in position with "
            "overcards. Let them bet the turn into you rather than raising -- "
            "their next barrel is more money from the same mistake."
        ),
        dont=(
            "Do not start check-raising light to fight back. Raising folds out "
            "the bluffs you are trying to collect."
        )
    ),
    "barrels_river": Entry(
        behavior=(
            "They keep firing on the river at a frequency no value range can "
            "support, including on boards where nothing they played gets there."
        ),
        why=(
            "By the river their value hands are a small, countable set. Betting "
            "far past that means the rest is air, and a bluff-catcher beats air."
        ),
        do=(
            "Call rivers with any hand that beats a bluff, including ace high "
            "in the right spot. The call does not need to be close to be right."
        ),
        dont=(
            "Do not raise as a bluff, and do not start folding good bluff-"
            "catchers because one call went badly."
        )
    ),
    "barrels_relentlessly": Entry(
        behavior=(
            "They keep betting -- flop, turn, and often the river -- at a rate no "
            "hand-reading can justify. They apply pressure by default rather than "
            "by plan."
        ),
        why=(
            "They cannot possibly have a strong hand often enough to support that "
            "many bets."
        ),
        do=(
            "Call down lighter, and let them keep firing. Middle pair becomes a "
            "call on two streets. Position helps: flat rather than raise, so they "
            "keep bluffing into a hand that is beating them."
        ),
        dont=(
            "Do not try to out-bluff them or raise as a bluff -- you are "
            "attacking the one part of their game that already works."
        )
    ),
    "three_bets_light": Entry(
        behavior=(
            "They re-raise before the flop constantly, at a frequency no value "
            "range can support."
        ),
        why=(
            "A re-raising range that wide is mostly hands that cannot handle "
            "pressure."
        ),
        do=(
            "Call their re-raises far wider in position, and four-bet light from "
            "time to time. Hands that play well post-flop are better calls than "
            "hands that need to hit -- you want to still be there on the flop "
            "when they have nothing."
        ),
        dont=(
            "Do not play a huge pot out of position with a marginal hand just "
            "because you know they are wide."
        )
    ),
    "bluffs_rivers": Entry(
        behavior=(
            "Their river bets are often nothing. They fire the last bullet with "
            "busted draws and hands that cannot win any other way."
        ),
        why="Bluffing the river only works if you get folds.",
        do=(
            "Call rivers far wider than normal. Any pair, sometimes ace high. "
            "Just call -- do not raise, because a raise folds out the bluffs you "
            "are trying to collect."
        ),
        dont=(
            "Do not extend this to their raises. Bluffing a river bet is common; "
            "raising the river as a bluff is rare even among aggressive players."
        )
    ),
    "light_calls": Entry(
        behavior=(
            "They show up at showdown with hands that had no business calling. "
            "They pay to see it and then pay again."
        ),
        why=(
            "Their calling range is far wider than the price justifies, so the "
            "value of your bets goes up on every street -- particularly the thin "
            "ones you would normally check."
        ),
        do=(
            "Value bet every made hand on every street, and size up on the river. "
            "Second pair is a bet. Ace high is sometimes a bet."
        ),
        dont=(
            "Do not bluff. They are calling with hands that beat nothing, which "
            "means they are also calling with hands that beat your bluff."
        )
    ),
    "tank_folds": Entry(
        behavior=(
            "Long pauses before folding. When they tank and then act, the pause "
            "itself is the information."
        ),
        why=(
            "Somebody genuinely deciding is somebody close to folding -- a player "
            "with a strong hand knows what they are doing quickly."
        ),
        do=(
            "When they take a long time and then call, respect it: they found a "
            "reason and it is usually a real hand. When they act instantly, treat "
            "it as the top or bottom of their range, not the middle."
        ),
        dont=(
            "Do not treat online timing as gospel. Disconnections, phones, and "
            "multi-tabling produce pauses that mean nothing."
        )
    ),
    "snap_calls": Entry(
        behavior=(
            "Instant calls. No deliberation, no consideration of folding -- the "
            "call was decided before you bet."
        ),
        why=(
            "An instant call is a hand that was never folding but also never "
            "raising: a medium-strength hand, live but beatable."
        ),
        do=(
            "Keep barrelling. Their snap-call range is full of hands that cannot "
            "stand another bet, and a scare card on the turn or river gives you a "
            "genuine chance to move them off it."
        ),
        dont=(
            "Do not read every fast action the same way. Instant calls are weak; "
            "instant raises are strong."
        ),
    ),
    "tank_folds_flop": Entry(
        behavior=(
            "Long pauses before folding the flop. The deliberation is concentrated "
            "on the first betting street after the cards come."
        ),
        why=(
            "A flop tank that ends in a fold means the bet already worked -- they "
            "looked for a reason to continue and did not find one."
        ),
        do=(
            "When they tank-call a flop, respect it and check back thin value. "
            "When they snap-fold, the next flop bet can be smaller."
        ),
        dont=(
            "Do not read every flop pause the same way. Multiway pots and phones "
            "produce empty tanks -- confirm their timing varies first."
        ),
    ),
    "tank_folds_turn": Entry(
        behavior=(
            "Long pauses before folding the turn. The expensive street is where "
            "their clock gives them away."
        ),
        why=(
            "Turn pots are already large, so a tank-then-fold is someone who "
            "almost called a price that was never going to be right."
        ),
        do=(
            "Fire turns when their flop call looked reluctant, and give up the "
            "moment a turn tank ends in a call."
        ),
        dont=(
            "Do not treat a turn tank as automatic fold equity on the river. "
            "Players who tank-call turns have already committed."
        ),
    ),
    "tank_folds_river": Entry(
        behavior=(
            "Long pauses before folding the river. They talk themselves out of "
            "the final call after staring at it."
        ),
        why=(
            "A river tank that ends in a fold means the bluff was always getting "
            "there -- they hoped to find a reason and did not."
        ),
        do=(
            "Size up river bluffs against them. They fold to the decision, not "
            "the price, so charging more is free when it works."
        ),
        dont=(
            "Do not hero-fold when they tank and then shove. A river tank into "
            "aggression is usually the nuts looking polite."
        ),
    ),
    "snap_calls_flop": Entry(
        behavior=(
            "Instant flop calls. The call was decided before the bet finished "
            "landing."
        ),
        why=(
            "A snap flop call is a hand that was never folding and never raising "
            "-- usually a weak pair or a gutshot, not a trap."
        ),
        do=(
            "Barrel the turn. Their flop snap-call range hates a second bet, "
            "especially on scare cards."
        ),
        dont=(
            "Do not assume every fast flop call is weak in a turbo game. Confirm "
            "they sometimes pause before treating speed as information."
        ),
    ),
    "snap_calls_turn": Entry(
        behavior=(
            "Instant turn calls. No thought about folding or raising -- just the "
            "call."
        ),
        why=(
            "An instant turn call is a medium hand that cannot raise and will not "
            "fold for one more street -- the opposite of a trap."
        ),
        do=(
            "Fire the river. They told you they are not strong enough to raise, "
            "so a scare card or sized-up bet still has fold equity."
        ),
        dont=(
            "Do not check back thin value just because they called fast. "
            "Snap-call ranges still contain second pair."
        ),
    ),
    "snap_calls_river": Entry(
        behavior=(
            "Instant river calls. The decision was made before they looked at the "
            "size."
        ),
        why=(
            "A snap river call is a bluff-catcher, not the nuts. The nuts takes a "
            "moment to consider raising."
        ),
        do=(
            "Value bet thinner on future rivers and skip hero raises when they "
            "snap-call. Their calling range is wide and weak."
        ),
        dont=(
            "Do not turn a missed bluff into a raise because they called fast. "
            "Instant calls still beat air."
        ),
    ),
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


# ---------------------------------------------------------------------------
# combinations
# ---------------------------------------------------------------------------
# A leak described on its own understates the case. Two leaks that point the
# same way multiply: a player who folds too much on the flop *and* never
# check-raises has removed both the reason to fear betting and the cost of
# being wrong, and the right play against the pair is more aggressive than the
# right play against either one.
#
# These are the pairs worth spelling out. Deliberately hand-written and
# deliberately few -- a combinatorial explosion of generated pairings would
# bury the two or three that actually change how you play.


@dataclass(frozen=True)
class Combination:
    leaks: frozenset
    headline: str
    body: str


COMBINATIONS: tuple[Combination, ...] = (
    Combination(
        frozenset({"overfold_flop", "never_check_raises"}),
        "Betting the flop against them is close to free",
        "They fold flops too often and they never check-raise, so a flop bet "
        "has almost no downside and wins the pot outright most of the time. "
        "Bet every single flop -- there is no hand you should be checking back "
        "for protection, because nothing bad happens when you bet.",
    ),
    Combination(
        frozenset({"overfold_turn", "overfold_river"}),
        "One bluff carried across two streets wins almost uncontested",
        "They fold both the turn and the river too often, which means a bluff "
        "does not have to work on the street you start it. Bet the turn, and "
        "bet again on the river when they call -- the second barrel gets there "
        "against a range that already folded everything it was comfortable "
        "folding.",
    ),
    Combination(
        frozenset({"station_turn", "station_river"}),
        "A pure value opponent -- stop bluffing entirely",
        "They will not fold on either of the two streets where the pot is "
        "biggest. Against this player there is no bluffing strategy at all: "
        "every chip you make comes from betting hands that are ahead, and the "
        "single biggest improvement you can make is checking back every hand "
        "that cannot call a raise.",
    ),
    Combination(
        frozenset({"folds_blinds", "no_defend"}),
        "Their blinds are yours for the taking",
        "They neither defend their big blind nor fight for it from the small. "
        "Raise every single time it folds to you in late position, at the "
        "minimum size that gets the job done. This adds up faster than any "
        "postflop edge because it happens on every orbit.",
    ),
    Combination(
        frozenset({"barrels_relentlessly", "bluffs_rivers"}),
        "Let them bluff into you on every street",
        "They fire too often on the turn and their river bets are frequently "
        "nothing. The counter is entirely passive: call with your "
        "bluff-catchers and let them keep betting. Raising costs you money "
        "here, because it folds out exactly the hands you are trying to "
        "collect from.",
    ),
    Combination(
        frozenset({"no_three_bet", "folds_to_three_bet"}),
        "Open everything, then believe them",
        "They rarely re-raise and they fold their own opens to a re-raise. "
        "Raise far more hands than usual against them, and re-raise their "
        "opens light. When they do put in a re-raise of their own, it is the "
        "top of a very tight range -- fold anything marginal without a second "
        "thought.",
    ),
    Combination(
        frozenset({"cbets_always", "barrels_relentlessly"}),
        "Their aggression is automatic, not chosen",
        "They bet the flop with everything and keep betting the turn. That is "
        "not a strategy, it is a habit, and it means their betting range is "
        "the whole range on both streets. Call down lighter than feels right, "
        "and check-raise the flop with hands that can stand a re-raise.",
    ),
    Combination(
        frozenset({"limps", "overfold_flop"}),
        "Raise their limps and take the flop",
        "They limp in with weak hands and then fold the flop when they miss. "
        "This is the cheapest money in the game: raise every limp, bet every "
        "flop, and give up the moment they do anything other than fold.",
    ),
    Combination(
        frozenset({"shows_down", "light_calls"}),
        "A calling machine -- value bet everything",
        "They reach showdown constantly and arrive with weak hands. Bet every "
        "made hand on every street, size up on the river, and never bluff. "
        "Hands you would normally check for pot control are pure value here.",
    ),
    Combination(
        frozenset({"cbets_never", "never_check_raises"}),
        "Every pot they do not bet is available",
        "When they check they have given up, and they will not raise you off "
        "the hand. Stab at every checked pot with any two cards, in or out of "
        "position, and fold cheaply the rare times they come back at you.",
    ),
    Combination(
        frozenset({"tank_folds_turn", "overfold_turn"}),
        "Turn pressure is doing double duty against them",
        "They fold turns too often and their turn folds arrive after a tank. "
        "Fire turns freely, and treat a tank-call as the rare hand that found "
        "a reason to stick around.",
    ),
    Combination(
        frozenset({"snap_calls_flop", "overfold_turn"}),
        "Float the flop snap-call, then take the turn",
        "They call flops instantly with weak-but-live hands and then fold "
        "turns too often. Barrel every turn after a flop snap-call.",
    ),
    # The flop and later-street stickiness rules each price their own street
    # in isolation, but the leaks are not independent: a player who calls too
    # wide on the flop carries that width forward, so the hand that reaches
    # the turn or river is a wider, weaker range than the turn/river numbers
    # alone say it is. Pricing only the later street prices the consequence
    # and not the cause -- the flop leak is why the later one is bigger than
    # it looks.
    Combination(
        frozenset({"station_flop", "station_turn"}),
        "Their flop calls are dragging a wide range into the turn",
        "They will not fold either street, and the flop call is the reason "
        "the turn one is so wide -- hands that should have died on the flop "
        "are still in in showing up on the turn. Value bet both streets for "
        "real money, not just the turn in isolation: the flop bet is what "
        "sets up the wider turn range you are then getting paid by.",
    ),
    Combination(
        frozenset({"station_flop", "station_river"}),
        "A range this sticky on the flop never really thins out",
        "They call flops too wide and rivers too wide, which is the same leak "
        "showing up twice: their range from the flop never actually narrows "
        "the way a normal calling range does. Bet every made hand on both "
        "streets and size up on the river -- the hands that get there are "
        "weaker than a river-only read would suggest.",
    ),
)


def combinations_for(leak_ids) -> list[Combination]:
    """Combinations whose leaks are all present, biggest first."""
    present = set(leak_ids)
    hits = [c for c in COMBINATIONS if c.leaks <= present]
    hits.sort(key=lambda c: -len(c.leaks))
    return hits
