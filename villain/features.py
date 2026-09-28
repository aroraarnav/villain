"""Turning hands into the statistics a profile is built from.

Every counter names its denominator -- that is where trackers disagree.
Frequencies (size-split fold rates are where the money is), sizings and
timing, and showdown truth for the hand-strength model.
"""

from __future__ import annotations

import math
import os
from collections.abc import Iterable

import numpy as np

try:
    from concurrent.futures import ProcessPoolExecutor
except ImportError:              # no process model (e.g. Pyodide/WASM)
    ProcessPoolExecutor = None

from .cards import card_ids
from .equity import equities
from .hero import hero_of
from .model import Act, Hand, Street, postflop_rank
from .priors import regime as regime_of
from .reads import _board_universe, texture
from .stats import VS_HERO, HandView, StatBook, size_bucket, stack_bucket

#: player id -> table-size regime -> book
Books = dict[str, dict[str, StatBook]]
#: (player_id, regime) -> (tank_ms, snap_ms) frozen from a think-time pass
PaceLocks = dict[tuple[str, str], tuple[float, float]]

TANK_MS = 8_000        # absolute fallback before we know a player's pace
SNAP_MS = 1_200        # absolute fallback for a snap
#: Relative pace: tank/snap vs that player's own mean think time once we have
#: enough samples. Absolute floors/ceilings stop a uniformly fast or slow
#: player from looking like every action is a tell.
REL_TANK = 1.75
REL_SNAP = 0.40
MIN_PACE_SAMPLES = 5
FLOOR_TANK_MS = 5_000
CEIL_SNAP_MS = 2_500
BLUFF_PCTILE = 0.35    # showdown strength below this, in a bet, was a bluff


#: Below this many hands, handing work to other processes costs more than it
#: saves -- the hands have to be pickled across, and a small import is already
#: sub-second.
PARALLEL_MIN_HANDS = 4000

#: Leave a core for the UI thread and whatever else is running.
def _default_workers() -> int:
    return max(1, min(8, (os.cpu_count() or 2) - 1))


def _can_spawn() -> bool:
    """Whether workers can re-import ``__main__`` -- false under a REPL or a
    heredoc, where the pool fails only after every child has printed a
    traceback over the real output."""
    import sys
    return bool(getattr(sys.modules.get("__main__"), "__file__", None))


def merge_books(into: Books, extra: Books) -> Books:
    """Fold one set of books into another, in place."""
    for pid, by_regime in extra.items():
        dst = into.setdefault(pid, {})
        for reg, book in by_regime.items():
            if reg in dst:
                dst[reg].merge(book)
            else:
                dst[reg] = book
    return into


def _record_chunk(payload):
    """Worker entry point: stats for one slice of the batch."""
    hands, locks, hero = payload
    books: Books = {}
    for hand in hands:
        record_hand(hand, books, pace_locks=locks, hero=hero)
    return books


def record_hands(hands: Iterable[Hand], books: Books | None = None,
                 workers: int | None = None, progress=None,
                 hero: str | None = None) -> Books:
    """Extract stats for every hand.

    Two passes: accumulate think times, then freeze snap/tank cutoffs from
    those means so every hand in the import is tagged against the same ones.
    The second pass evaluates every holding each board allows and each hand is
    independent once the cutoffs are frozen, so a large import splits across
    processes; counters merge, so the division cannot change the result."""
    hands = list(hands)
    books = books if books is not None else {}
    scratch: Books = {}
    for hand in hands:
        _think_pass(hand, scratch)
    locks: PaceLocks = {}
    for pid, by_regime in scratch.items():
        for reg, book in by_regime.items():
            locks[(pid, reg)] = _pace_thresholds(book)
    # Resolved over the whole batch, because a single hand cannot say who
    # exported it -- unless the caller knows better: a batch narrowed to a few
    # players can name a different "you" than the whole database does.
    if hero is None:
        hero = hero_of(hands)

    n_workers = _default_workers() if workers is None else workers
    if ProcessPoolExecutor is not None and n_workers > 1 and len(hands) >= PARALLEL_MIN_HANDS and not books and _can_spawn():
        size = math.ceil(len(hands) / n_workers)
        chunks = [(hands[i:i + size], locks, hero)
                  for i in range(0, len(hands), size)]
        try:
            with ProcessPoolExecutor(max_workers=n_workers) as pool:
                done = 0
                for part in pool.map(_record_chunk, chunks):
                    merge_books(books, part)
                    # A chunk at a time is the only honest granularity here:
                    # the workers do not report back mid-chunk.
                    done = min(done + size, len(hands))
                    if progress is not None:
                        progress(done, len(hands), "reading players")
            return books
        except Exception:
            # No spawning, a pickling failure, a worker killed for memory:
            # fall back rather than fail an import over an optimization.
            books.clear()

    for done, hand in enumerate(hands, 1):
        record_hand(hand, books, pace_locks=locks, hero=hero)
        if progress is not None and done % 200 == 0:
            progress(done, len(hands), "reading players")
    if progress is not None:
        progress(len(hands), len(hands), "reading players")
    return books


