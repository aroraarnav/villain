"""Plain-language definitions for everything the interface shows.

A profiler is useless if the reader has to already know what "fold vs turn bet,
47%, breakeven 40%" implies. Every statistic here carries three sentences: what
the number counts, what a high one means for you, and what a low one means --
because for most of these, both directions are exploitable and they call for
opposite play. Overfolding says bluff more; underfolding says stop bluffing and
value bet. Getting that backwards costs more than not knowing the number.

Kept as data rather than prose in the template so the same words appear in the
UI, in exports, and in any future surface, and so a test can assert that no
statistic reaches the screen without an explanation.
"""

from __future__ import annotations

#: stat -> what it counts, what high means, what low means.
STATS: dict[str, dict[str, str]] = {
    "vpip": {
        "what": "How often they voluntarily put money in before the flop — any "
                "call or raise, but not a blind they were forced to post.",
        "high": "Playing too many hands. Their range is full of weak holdings, "
                "so they will miss most flops.",
        "low": "Very selective. When they enter a pot, respect it — but their "
               "blinds are free money.",
    },
    "pfr": {
        "what": "How often they raise before the flop.",
        "high": "Aggressive preflop. Expect to be raised, and defend wider.",
        "low": "Passive entry — they call more than they raise, which caps how "
               "strong their range can be.",
    },
    "three_bet": {
        "what": "When someone raises in front of them, how often they re-raise.",
        "high": "Re-raising more often than a value range allows, so many are "
                "bluffs. Fight back wider.",
        "low": "Their re-raises are the top of their range. Fold anything "
               "marginal to one.",
    },
    "fold_to_three_bet": {
        "what": "After raising, how often they fold to a re-raise.",
        "high": "Their opening range collapses under pressure. Re-raise them light.",
        "low": "They defend their raises. Re-raise for value, not as a bluff.",
    },
    "four_bet": {
        "what": "Facing a three-bet, how often they raise again rather than "
                "call or fold.",
        "high": "They four-bet often, so a three-bet from you is not the end "
                "of the hand. Three-bet a tighter range and be ready to play "
                "for stacks.",
        "low": "They almost never four-bet. Your three-bets can be wide, and "
               "when they do raise again, believe it.",
    },
    "five_bet": {
        "what": "Facing a four-bet, how often they raise again. Rare enough "
                "that it takes a lot of hands to mean anything.",
        "high": "They five-bet light, so four-betting as a bluff is expensive "
                "against them.",
        "low": "They fold or call four-bets. Four-bet bluffs get through.",
    },
    "squeeze": {
        "what": "After a raise and at least one caller, how often they "
                "re-raise.",
        "high": "They punish limped-along pots. Do not flat behind a raiser "
                "with a hand you cannot continue with.",
        "low": "They let multiway pots go to the flop, so cold-calling behind "
               "a raiser is cheap.",
    },
    "cold_call": {
        "what": "How often they call a raise having not yet put money in.",
        "high": "They call raises with hands that should fold or re-raise, so "
                "their calling range is capped and full of second-best hands.",
        "low": "They three-bet or fold rather than call, so a flat call from "
               "them is a narrow, deliberate range.",
    },
    "rfi": {
        "what": "How often they open the pot with a raise when it is folded "
                "to them.",
        "high": "They open a lot, so their opening range is weak on average. "
                "Three-bet and defend wider against it.",
        "low": "They open rarely, so an open is a real hand. Give their raises "
               "more respect and steal their blinds more often.",
    },
    "bb_defend": {
        "what": "In the big blind facing a raise, how often they play rather "
                "than fold.",
        "high": "Hard to steal from. Open a tighter range against them.",
        "low": "Giving up their blind. Raise every button — the folds alone pay.",
    },
    "limp": {
        "what": "How often they enter the pot by calling the big blind instead "
                "of raising, when first to act.",
        "high": "A limping range is capped and passive. Raise over the top and "
                "bet the flop.",
        "low": "Normal — good players rarely limp.",
    },
    "fold_to_steal": {
        "what": "In the blinds facing a late-position raise, how often they fold.",
        "high": "Their blinds are free. Open every button and small blind.",
        "low": "They defend. Steal with hands that can play after the flop.",
    },
    "cbet:flop": {
        "what": "Having raised before the flop, how often they bet the flop.",
        "high": "They bet their whole range, so most of it is air. Check-raise "
                "and float wide.",
        "low": "A checked flop means they missed. Take the pot away.",
    },
    "cbet:turn": {
        "what": "Having bet the flop, how often they fire again on the turn.",
        "high": "They keep barrelling with too much. Call down lighter.",
        "low": "They give up on the turn. Float the flop and take it next street.",
    },
    "fold_vs_bet:flop": {
        "what": "Facing a bet on the flop, how often they fold.",
        "high": "Bet every flop against them — the folds alone show a profit.",
        "low": "They will not fold flops. Bet for value, not as a bluff.",
    },
    "fold_vs_bet:turn": {
        "what": "Facing a bet on the turn, how often they fold.",
        "high": "The most profitable leak there is — turn pots are big. Fire a "
                "second barrel with anything.",
        "low": "Sticky on the turn. Value bet thinner and abandon bluffs.",
    },
    "fold_vs_bet:river": {
        "what": "Facing a bet on the river, how often they fold.",
        "high": "Bluff every river you get to, and size up.",
        "low": "A calling station on the river. Bet made hands, never bluff.",
    },
    "fold_to_cbet:flop": {
        "what": "Facing a bet from the player who raised before the flop, how "
                "often they fold.",
        "high": "They surrender the flop. Raise more hands and bet every one.",
        "low": "They stick around. Continue with real equity, not automatically.",
    },
    "fold_to_cbet:turn": {
        "what": "Facing a second barrel from the preflop raiser, how often they fold.",
        "high": "The second barrel prints. Keep firing.",
        "low": "They call down. Barrel with hands that improve, not with air.",
    },
    "fold_to_cbet:river": {
        "what": "Facing a third barrel from the preflop raiser, how often they fold.",
        "high": "They give up the river after coming this far. Fire the last one.",
        "low": "They call the river down. Only bet the third barrel for value.",
    },
    "cbet:river": {
        "what": "Having bet the turn, how often they fire the river as well.",
        "high": "They triple-barrel too often, so the last bet is frequently air. "
                "Call down with any hand that beats a bluff.",
        "low": "They shut down on the river. Their bet there is the real thing — "
               "fold marginal hands and take the free showdown when they check.",
    },
    "raise_vs_bet:turn": {
        "what": "Facing a bet on the turn, how often they raise.",
        "high": "Turn raises are rarely bluffs at most frequencies; at this one "
                "they are. Do not fold the top of your range to them.",
        "low": "They never raise turns, so a second barrel is close to risk-free.",
    },
    "raise_vs_bet:river": {
        "what": "Facing a bet on the river, how often they raise.",
        "high": "Raising rivers this often cannot be all value. Call with hands "
                "that beat a bluff.",
        "low": "A river raise from them is the nuts. Fold everything else to it.",
    },
    "check_raise:flop": {
        "what": "Having checked the flop, how often they raise a bet.",
        "high": "Dangerous to bet into. Check back more of your marginal hands.",
        "low": "Their check is a surrender. Bet every flop they check to you.",
    },
    "donk:flop": {
        "what": "Betting into the previous street's aggressor instead of checking "
                "to them.",
        "high": "Unusual and usually unbalanced — often a weak made hand "
                "protecting itself.",
        "low": "Normal. They check to the raiser as expected.",
    },
    "wtsd": {
        "what": "Having seen a flop, how often they reach showdown.",
        "high": "They want to see cards. Value bet thin; bluffing is throwing "
                "money away.",
        "low": "They are looking for a reason to fold. Barrel relentlessly.",
    },
    "wsd": {
        "what": "Of the showdowns they reach, how often they win.",
        "high": "They get there with real hands. Their calls mean something.",
        "low": "They arrive with weak holdings — paying off too often.",
    },
    "wwsf": {
        "what": "Having seen a flop, how often they end up winning the pot.",
        "high": "They take their share of pots after the flop.",
        "low": "They see flops and give up. Bet at them.",
    },
    "aggression:flop": {
        "what": "Of everything they do on the flop, how much is betting or raising.",
        "high": "Constant pressure. Let them bet your good hands for you.",
        "low": "Passive — they check and call. Bet your value hands relentlessly.",
    },
    "aggression:turn": {
        "what": "Of everything they do on the turn, how much is betting or raising.",
        "high": "Bets more turns than any range supports. Call down lighter.",
        "low": "Gives up the turn. Take the pot when they check.",
    },
    "aggression:river": {
        "what": "Of everything they do on the river, how much is betting or raising.",
        "high": "Bluffs rivers often. Bluff-catchers are printing — just call.",
        "low": "Only bets rivers with the goods. Fold to their river bets.",
    },
    "river_bet_bluff": {
        "what": "Of the river bets where they later showed, how many were weak "
                "hands.",
        "high": "They bluff rivers a lot. Call wider.",
        "low": "River bets are value. Believe them.",
    },
    "sd_light_call": {
        "what": "How often they call down and show a weak hand.",
        "high": "They pay off. Value bet every made hand.",
        "low": "Their calls are strong. Do not bet thin against them.",
    },
    "bb_fold_to_open": {
        "what": "In the big blind facing a raise, how often they fold.",
        "high": "Their blind is free money. Raise it with anything.",
        "low": "They defend. Open hands that can play after the flop.",
    },
    "call_vs_bet:flop": {
        "what": "Facing a bet on the flop, how often they call rather than "
                "fold or raise.",
        "high": "They float wide. Keep betting the turn -- most of that "
                "calling range missed.",
        "low": "They fold or raise instead of calling; their flop calls mean "
               "something.",
    },
    "call_vs_bet:turn": {
        "what": "Facing a bet on the turn, how often they call rather than "
                "fold or raise.",
        "high": "Sticky on the turn. Value bet thinner and cut the bluffs.",
        "low": "They will not call turns; barrel them.",
    },
    "call_vs_bet:river": {
        "what": "Facing a bet on the river, how often they call rather than "
                "fold or raise.",
        "high": "They pay off. Bet every made hand and never bluff.",
        "low": "They fold rivers. Bluff the last street relentlessly.",
    },
    "call_cbet:flop": {
        "what": "Facing a continuation bet from the preflop raiser, how often "
                "they call rather than fold or raise.",
        "high": "They call the flop wide and give up later. Fire again.",
        "low": "They fold or raise the flop rather than calling along.",
    },
    "raise_vs_bet:flop": {
        "what": "Facing a bet on the flop, how often they raise.",
        "high": "Aggressive with their flop range. Bet thinner and be ready to "
                "fold to the raise.",
        "low": "They never raise flops, so your bets face no real risk.",
    },
    "check_raise:turn": {
        "what": "Having checked the turn, how often they raise a bet.",
        "high": "Dangerous to bet into. Check back marginal hands on the turn.",
        "low": "Their turn check is a surrender; bet it freely.",
    },
    "probe:turn": {
        "what": "Betting the turn after the previous street's aggressor gave up.",
        "high": "They punish a checked flop. Continue more of your range.",
        "low": "They let checked pots go. Stab at the turn yourself.",
    },
    "probe:river": {
        "what": "Betting the river after the previous street's aggressor gave up.",
        "high": "They attack checked rivers; their bets there are often air.",
        "low": "They check rivers back. Value bet thinner against them.",
    },
    "tank_fold": {
        "what": "How often a fold came after a long pause.",
        "high": "Long pauses mean they are looking for a fold. Their quick "
                "actions are the strong ones.",
        "low": "They act at a steady speed — less timing information available.",
    },
    "tank_fold:flop": {
        "what": "How often a flop fold came after a long pause.",
        "high": "Flop tanks that end in a fold mean the bet already worked. "
                "Respect a flop tank-call.",
        "low": "Steady flop timing — less to read on the first street.",
    },
    "tank_fold:turn": {
        "what": "How often a turn fold came after a long pause.",
        "high": "Turn tanks are expensive tells. A pause-then-fold means "
                "pressure worked; a pause-then-call means give up.",
        "low": "Steady turn timing — lean on frequencies instead.",
    },
    "tank_fold:river": {
        "what": "How often a river fold came after a long pause.",
        "high": "River tanks that fold mean the bluff was always getting "
                "there. Size up next time.",
        "low": "Steady river timing — less clock information on the end.",
    },
    "snap_call": {
        "what": "How often a call came instantly.",
        "high": "Instant calls are weak-but-live hands, never traps. Keep betting.",
        "low": "They think before calling — timing tells you less.",
    },
    "snap_call:flop": {
        "what": "How often a flop call came instantly.",
        "high": "Snap flop calls are weak-but-live. Barrel the turn.",
        "low": "They pause before calling flops — less of a speed tell.",
    },
    "snap_call:turn": {
        "what": "How often a turn call came instantly.",
        "high": "Snap turn calls cannot raise. Fire the river.",
        "low": "They think on the turn — speed is less informative.",
    },
    "snap_call:river": {
        "what": "How often a river call came instantly.",
        "high": "Snap river calls are bluff-catchers. Value bet thinner.",
        "low": "They deliberate on rivers — less to read from speed.",
    },
}