#: The only counters that need a hand's cards scored. Equity and showdown
#: percentiles are nearly all of the cost of reading a hand, and everything
#: else they feed is a meter, which evidence never asks about.
EVALUATED_RATIOS = frozenset({"river_bet_bluff", "sd_light_call"})


def record_hand(hand: Hand, books: Books,
                pace_locks: PaceLocks | None = None,
                hero: str | None = None, score_cards: bool = True) -> None:
    """Fold one hand into every participating player's book for this regime.

    ``pace_locks`` freezes snap/tank cutoffs (from :func:`record_hands`).
    Without locks, thresholds follow the running mean (streaming / evidence).

    ``hero`` is whoever exported the hands. Given one, decisions with that
    player on the other side are counted again under :data:`VS_HERO`; without
    one no ``vs:`` counter is written, since an against-you slice keyed on an
    arbitrary seat is worse than none."""
    if "pot_mismatch" in hand.flags or hand.big_blind <= 0:
        return
    view = HandView(hand)
    reg = regime_of(len(hand.seats))
    hero_seat = next((s.seat for s in hand.seats if s.player_id == hero), None)

    for seat in hand.seats:
        book = book_for(books, seat.player_id, reg, seat.name)
        book.name = seat.name or book.name
        book.hands += 1
        book.first_seen = min(book.first_seen or hand.started_at, hand.started_at)
        book.last_seen = max(book.last_seen or hand.started_at, hand.started_at)
        book.count(f"seat:{seat.position}", True)
        book.measure("table_size", len(hand.seats))
        book.measure("stack_bb", seat.stack / hand.big_blind)

    # A bomb pot has no preflop round -- forced ante, action starts on the
    # flop -- so counting it credits VPIP, RFI and limp opportunities nobody
    # was given. The postflop streets are real and still counted.
    if "bomb_pot" not in hand.flags:
        _preflop(hand, view, books, reg, pace_locks=pace_locks, hero_seat=hero_seat)
    pace_events = _postflop(hand, view, books, reg, pace_locks=pace_locks,
                            hero_seat=hero_seat)
    _results(hand, view, books, reg, pace_events, score_cards=score_cards)


def _ip_against(hand: Hand, seat: int, other: int | None) -> str:
    """"ip" or "oop" for ``seat`` against one opponent, by postflop order.

    Preflop "in position" meant the seat was labeled BTN or CO, so a UTG open
    folding to the big blind's 3-bet was booked out of position -- and the
    simulator reads these keys as position against the raiser."""
    if other is None:
        return "oop"
    rank = postflop_rank(len(hand.seats))
    mine = rank.get(hand.seat(seat).position, -1)
    theirs = rank.get(hand.seat(other).position, -1)
    return "ip" if mine > theirs else "oop"


def book_for(books: Books, player_id: str, reg: str, name: str = "") -> StatBook:
    by_regime = books.setdefault(player_id, {})
    book = by_regime.get(reg)
    if book is None:
        book = by_regime[reg] = StatBook(player_id=player_id, name=name, regime=reg)
    return book


def _book(hand: Hand, books: Books, seat: int, reg: str) -> StatBook:
    s = hand.seat(seat)
    return book_for(books, s.player_id, reg, s.name)


def _think_pass(hand: Hand, books: Books) -> None:
    """First timing pass: think-time meters only, so cutoffs can be frozen."""
    if "pot_mismatch" in hand.flags or hand.big_blind <= 0:
        return
    view = HandView(hand)
    reg = regime_of(len(hand.seats))
    for d in view.decisions():
        ms = d.action.think_ms
        if ms is None or ms < 0 or ms > 120_000:
            continue
        book = _book(hand, books, d.seat, reg)
        book.measure("think:all", ms)


# -- preflop -------------------------------------------------------------------

def _preflop(hand: Hand, view: HandView, books: Books, reg: str,
             pace_locks: PaceLocks | None = None,
             hero_seat: int | None = None) -> None:
    opener: int | None = None
    three_bettor: int | None = None
    open_size = 0
    three_bet_amt = 0
    voluntary: set[int] = set()
    limpers: set[int] = set()
    cold_callers = 0
    bb = hand.big_blind
    # Open sizes are read in blinds of the pot being opened. A straddle is the
    # biggest blind in it, and dividing by the posted big blind made a
    # standard open over a straddle read as a 5bb open -- 5% of real hands.
    live_bb = max([bb] + [a.to_amount for a in hand.actions
                          if a.act is Act.POST_STRADDLE and a.street is Street.PREFLOP])
    # VPIP and PFR are per *hand*, not per decision. A player who limps and
    # then calls a raise put money in once; counting both decisions inflates
    # the denominator and makes the sample look larger than it is.
    entered: dict[int, dict[str, bool]] = {}

    for d in view.decisions():
        if d.street is not Street.PREFLOP:
            break
        a = d.action
        book = _book(hand, books, d.seat, reg)
        pos = hand.seat(d.seat).position
        depth = stack_bucket(hand.seat(d.seat).stack / bb)
        raised = a.act is Act.RAISE
        called = a.act is Act.CALL
        folded = a.act is Act.FOLD

        # Recorded once per hand, after the street is over.
        seen = entered.setdefault(d.seat, {"vpip": False, "pfr": False})
        seen["vpip"] = seen["vpip"] or raised or called
        seen["pfr"] = seen["pfr"] or raised

        if d.aggression_level == 0 and not voluntary:
            # First in: nobody has voluntarily put money in yet.
            book.count("rfi", raised)
            book.count(f"rfi:{pos}", raised)
            book.count(f"rfi:{pos}:{depth}", raised)
            book.count("limp", called)
            if pos in ("CO", "BTN", "SB"):
                book.count("steal", raised)
            if raised:
                book.measure("open_bb", a.to_amount / live_bb)
                book.measure(f"open_bb:{pos}", a.to_amount / live_bb)

        elif d.aggression_level == 0:
            # Limpers in, nobody raised: an isolation spot, and a wider one
            # than opening a folded pot. Pooled under rfi the gap is invisible.
            book.count("iso", raised)
            book.count(f"iso:{pos}", raised)
            book.count(f"iso:{pos}:{depth}", raised)
            book.count("over_limp", called)
            if raised:
                book.measure("iso_bb", a.to_amount / live_bb)
                book.measure(f"iso_bb:{pos}", a.to_amount / live_bb)

        elif d.aggression_level == 1 and d.seat != opener:
            # Facing a single raise.
            book.count("three_bet", raised)
            book.count(f"three_bet:{pos}", raised)
            book.count(f"three_bet:{depth}", raised)
            if opener is not None:
                opener_pos = hand.seat(opener).position
                book.count(f"three_bet:{pos}:vs:{opener_pos}", raised)
                if opener_pos in ("UTG", "UTG1", "UTG2", "MP", "LJ", "HJ"):
                    book.count(f"three_bet:{pos}:vs:ep", raised)
            if pos == "BB":
                book.count("bb_defend", raised or called)
                book.count("bb_fold_to_open", folded)
            if d.seat not in voluntary:
                book.count("cold_call", called)
                book.count(f"cold_call:{pos}", called)
                if opener is not None:
                    book.count(f"cold_call:{pos}:vs:{hand.seat(opener).position}", called)
            if cold_callers and d.seat not in voluntary:
                book.count("squeeze", raised)
            if opener is not None and hand.seat(opener).position in ("CO", "BTN", "SB") \
                    and pos in ("SB", "BB"):
                book.count("fold_to_steal", folded)
                book.count("three_bet_vs_steal", raised)
            if d.seat in limpers:
                book.count("limp_fold", folded)
                book.count("limp_raise", raised)
            if raised and open_size:
                book.measure("three_bet_ratio", a.to_amount / open_size)
                book.measure(f"three_bet_ratio:{_ip_against(hand, d.seat, opener)}",
                             a.to_amount / open_size)
            # The same decisions, sliced to raises that were yours. Alongside
            # the pooled counter, never instead: that is its baseline.
            if opener is not None and opener == hero_seat:
                book.count(f"{VS_HERO}three_bet", raised)
                if pos == "BB":
                    book.count(f"{VS_HERO}bb_defend", raised or called)
                if hand.seat(opener).position in ("CO", "BTN", "SB") \
                        and pos in ("SB", "BB"):
                    book.count(f"{VS_HERO}fold_to_steal", folded)

        elif d.aggression_level == 2:
            if d.seat == opener:
                book.count("fold_to_three_bet", folded)
                book.count(f"fold_to_three_bet:{_ip_against(hand, d.seat, three_bettor)}",
                           folded)
                book.count("four_bet", raised)
                if three_bet_amt:
                    if raised:
                        book.measure("four_bet_ratio", a.to_amount / three_bet_amt)
                if three_bettor is not None and three_bettor == hero_seat:
                    book.count(f"{VS_HERO}fold_to_three_bet", folded)

        elif d.aggression_level >= 3 and d.seat == three_bettor:
            book.count("fold_to_four_bet", folded)
            book.count("five_bet", raised)

        _timing(book, d, "pf", pace_locks=pace_locks, regime=reg)

        if raised:
            if opener is None:
                opener, open_size = d.seat, a.to_amount
            elif three_bettor is None:
                three_bettor, three_bet_amt = d.seat, a.to_amount
        if called and d.aggression_level >= 1 and d.seat not in voluntary:
            cold_callers += 1
        if called and d.aggression_level == 0:
            limpers.add(d.seat)
        if raised or called:
            voluntary.add(d.seat)

    for seat, seen in entered.items():
        book = _book(hand, books, seat, reg)
        book.count("vpip", seen["vpip"])
        book.count("pfr", seen["pfr"])
        # Of the hands they chose to play, how many they raised. VPIP and PFR
        # as separate marginals cannot see it: 25/13 and 25/22 look alike and
        # are completely different plans.
        if seen["vpip"]:
            book.count("raise_share", seen["pfr"])