#: Words the interface uses that carry a specific meaning.
TERMS: dict[str, str] = {
    "guesswork": "Under 50 hands. Not enough to read anyone — treat everything "
                 "here as a placeholder.",
    "thin": "50 to 150 hands. Preflop numbers are becoming real; anything about "
            "turns and rivers is still mostly guesswork.",
    "usable": "150 to 500 hands. Preflop reads are reliable and the bigger "
              "postflop leaks will show.",
    "solid": "Over 500 hands. Trust it, including the finer postflop numbers.",
    "watch": "Seen, but not confirmed. Probably real and not yet worth acting "
             "on -- no price is given because the tool is not confident enough "
             "to tell you what it is worth. Keep playing them and it will "
             "either firm up or disappear.",
    "tentative": "The evidence leans this way but could still be luck. Worth "
                 "knowing, not worth changing your whole game for.",
    "likely": "Probably real. Act on it, and keep watching.",
    "strong": "The sample supports this. Attack it.",
    "bb/100": "Big blinds won per 100 hands — the standard unit of poker "
              "winrate. At 5c/10c stakes, 1 bb/100 is 10 cents per 100 hands.",
    "breakeven": "The frequency at which the exploit stops making money. Bluffing "
                 "a two-thirds pot bet needs them to fold 40% of the time to "
                 "break even, so anything above that is profit.",
    "field": "What a typical player at this table size does — context for "
             "whether a number is unusual, not a target to aim at. After "
             "the pool has been fit, this is that pool, not a generic "
             "online table.",
    "against you": "The same statistic counted only on the decisions where you "
                   "were the one they were facing. Read against their own game "
                   "rather than the field: the interesting thing is not that "
                   "they fold 70% to your rivers, it is that they fold 45% to "
                   "everybody else's.",
    "otherwise": "The same statistic on every decision that was not against "
                 "you. Your own hands are taken out of it, because a number "
                 "compared with a total that contains it can only ever "
                 "understate the difference.",
    "adjustment": "A statistic where they treat you differently from everyone "
                  "else, by enough to change what you should do and with "
                  "enough hands behind it to believe. Most players, most of "
                  "the time, have none — that is the normal result, not a "
                  "missing feature.",
    "estimate": "The frequency after accounting for sample size. A player who "
                "folded 3 of 3 is not a 100% folder, and this pulls that back "
                "toward reality.",
    "95% range": "Where their true frequency probably sits. A wide range means "
                 "not enough hands yet.",
    "available": "A ranking of which leaks to attack first, in big blinds per "
                 "100 hands, scaled by a capture fraction (you cannot bluff "
                 "every river you reach with the nuts). Ordering is the "
                 "signal; do not bank the number as a live winrate.",
    "confidence": "How much of this read comes from their actual hands rather "
                  "than from assumptions about players in general. Archetype "
                  "confidence is a 10-way posterior; skill confidence is "
                  "coverage plus volume — same word, two formulas.",
    "unknown": "Not enough hands for a skill comparison. The number would "
               "mostly measure sample size, so it is withheld.",
    "percentile": "The share of every possible hole-card combination on that "
                  "board your hand beats -- not your chance to win the pot, "
                  "which depends on what the other hand actually is. "
                  "\"Usually X%\" is what a model fit on this database's own "
                  "revealed hands says a line like that typically represents.",
}