# -- postflop ------------------------------------------------------------------

def _postflop(hand: Hand, view: HandView, books: Books, reg: str,
              pace_locks: PaceLocks | None = None,
              hero_seat: int | None = None
              ) -> dict[tuple[int, str], tuple[str, str]]:
    """Postflop frequencies plus pace tags for timing-outcome resolution.

    Returns ``(seat, street) -> (pace, action)`` for the first timed
    check/call/aggro on each flop/turn, used by :func:`_results`."""
    pf_raises = sum(1 for a in hand.actions
                    if a.street is Street.PREFLOP and a.act.is_aggressive)
    if pf_raises >= 2:
        pot_type = "3bp"
    elif pf_raises >= 1:
        pot_type = "srp"
    else:
        pot_type = "limp"
    street = None
    first_bettor: int | None = None
    bettor_had_initiative = False
    checked: set[int] = set()
    # Aggressors who checked while holding the lead and have not bet since.
    # Membership separates a delayed c-bet from a second barrel, so it is
    # released the moment they take the lead back -- after checking the flop
    # and betting the turn, a river bet is cbet:river.
    declined_initiative: set[int] = set()
    faced_bet_size: dict[int, float] = {}
    # Who put in the bet each seat is facing. The size was already tracked; the
    # bettor is what says whether the decision was against you.
    faced_bet_from: dict[int, int] = {}
    # Fold-vs-bet / c-bet once per street: raise wars must not manufacture
    # independent opportunities (and confidence) from the same pot.
    faced_bet_already: set[int] = set()
    # First timed check/call/aggro per seat per street, for outcome deltas.
    pace_events: dict[tuple[int, str], tuple[str, str]] = {}
    # Tags waiting for a later bet faced (fold-next opportunity).
    pending_fold: dict[int, list[tuple[str, str, str]]] = {}
    # Who called a bet on the street just gone, and who was in position doing
    # it. Every other counter here asks what they did at a decision; these are
    # what makes a sequence -- called the flop, folded the turn -- measurable.
    called_prev: set[int] = set()
    called_prev_ip: set[int] = set()
    called_here: set[int] = set()
    called_here_ip: set[int] = set()
    resolved_after_call: set[int] = set()

    for d in view.decisions():
        if d.street is Street.PREFLOP:
            continue
        if d.street is not street:
            street = d.street
            first_bettor, bettor_had_initiative = None, False
            checked, faced_bet_size, faced_bet_already = set(), {}, set()
            called_prev, called_prev_ip = called_here, called_here_ip
            called_here, called_here_ip = set(), set()
            resolved_after_call = set()
            # Two-way, not the four-way split reads.texture returns: four
            # ways runs out of sample, and "did the board connect" is the part
            # that moves the decision.
            paired, suited, connected, _high = texture(hand.board_at(street))
            tex = "wet" if (suited or connected or paired) else "dry"
            # Two ways, not four, for the same sample reason. High-vs-low is
            # the split that changes which ranges connect.
            faced_bet_from = {}
            for seat in view.saw[street]:
                _book(hand, books, seat, reg).count(f"saw:{street.label}", True)

        a = d.action
        book = _book(hand, books, d.seat, reg)
        s = street.label
        ipo = "ip" if d.in_position else "oop"
        depth = stack_bucket(hand.seat(d.seat).stack / max(hand.big_blind, 1))
        raised = a.act is Act.RAISE
        bet = a.act is Act.BET
        called = a.act is Act.CALL
        folded = a.act is Act.FOLD
        checkd = a.act is Act.CHECK
        initiative = view.initiative_at(street)

        # What they did on the street after calling a bet. Once per seat per
        # street, or a raise war books several follow-ups to one call.
        if d.seat in called_prev and d.seat not in resolved_after_call:
            resolved_after_call.add(d.seat)
            if d.facing_bet:
                book.count(f"after_call:{s}:fold", folded)
                book.count(f"after_call:{s}:raise", raised)
            else:
                # Checked to after calling: betting here is the second half of
                # a float, and giving up is what a hand with nothing does.
                book.count(f"after_call:{s}:stab", bet)
                if d.seat in called_prev_ip:
                    book.count(f"after_call:{s}:stab:ip", bet)

        # Resolve fold-next for earlier pace tags before this facing-bet action
        # becomes a new tag of its own.
        if d.facing_bet and d.seat in pending_fold:
            for pace, st, action in pending_fold.pop(d.seat):
                book.count(f"after:{pace}:{st}:{action}:fold_next", folded)

        # Pot sizes are recorded so the exploit layer can price a leak in big
        # blinds instead of reporting an abstract severity score.
        book.measure(f"pot_bb:{s}", a.pot_before / hand.big_blind)

        # Raw action mix, for aggression frequency.
        for label, hit in (("bet", bet), ("raise", raised), ("call", called),
                           ("fold", folded), ("check", checkd)):
            book.count(f"act:{s}:{label}", hit)

        if not d.facing_bet:
            if d.has_initiative:
                # Continuation bet: they took the lead last street and the
                # action is on them with nothing wagered. ``has_initiative``
                # walks back through every earlier street, so a preflop raiser
                # who checked the flop still leads on the turn -- betting there
                # is a delayed c-bet, not a second barrel.
                if d.seat in declined_initiative:
                    book.count(f"delayed_cbet:{s}", bet)
                    if bet:
                        book.measure(f"delayed_cbet_size:{s}", d.bet_fraction)
                else:
                    book.count(f"cbet:{s}", bet)
                    if bet:
                        book.measure(f"cbet_size:{s}", d.bet_fraction)
                    # Heads-up and multiway are different decisions, as are
                    # wet and dry boards. Pooled, one number describes neither
                    # plan.
                    for slice_ in ("hu" if d.players_in <= 2 else "mw", tex, ipo, pot_type, depth):
                        book.count(f"cbet:{s}:{slice_}", bet)
                        if bet:
                            book.measure(f"cbet_size:{s}:{slice_}", d.bet_fraction)
                    # Heads-up only: a multiway c-bet is aimed at the field,
                    # not at you.
                    if (d.players_in <= 2 and hero_seat is not None
                            and d.seat != hero_seat
                            and hero_seat in view.saw[street]):
                        book.count(f"{VS_HERO}cbet:{s}", bet)
                if bet:
                    declined_initiative.discard(d.seat)
                else:
                    declined_initiative.add(d.seat)
            elif initiative is not None and initiative not in declined_initiative:
                # Betting into the player who holds the lead.
                book.count(f"donk:{s}", bet)
            else:
                # Nobody claimed the lead -- a probe or a stab at a dead pot.
                book.count(f"probe:{s}", bet)
                book.count(f"probe:{s}:{ipo}", bet)
            if bet:
                book.measure(f"bet_size:{s}", d.bet_fraction)
                book.count(f"overbet:{s}", d.bet_fraction > 1.0)
        else:
            frac = faced_bet_size.get(d.seat, d.bet_fraction)
            bucket = size_bucket(frac)
            first_face = d.seat not in faced_bet_already
            # The c-bet itself, unraised, faced by somebody other than the
            # c-bettor. "Any first bet faced once a c-bet exists" also counted
            # the c-bettor folding to a check-raise as folding to a c-bet --
            # their own.
            facing_cbet = (first_bettor is not None and bettor_had_initiative
                           and d.seat != first_bettor and d.aggression_level == 1
                           and faced_bet_from.get(d.seat) == first_bettor)
            if raised:
                # Taking the lead back by raising is taking it back as much as
                # betting is; left set, their next-street bet was booked as a
                # delayed c-bet.
                declined_initiative.discard(d.seat)
            faced_bet_already.add(d.seat)
            if first_face:
                book.count(f"fold_vs_bet:{s}", folded)
                book.count(f"fold_vs_bet:{s}:{bucket}", folded)
                # Size and pot *they faced* — what bluffs at them must clear.
                book.measure(f"faced_size:{s}", frac)
                # pot_before already includes the bet; peel it off so severity
                # prices against the pot before the bluff goes in.
                pot_bb = a.pot_before / hand.big_blind
                before_bet = pot_bb / (1.0 + frac) if frac > 0 else pot_bb
                book.measure(f"pot_to_bluff:{s}", before_bet)
                # HU vs multiway and IP vs OOP are different games; pooling
                # them quietly biases short-handed home-game reads.
                pot_kind = "hu" if d.players_in <= 2 else "mw"
                book.count(f"fold_vs_bet:{s}:{pot_kind}", folded)
                pos_kind = "ip" if d.in_position else "oop"
                book.count(f"fold_vs_bet:{s}:{pos_kind}", folded)
                # Starting-stack depth. ``:mid`` is already the size bucket,
                # so the stack slice lives under ``stk:``; mixed, a 40bb
                # player's fold rate reads as a fold-to-half-pot rate.
                book.count(f"fold_vs_bet:{s}:stk:{depth}", folded)
                # Facing a raise is not facing a bet, and fold_vs_bet pools
                # them.
                if d.aggression_level >= 2:
                    book.count(f"fold_vs_raise:{s}", folded)
                    book.count(f"fold_vs_raise:{s}:{pos_kind}", folded)
                book.count(f"raise_vs_bet:{s}", raised)
                book.count(f"raise_vs_bet:{s}:{pos_kind}", raised)
                book.count(f"call_vs_bet:{s}", called)
                if called:
                    called_here.add(d.seat)
                    if d.in_position:
                        called_here_ip.add(d.seat)
                if facing_cbet:
                    book.count(f"fold_to_cbet:{s}", folded)
                # The same decisions, sliced to bets that were yours. Under
                # first_face for the reason the pooled counters are, and this
                # is the thinnest sample in the tool.
                if faced_bet_from.get(d.seat) == hero_seat and hero_seat is not None:
                    book.count(f"{VS_HERO}fold_vs_bet:{s}", folded)
                    book.count(f"{VS_HERO}call_vs_bet:{s}", called)
                    book.count(f"{VS_HERO}raise_vs_bet:{s}", raised)
                    if facing_cbet:
                        book.count(f"{VS_HERO}fold_to_cbet:{s}", folded)
                if d.seat in checked:
                    # first_face again: in a raise war a player faces a bet,
                    # check-raises, faces the re-raise and folds. Counting both
                    # books the check-raiser as having check-folded.
                    book.count(f"check_raise:{s}", raised)
                    book.count(f"check_fold:{s}", folded)
            if raised:
                book.measure(f"raise_ratio:{s}", a.to_amount / max(a.to_call, 1))


        tagged = _timing(book, d, s, pace_locks=pace_locks, regime=reg)
        if tagged is not None:
            pace, kind = tagged
            key = (d.seat, s)
            if key not in pace_events:
                pace_events[key] = (pace, kind)
                pending_fold.setdefault(d.seat, []).append((pace, s, kind))

        if checkd:
            checked.add(d.seat)
        if bet or raised:
            if first_bettor is None:
                first_bettor = d.seat
                bettor_had_initiative = d.has_initiative
            for seat in view.saw[street]:
                if seat != d.seat:
                    faced_bet_size[seat] = d.bet_fraction
                    faced_bet_from[seat] = d.seat

    return pace_events


def _pace_thresholds(book: StatBook) -> tuple[float, float]:
    """Tank/snap cutoffs: relative to this player's mean once we know it."""
    meter = book.meters.get("think:all")
    if meter is None or meter.n < MIN_PACE_SAMPLES or meter.mean is None:
        return float(TANK_MS), float(SNAP_MS)
    avg = meter.mean
    tank = max(FLOOR_TANK_MS, avg * REL_TANK)
    snap = min(CEIL_SNAP_MS, avg * REL_SNAP)
    if snap >= tank:
        return float(TANK_MS), float(SNAP_MS)
    return tank, snap


def _timing(book: StatBook, d, street_label: str,
            pace_locks: PaceLocks | None = None,
            regime: str = "") -> tuple[str, str] | None:
    """Record think times and pace shares. Returns ``(pace, action)`` for a
    timed flop/turn check, call, or aggressive action; otherwise ``None``.

    With ``pace_locks`` (batch import), every hand uses the same cutoffs from
    the player's full-sample mean. Without locks, thresholds follow the
    running mean so a streaming session still adapts."""
    ms = d.action.think_ms
    if ms is None or ms < 0 or ms > 120_000:
        return None
    lock = (pace_locks or {}).get((book.player_id, regime))
    if lock is not None:
        tank_ms, snap_ms = lock
    else:
        tank_ms, snap_ms = _pace_thresholds(book)
    book.measure("think:all", ms)
    act = d.action.act
    kind = ("fold" if act is Act.FOLD else "call" if act is Act.CALL
            else "check" if act is Act.CHECK else "aggro")
    book.measure(f"think:{kind}", ms)
    book.measure(f"think:{street_label}", ms)
    if act is Act.FOLD:
        tank = ms > tank_ms
        book.count("tank_fold", tank)
        book.count(f"tank_fold:{street_label}", tank)
    if act is Act.CALL:
        snap = ms < snap_ms
        book.count("snap_call", snap)
        book.count(f"snap_call:{street_label}", snap)
    if act.is_aggressive:
        snap = ms < snap_ms
        book.count("snap_aggro", snap)
        book.count(f"snap_aggro:{street_label}", snap)

    if ms > tank_ms:
        pace = "tank"
    elif ms < snap_ms:
        pace = "snap"
    else:
        pace = "normal"

    if street_label not in ("flop", "turn") or kind == "fold":
        return None
    # Share denominators: every timed check/call/raise, split by pace.
    book.count(f"timed:{street_label}:{kind}", True)
    for label in ("snap", "normal", "tank"):
        book.count(f"pace:{label}:{street_label}:{kind}", pace == label)
    return pace, kind