#: What a low score in each rated area means for you. The rating knows these
#: things whether or not a statistical test clears, and saying nothing about
#: them makes a weak player look unreadable.
#: What each rating component measures, and what a good or bad score means.
#: Split by direction on purpose: the single blurb this replaced was written as
#: a weakness, so a player scoring 100 on hand selection was told they "play
#: the wrong hands".
#:
#: ``stats`` names the frequencies the component is computed from, so the
#: breakdown can offer the same "see the hands" evidence the leaks do.
COMPONENTS: dict[str, dict] = {
    "Hand selection": {
        "measures": "Which hands they enter pots with, and whether they enter "
                    "by raising or by calling.",
        "low": "They play the wrong hands -- too many, too few, or entering "
               "pots by calling. Punish it before the flop by raising more of "
               "your own hands against them.",
        "high": "Their starting hands are sensible for the table size. No "
                "cheap edge before the flop.",
        "stats": ["vpip", "limp", "cold_call", "rfi"],
    },
    "Preflop aggression": {
        "measures": "How often they raise rather than call the hands they "
                    "choose to play.",
        "low": "They call where they should raise. Their calling range is "
               "capped, so bet at them after the flop and believe them when "
               "they finally raise.",
        "high": "They raise the hands they play, so their range is not capped "
                "when they call. Do not read a flat call as weakness.",
        "stats": ["vpip", "pfr", "three_bet", "four_bet", "five_bet", "squeeze"],
    },
    "Postflop aggression": {
        "measures": "How often they bet and raise after the flop, street by "
                    "street.",
        "low": "Their betting after the flop is off -- too passive to protect "
               "their good hands, or too busy to have them. Either way their "
               "bets and checks say more than they should.",
        "high": "Their betting frequency is in a normal band, so a bet from "
                "them carries the usual information and no more.",
        "stats": ["aggression:flop", "aggression:turn", "aggression:river"],
    },
    "Discipline vs bets": {
        "measures": "How often they fold when facing a bet, against the "
                    "frequency the pot odds call for.",
        "low": "They fold at the wrong frequencies when facing bets. "
               "Whichever way they err, the answer is to bet more or bluff "
               "less accordingly.",
        "high": "They fold about as often as the price demands, so neither "
                "bluffing nor value betting thin is free against them.",
        "stats": ["fold_vs_bet:flop", "fold_vs_bet:turn", "fold_vs_bet:river"],
    },
    "Showdown judgment": {
        "measures": "How often they reach showdown, and how often they win "
                    "when they do.",
        "low": "They arrive at showdown with the wrong hands -- paying off "
               "too often, or folding hands that were good. Value bet thinner "
               "against them.",
        "high": "The hands they take to showdown are the right ones. Thin "
                "value bets will not get paid as often as usual.",
        "stats": ["wtsd", "wsd"],
    },
    "Bet sizing": {
        "measures": "Whether their bet sizes vary with the situation or stay "
                    "the same regardless.",
        "low": "Their sizes are readable or badly chosen. One size for every "
               "situation means the size tells you nothing they meant it to, "
               "and often a lot they did not.",
        "high": "Their sizing does not give them away.",
        "stats": [],
    },
    "Resistance to exploitation": {
        "measures": "How much money the leaks found against them are worth in "
                    "total, weighted by how much evidence there is.",
        "low": "There is real money available against them -- see the leaks "
               "listed above.",
        "high": "No leak found so far is worth much. On a thin sample that "
                "means 'not yet found', not 'not there'.",
        "stats": [],
    },
}