# -- results and showdown truth ------------------------------------------------

def _results(hand: Hand, view: HandView, books: Books, reg: str,
             pace_events: dict[tuple[int, str], tuple[str, str]] | None = None,
             score_cards: bool = True) -> None:
    bb = hand.big_blind
    showdown = view.showdown()
    complete_board = len(hand.board) >= 5
    pace_events = pace_events or {}

    for seat in hand.seats:
        book = book_for(books, seat.player_id, reg, seat.name)
        net_bb = seat.net / bb
        book.measure("net_bb", net_bb)
        saw_flop = seat.seat in view.saw.get(Street.FLOP, set()) and hand.reached(Street.FLOP)
        # Won means collected a share of a pot. ``net > 0`` scored every chop
        # as a loss for both players, and a raked chop as a losing hand.
        took_pot = seat.won > 0
        if saw_flop:
            book.count("wwsf", took_pot)
            book.count("wtsd", seat.seat in showdown)
        if seat.seat in showdown:
            book.count("wsd", took_pot)
            book.measure("sd_net_bb", net_bb)
        else:
            book.measure("nonsd_net_bb", net_bb)
        if seat.seat in view.folded_on:
            book.measure("fold_street", float(view.folded_on[seat.seat]))

        for (s_seat, street), (pace, action) in pace_events.items():
            if s_seat != seat.seat:
                continue
            book.count(f"after:{pace}:{street}:{action}:won", seat.net > 0)
            book.count(f"after:{pace}:{street}:{action}:wtsd", seat.seat in showdown)

    if not score_cards:
        return
    _all_in_ev(hand, view, books, reg, showdown)

    if not complete_board:
        return

    known = {s.seat: s.hole_cards for s in hand.seats
             if len(s.hole_cards) == 2 and s.seat in showdown}
    if not known:
        return
    strengths = _showdown_strengths(hand.board, known)
    aggressors = {view.aggressor[st] for st in Street if view.aggressor[st] is not None}

    for seat, pct in strengths.items():
        book = _book(hand, books, seat, reg)
        book.measure("sd_strength", pct)
        if seat in aggressors and view.aggressor[Street.RIVER] == seat:
            book.count("river_bet_bluff", pct < BLUFF_PCTILE)
            book.measure("river_bet_strength", pct)
        elif seat not in aggressors:
            book.count("sd_light_call", pct < 0.5)
            book.measure("sd_call_strength", pct)
        for (s_seat, street), (pace, action) in pace_events.items():
            if s_seat == seat:
                book.measure(f"after:{pace}:{street}:{action}:sd_strength", pct)