#: Above this a component reads as a strength rather than a weakness.
COMPONENT_STRONG = 78.0


def component_entry(name: str) -> dict | None:
    return COMPONENTS.get(name)


def component_reading(name: str, score: float) -> str:
    """The explanation that matches which way the score actually went."""
    entry = COMPONENTS.get(name)
    if not entry:
        return ""
    return entry["high"] if score >= COMPONENT_STRONG else entry["low"]


def component_stats(name: str) -> list[str]:
    entry = COMPONENTS.get(name)
    return list(entry["stats"]) if entry else []


def component_help(name: str) -> str | None:
    """The weakness reading. Kept for the callers that only show weak spots."""
    entry = COMPONENTS.get(name)
    return entry["low"] if entry else None


#: How the table-size split is explained.
REGIMES: dict[str, str] = {
    "hu": "Heads-up. Everyone plays far more hands here, so a 55% VPIP is tight.",
    "3max": "Three-handed. You are in a blind two hands out of three.",
    "6max": "Six-handed. The standard short table.",
    "full": "Seven or more players. The tightest of the four.",
}


def stat_help(stat: str) -> dict[str, str] | None:
    """Explanation for a stat, falling back to the street-agnostic version."""
    if stat in STATS:
        return STATS[stat]
    base = stat.rsplit(":", 1)[0]
    return STATS.get(base)


#: The same statistics said as something one player does to another.
#:
#: An adjustment is not a frequency, it is a person reacting to you, and "fold
#: vs turn bet, 19%" does not read as one. The exploit layer already prefers
#: behavior to statistics for the same reason; this is that sentence with
#: *you* in it, which is the whole difference between the two sections.
#: Kept short enough to sit in a column beside the numbers: the terminal card
#: has to fit on one screen, and a phrase that wraps costs more than the extra
#: words are worth.
VERSUS_BEHAVIOR: dict[str, str] = {
    "fold_vs_bet": "folds to your {street} bets",
    "call_vs_bet": "calls your {street} bets",
    "raise_vs_bet": "raises your {street} bets",
    "fold_to_cbet": "folds to your {street} c-bet",
    "cbet": "c-bets into you on the {street}",
    "three_bet": "re-raises your opens",
    "fold_to_three_bet": "folds to your three-bets",
    "fold_to_steal": "folds their blinds to you",
    "bb_defend": "defends their blind vs you",
}


def versus_behavior(stat: str) -> str:
    """How to say ``stat`` as a thing this player does to you."""
    base, _, street = stat.partition(":")
    phrase = VERSUS_BEHAVIOR.get(base)
    if phrase is None:
        return stat
    return phrase.format(street=street) if street else phrase


def payload() -> dict:
    return {"stats": STATS, "terms": TERMS, "regimes": REGIMES}