def _all_in_ev(hand: Hand, view: HandView, books: Books, reg: str,
               showdown: set[int]) -> None:
    """Score all-in pots by equity as well as by outcome.

    Over a few hundred hands a chip graph is mostly variance -- getting it in
    at 80% and losing costs what punting costs -- so a pot that went in face up
    is also credited by equity at that moment.

    Pots are layered by how deep each seat went, the way they are paid. Each
    layer is split by equity among the showdown seats that reached it, and a
    layer only one of them reached is theirs outright. Applying one equity to
    everything a seat could win charged a big stack its all-in odds on a side
    pot it won uncontested: kings that called a short stack's aces and then
    took a 600 side pot scored -24.5bb for a +25.2bb decision."""
    all_in_actions = [a for a in hand.actions if a.all_in]
    if not all_in_actions or len(showdown) < 2:
        return
    known = {s.seat: list(s.hole_cards) for s in hand.seats
             if s.seat in showdown and len(s.hole_cards) == 2}
    if len(known) != len(showdown):
        return

    street = max(a.street for a in all_in_actions)
    board = hand.board_at(street)
    invested = {s.seat: s.invested for s in hand.seats}
    shares_of: dict[tuple[int, ...], dict[int, float]] = {}
    expected = dict.fromkeys(known, 0.0)
    prev = 0
    for level in sorted({v for v in invested.values() if v > 0}):
        layer = (level - prev) * sum(1 for v in invested.values() if v >= level)
        prev = level
        contenders = tuple(seat for seat in known if invested[seat] >= level)
        if not contenders:
            # Only folded money this deep: it went to whoever went deepest.
            contenders = (max(known, key=lambda seat: invested[seat]),)
        if contenders not in shares_of:
            if len(contenders) == 1:
                shares_of[contenders] = {contenders[0]: 1.0}
            else:
                try:
                    split = equities([known[seat] for seat in contenders], board)
                except ValueError:
                    return
                shares_of[contenders] = dict(zip(contenders, split))
        for seat, share in shares_of[contenders].items():
            expected[seat] += share * layer
    if sum(1 for group in shares_of if len(group) > 1) > 1:
        hand.flags.add("side_pot")      # contested by different groups
    for seat in known:
        book = book_for(books, hand.seat(seat).player_id, reg)
        player = hand.seat(seat)
        book.measure("ev_net_bb", (expected[seat] - player.invested) / hand.big_blind)
        # The realized result of the same pots, so a rating can swap one for
        # the other instead of counting the all-in twice.
        book.measure("allin_realised_bb", player.net / hand.big_blind)
        book.measure("allin_equity", shares_of[tuple(known)][seat]
                     if tuple(known) in shares_of else 1.0 / len(known))


def _showdown_strengths(board: list[str], known: dict[int, tuple[str, ...]]) -> dict[int, float]:
    """Percentile of each shown hand among every holding the board allows.

    "Two pair" says nothing without the board -- two pair on a paired
    four-flush board is a bluff-catcher. The universe is the one the reads
    already build and cache per board; this rebuilt it, slower, for every
    showdown."""
    universe, lookup = _board_universe(tuple(board[:5]))
    out: dict[int, float] = {}
    for seat, cards in known.items():
        a, b = sorted(int(c) for c in card_ids(cards))
        score = lookup[a * 52 + b]
        out[seat] = float(np.searchsorted(universe, score, side="left") / len(universe))
    return out
