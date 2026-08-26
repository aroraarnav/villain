"""Turn a villain's measured profile into table decisions.

A frequency is a strength threshold: fold 60% means fold the weakest 60%.
Preflop uses a static ranking (open vs defend are different); postflop uses
percentile vs the board. Betting polarizes once the frequency exceeds what
value can explain. Heuristic, not a solver -- plus a little noise on the cut.

Two things that are not obvious from that summary and are load-bearing:

* The cut is taken *inside the range that reaches the node*, never inside the
  deck. Every statistic is counted over a denominator the player's own earlier
  actions already filtered, so reading it against all 1,326 dealt combos
  overshoots by roughly 1 / P(reaching the node). See :mod:`villain.ranges`.
* Stack depth is not a frequency. At 20bb there is no flat and an open is a
  shove; 20-30bb facing a raise is 3-bet-or-fold; a raise that would leave a
  stub behind gets the chips in. The measured rates still cut the range -- the
  depth decides what action that cut can emit.
* A made-hand *tie* is the top of the range, not the middle of it. Every ten
  on T-T-9-9-x is the same boat; ranking that pile at its midpoint is how a
  21% continue cut folded the nuts. Full house or better never folds, and
  never checks back the river. Narrowing cannot delete the hole they hold.

"""

from __future__ import annotations

import numpy as np

from . import profile as _p
from .cards import FULL_HOUSE, RANKS, card_text, evaluate
from .holdem import STREETS
from .model import positions_for
from .ranges import Ranges, class_scores, index_of
from .reads import texture
from .stats import size_bucket, stack_bucket

# -- preflop ranking ----------------------------------------------------------
# Two rankings, combo-weighted. "Defends 45%" is a share of the 1,326 dealt
# combinations, not of the 169 class names. Chen's formula put 22-55 around
# the 45% cutoff -- the same slice as KTo -- so a monotonic BB defend folded
# 33 three-handed. Opening is high-card heavy (22 is a button open); defending
# a blind is not (every pair calls a steal before KTo does).

def _all_classes() -> list[str]:
    order = "23456789TJQKA"
    out = []
    for i in range(13):
        for j in range(i, 13):
            if i == j:
                out.append(order[i] * 2)
            else:
                out.append(order[j] + order[i] + "s")
                out.append(order[j] + order[i] + "o")
    return out


def _combos(cls: str) -> int:
    if len(cls) == 2:
        return 6
    return 4 if cls.endswith("s") else 12


def _open_score(cls: str) -> float:
    """Chen, but pairs are not floored together at 5.0.

    That floor is why 22-55 ranked as one clump around a 45% open, so a button
    that should open 22 folded it. Spread the pairs; leave unpaired Chen alone.
    """
    order = "23456789TJQKA"
    hi = order.index(cls[0])
    lo = order.index(cls[1])
    if cls[0] == cls[1]:
        return 5.0 + hi * 0.45 + (hi / 12) ** 2 * 8.5
    base = {12: 10, 11: 8, 10: 7, 9: 6}.get(hi, (hi + 2) / 2.0)
    score = base
    if cls.endswith("s"):
        score += 2
    gap = hi - lo - 1
    score -= {0: 0, 1: 1, 2: 2, 3: 4}.get(gap, 5)
    if gap <= 1 and hi < 10:
        score += 1
    return score


def _defend_score(cls: str) -> float:
    """Blind defense: every pair outranks every unpaired hand.

    A tight BB still set-mines 22/33 against a steal. Ranking them with Chen
    put them below KTo and 76s, which is the opposite of how the spot plays.
    """
    order = "23456789TJQKA"
    hi = order.index(cls[0])
    lo = order.index(cls[1])
    if cls[0] == cls[1]:
        return 50 + hi
    gap = hi - lo - 1
    connected = max(0, 4 - gap)
    if cls.endswith("s"):
        score = 8 + hi * 1.1 + lo * 0.35 + connected * 1.8
        if hi == 12:
            score += 6 + lo * 0.25
        if lo >= 9:
            score += 3
        return score
    score = hi * 0.9 + lo * 0.2 + connected * 0.6
    if hi == 12:
        score += 4 + lo * 0.3
    if lo >= 9:
        score += 2
    return score


def _combo_table(score_fn) -> dict[str, float]:
    items = [(cls, _combos(cls), score_fn(cls)) for cls in _all_classes()]
    items.sort(key=lambda row: (row[2], row[0]))
    out, cum, total = {}, 0.0, 1326.0
    for cls, n, _ in items:
        out[cls] = (cum + n / 2.0) / total
        cum += n
    return out


_OPEN_PCT = _combo_table(_open_score)
_DEFEND_PCT = _combo_table(_defend_score)


def _class_of(hole: tuple[int, ...]) -> str:
    (r1, s1), (r2, s2) = ((c // 4, c % 4) for c in hole)
    if r1 < r2:
        r1, s1, r2, s2 = r2, s2, r1, s1
    if r1 == r2:
        return RANKS[r1] + RANKS[r2]
    return RANKS[r1] + RANKS[r2] + ("s" if s1 == s2 else "o")


def preflop_strength(hole: tuple[int, ...], spot: str = "open") -> float:
    """0 (worst) .. 1 (best). ``spot`` is ``open`` (RFI / 3-bet) or ``defend``."""
    table = _DEFEND_PCT if spot == "defend" else _OPEN_PCT
    return table.get(_class_of(hole), 0.5)


#: Extra betting frequency above this, on this street, is taken from the
#: bottom of the range as bluffs -- unless showdowns say they are merged.
#: A maniac who c-bets 80% does not have the best 80%; a station who c-bets
#: 80% and shows up with middle pair does.
VALUE_CAP = {"flop": 0.55, "turn": 0.40, "river": 0.28}

#: Pool mean of shown-down hand strength. Above it they arrive with value;
#: below it the betting range is weaker than the field.
POOL_SD_STRENGTH = 0.60

#: Mean bet size faced, as a fraction of the pot, per street. Measured over
#: the stored hands (n≈29k/23k/15k). Used as the pivot for the size shift when
#: a player's own ``faced_size`` is too thin -- a pooled fold frequency has to
#: pivot on *something*, and the pool is a better guess than pretending the
#: bet in front of them is the one they usually face.
POOL_FACED_SIZE = {"flop": 0.66, "turn": 0.79, "river": 1.06}

#: Showdown-selected, so it undercounts bluffs that won uncontested. Bump
#: the measured bluff rate a little when turning it into a polar split.
SD_BLUFF_BUMP = 1.15

#: Raises are more polar than c-bets: a 9% check-raise is nuts plus draws,
#: not the top 9% (two pair and better, never a flush draw). A 6% raise is
#: still small enough to be value-only on every street.
RAISE_VALUE_CAP = {"flop": 0.08, "turn": 0.06, "river": 0.06}

#: Early-position openers. A 3-bet vs UTG is not a 3-bet vs the button.
EP_POS = {"UTG", "UTG1", "UTG2", "MP", "LJ", "HJ"}
STEAL_POS = {"CO", "BTN", "SB"}

#: Continue rates for a seat that did not make the raise being answered.
#:
#: ``fold_to_three_bet`` is counted only on the opener and ``fold_to_four_bet``
#: only on the 3-bettor -- features.py gates both on exactly that seat. A cold
#: caller facing the same raise never posted either number, and handing them
#: the opener's rate is how a blind cold-calls a 3-bet with a hand nobody cold
#: calls, then calls off a 4-bet shove with it. Nothing measures this spot, so
#: it takes a flat default: cold calling a re-raise is rare at every stake, and
#: rarer the deeper the raise goes.
COLD_CALL_VS_3BET = 0.06
COLD_CALL_VS_4BET = 0.02


def _polar_split(freq: float, value_cap: float,
                 spr: float | None = None) -> tuple[float, float]:
    """The value share and the bluff share of a betting frequency.

    Short SPR does not invent a new c-bet rate -- it cuts the *air* slice.
    Value still fires; stabbing 70% of junk at SPR 2 is the 100bb plan.
    """
    freq = _clamp(freq, 0.0, 1.0)
    value_frac = min(freq, value_cap)
    bluff_frac = max(0.0, freq - value_cap)
    if spr is not None and spr <= COMMIT_SPR and bluff_frac > 0:
        bluff_frac *= _clamp(spr / 4.0, 0.15, 1.0)
    return value_frac, bluff_frac


def _polar_bet(strength: float, freq: float, value_cap: float,
               rng=None, spr: float | None = None) -> str | None:
    """``value``, ``bluff``, or ``None`` (check / fold) for a betting frequency.

    With ``rng`` the inner edges fade across :data:`POLAR_MIX` so the same
    combo is not a pure strategy. Without it (the unit tests of the split)
    the cut is hard, which is what "this percentile is a bluff" means.
    """
    value_frac, bluff_frac = _polar_split(freq, value_cap, spr)
    if rng is None:
        if strength >= 1 - value_frac:
            return "value"
        if bluff_frac > 0 and strength <= bluff_frac:
            return "bluff"
        return None
    if _over(strength, 1 - value_frac, rng, POLAR_MIX):
        return "value"
    if bluff_frac > 0 and _under(strength, bluff_frac, rng, POLAR_MIX):
        return "bluff"
    return None


def _polar_bands(freq: float, value_cap: float,
                 spr: float | None = None) -> list[tuple[float, float]]:
    """The range a polarised bet represents: a value slice and an air slice."""
    value_frac, bluff_frac = _polar_split(freq, value_cap, spr)
    bands = [(1 - value_frac, 1.0)]
    if bluff_frac > 0:
        bands.append((0.0, bluff_frac))
    return bands


def _street_value_cap(profile, street: str, freq: float, default_cap: float) -> float:
    """Value share of a betting frequency, from their shown-down betting range.

    ``river_bet_bluff`` is the fraction of river bets shown down as junk,
    which *is* the river's polar split -- 40% bluffs means 40% of the frequency
    is air, whatever VALUE_CAP says. ``sd_strength`` is the weaker prior for
    earlier streets and thin river samples.
    """
    if street == "river":
        bluff = _freq_n(profile, "river_bet_bluff", None, 15)
        if bluff is not None:
            bluff = _clamp(bluff * SD_BLUFF_BUMP, 0.05, 0.70)
            return _clamp(freq * (1.0 - bluff), 0.05, 0.90)
    mean_sd = _size(profile, "sd_strength", None, 20)
    if mean_sd is not None:
        # Stronger than the pool → more merged; weaker → more polar.
        return _clamp(default_cap + (mean_sd - POOL_SD_STRENGTH) * 1.2, 0.12, 0.85)
    return default_cap


def hand_strength(hole: tuple[int, ...], board: list[int]) -> float:
    """Percentile of this made hand against every two-card holding the board
    allows -- the same measure the reads use, computed for one hand."""
    board_ids = np.array(board, dtype=np.int64)
    mask = np.ones(52, dtype=bool)
    mask[board_ids] = False
    mask[list(hole)] = False
    live = np.nonzero(mask)[0]
    i, j = np.triu_indices(len(live), k=1)
    combos = np.stack([live[i], live[j]], axis=1).astype(np.int64)
    seven = np.concatenate(
        [combos, np.repeat(board_ids[None, :], len(combos), axis=0)], axis=1)
    universe = np.sort(evaluate(seven))
    mine = int(evaluate(np.concatenate(
        [np.array(hole, dtype=np.int64), board_ids])[None, :])[0])
    return float(np.searchsorted(universe, mine, side="left") / len(universe))


# Reading a profile is villain.profile's job -- see its "reading a
# measurement" block. What stays here is the *bar*: how much sample the bot
# insists on before it will let a measured number override a default. That is
# a policy choice, and it is botplay's, so it lives with the policy. Twenty
# call sites below take these defaults rather than naming a bar.
def _freq(profile, stat: str, default: float) -> float:
    return _p.rate(profile, stat, default)


def _freq_n(profile, stat, default, min_opps=15.0):
    return _p.rate(profile, stat, default, min_opps)


def _freq_chain(profile, keys, default, min_opps=15.0):
    return _p.rate_chain(profile, keys, default, min_opps)


#: Read at enough call sites that the long name was the noisiest thing on the
#: line.
_chain = _freq_chain


def _sampled(profile, stat, min_opps=15.0) -> bool:
    return _p.sampled(profile, stat, min_opps)


def _any_sampled(profile, keys, min_opps=15.0) -> bool:
    return any(_sampled(profile, k, min_opps) for k in keys if k)


def _size(profile, key, default, min_n=5.0):
    return _p.size(profile, key, default, min_n)


def _size_chain(profile, keys, default, min_n=5.0):
    return _p.size_chain(profile, keys, default, min_n)


def _size_sd(profile, key, min_n=5.0):
    return _p.size_sd(profile, key, min_n)


def _raise_to(hand, legal, target: int) -> tuple[str, int]:
    to = int(min(max(target, legal.min_raise_to), legal.max_raise_to))
    return ("raise", to)


def _board_ctx(hand) -> tuple[str, str, str, str]:
    """wet/dry, hi/lo, hu/mw, pot type -- the slices features already counts."""
    board = [card_text(c) for c in hand.board]
    paired, suited, connected, high = texture(board)
    tex = "wet" if (suited or connected or paired) else "dry"
    hilo = "hi" if high else "lo"
    live = sum(1 for s in hand.seats if not s.folded)
    mw = "hu" if live <= 2 else "mw"
    pot = getattr(hand, "pot_kind", "srp") or "srp"
    if pot == "pre":
        pot = "srp"
    return tex, hilo, mw, pot


def _bet_frac(profile, street: str, rng, default: float = 0.6,
              slices: tuple[str, ...] = (), delayed: bool = False,
              polar: str | None = None) -> float:
    """Their c-bet size here -- pot type and texture first, then the pool.

    Sampled from the measured spread when we have one, so every stab is not
    exactly the mean. Delayed c-bets use their own size. Polar air is allowed
    to fire the overbet coin even when the mean is small; value is not,
    because that is how KK jammed a texture they bet a third on.
    """
    keys = []
    if delayed:
        keys.append(f"delayed_cbet_size:{street}")
    keys += [f"cbet_size:{street}:{s}" for s in slices if s]
    keys += [f"cbet_size:{street}", f"bet_size:{street}"]
    frac = _clamp(_size_chain(profile, keys, default), 0.2, 2.0)
    sd = None
    for key in keys:
        sd = _size_sd(profile, key)
        if sd is not None:
            break
    if sd is not None and sd > 0.05:
        frac = _clamp(float(rng.normal(frac, sd)), 0.2, 2.0)
    over_f = _freq_n(profile, f"overbet:{street}", 0.0, 12)
    if polar == "bluff":
        fire_over = over_f >= 0.05 and rng.random() < over_f
    elif polar == "value":
        fire_over = over_f >= 0.25 and frac >= 0.75 and rng.random() < over_f
    else:
        fire_over = over_f >= 0.05 and frac >= 0.75 and rng.random() < over_f
    if fire_over:
        frac = _clamp(max(frac, 1.15), 1.05, 2.0)
    return frac


#: How much of MDF still applies once the pot has been raised. Facing a bet the
#: aggressor bluffs near equilibrium and MDF holds; facing a raise their range
#: is value-heavy, and facing a re-raise it is almost pure value. Calibrated so
#: a pot-sized raise reproduces the flat cutoffs these replaced.
RAISE_VALUE_WEIGHT = 0.40
RERAISE_VALUE_WEIGHT = 0.08

#: The share of the continuing range strong enough to raise it again, and to
#: get it all in at re-raise depth.
RERAISE_SHARE = 0.22
JAM_SHARE = 0.375

#: Largest raise-to, as a multiple of the amount owed, that a measured
#: ``raise_ratio`` may ask for. Check-raising a c-bet to about 3x and
#: re-raising to about 2.5x is the range real play lives in; past ~4.5x the
#: number is an artefact of dividing by a small increment rather than a sizing
#: anybody chose.
RAISE_RATIO_CAP = 4.5

#: Typical 3-bet is ~2.2x the pot it faces (9bb into ~4bb). The pivot when
#: shifting a measured fold-to-3-bet onto a shove: that number was counted
#: against this size, not against 100bb jams.
USUAL_PRE_RAISE_POT = 2.2

#: Live players call shoves wider than MDF. 1.6× keeps a station looking you
#: up with the top of a 3-bet-calling range without giving a nit the same 43%
#: they continue vs a 9bb 3-bet.
SHOVE_CONTINUE_LIVE = 1.6
TYPICAL_THREE_BET_CONTINUE = 0.45

#: Pool baseline for raising a bet, the reference their own rate modulates.
POOL_RAISE_VS_BET = 0.06

#: Bet sizes that play as a polar claim when facing them. Frequency is still
#: ``fold_vs_bet`` (and the MDF shift); only the ordering of the continue
#: range changes. Small and mid are merged, so draws continue. A pot-or-more
#: stab is value-or-air — the same made-hand ranking facing a raise already
#: uses, which is how a competent player defends an overbet without a new
#: fold number.
POLAR_FACE_BUCKETS = frozenset({"big", "over"})

#: Half-width of the mixed-strategy band around a frequency cut. Inside it the
#: call is randomised so the same hand is not always played the same way;
#: outside it the cut is hard. Noise belongs on the decision at the boundary,
#: never on the ranking: the 3-, 4- and 5-bet gates can sit 0.02 apart, so a
#: term on the percentile wide enough to matter chooses the action itself.
MIX_BAND = 0.015

#: Wider fade around polar value/bluff gates, so the same combo is not always
#: the same action. Symmetric about the gate, so a uniform range still realises
#: the measured frequency. 3-bet / 4-bet / 5-bet keep :data:`MIX_BAND`.
POLAR_MIX = 0.08
CONTINUE_MIX = 0.04

#: Think-time clamps for the UI. A 30-second tank in the sample must not freeze
#: the table; a missing meter must not look like a snap.
THINK_FLOOR_MS = 400
THINK_CAP_MS = 8000
THINK_DEFAULT_MS = 1800
THINK_TANK_REL = 1.75
THINK_SNAP_REL = 0.40

#: At or below this remaining effective stack, first-in opens are shoves and
#: facing a raise is shove-or-fold -- there is no postflop left. Nash charts
#: live in this band; min-raising 22% of hands for 2.5x with 12bb behind is
#: how the stack knob used to change nothing.
PUSH_FOLD_BB = 20.0

#: Facing a raise with this much behind, there is no flat -- 3-bet or fold.
#: 28bb calling a 3-bet to play a 12bb pot is the line the 20bb shove-or-fold
#: gate left open.
THREEBET_OR_FOLD_BB = 30.0

#: Stack-to-pot at or below which a raise is a jam. Leaving 0.4 pot behind
#: after a "raise" is a stub, not a plan.
COMMIT_SPR = 2.0
LEAVE_BEHIND_POTS = 0.5


def _over(strength: float, gate: float, rng, band: float = MIX_BAND) -> bool:
    """Whether a hand clears a frequency cut, mixed inside ``band``."""
    if band <= 0:
        return strength >= gate
    if strength >= gate + band:
        return True
    if strength <= gate - band:
        return False
    p = (strength - (gate - band)) / (2 * band)
    return bool(rng.random() < p)


def _under(strength: float, gate: float, rng, band: float = POLAR_MIX) -> bool:
    """Whether a hand sits in the bottom ``gate`` slice, mixed inside ``band``.

    Polar air is a *low* cut. Reusing :func:`_over` would fade the wrong edge.
    ``gate <= 0`` is a player who does not polar-bluff -- never a coin flip.
    """
    if gate <= 0:
        return False
    if band <= 0:
        return strength <= gate
    if strength <= gate - band:
        return True
    if strength >= gate + band:
        return False
    p = (gate + band - strength) / (2 * band)
    return bool(rng.random() < p)


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


# -- range-conditional strength ----------------------------------------------
# A measured frequency is a cut inside the range that reaches the node, never
# inside the deck. These two orderings rank a range preflop; postflop the
# ordering is made-hand strength on the actual board.

_ORDER_OPEN = class_scores(_OPEN_PCT)
_ORDER_DEFEND = class_scores(_DEFEND_PCT)


def _ranges(hand) -> Ranges:
    """The per-seat ranges for this hand, created on first use."""
    r = getattr(hand, "_ranges", None)
    if r is None:
        r = Ranges(hand.n)
        hand._ranges = r
    return r


def _rank(hand, seat, order, board=None) -> float:
    """Where this seat's actual hand sits inside the range they can hold here."""
    return _ranges(hand).percentile(seat, hand.seats[seat].hole, order, board)


def _rank_hi(hand, seat, order, board=None) -> float:
    """``_rank``, counted from the top of a tie rather than its midpoint.

    Midpoint keeps preflop class cuts from slicing AA in half. Postflop a made
    tie can be most of the range -- every ten on T-T-9-9-x is one full house --
    and its midpoint sits at 0.6, so a 21% continue cut folds the nuts.
    """
    rs = _ranges(hand)
    hole = hand.seats[seat].hole
    better = rs.better_frac(seat, hole, order, board)
    return max(_rank(hand, seat, order, board), 1.0 - better)


def _keep(hand, seat, order, bands, board=None) -> None:
    """Stage the range this action implies, for the engine to commit if played.

    Acting on a frequency is a statement about the range: 4-betting the top
    16% of what you hold says you hold that slice. Without recording it every
    node re-reads the frequency against the full deck and nothing narrows.
    """
    rs = _ranges(hand)
    hole = tuple(hand.seats[seat].hole)
    hand.stage(seat, lambda: rs.narrow(seat, order, bands, board, keep_hole=hole))


def _made_category(hand, seat) -> int:
    """Made-hand category of this seat on the current board, or -1."""
    board = hand.board
    if not board:
        return -1
    score = _ranges(hand).board_cache(board).score[index_of(hand.seats[seat].hole)]
    return int(score) >> 20


def _is_monster(hand, seat) -> bool:
    """A full house or better. Frequency cuts do not fold these."""
    return _made_category(hand, seat) >= FULL_HOUSE


def _position(hand, seat):
    return positions_for(list(range(hand.n)), hand.button).get(seat, "")


def _in_position(hand, seat) -> bool:
    """Whether ``seat`` acts last among the players still in the hand.

    Postflop action starts left of the button and ends on it, so the last live
    seat walking backwards from the button is the one in position.
    """
    live = {i for i, s in enumerate(hand.seats) if not s.folded}
    if len(live) < 2:
        return True
    i = hand.button
    for _ in range(hand.n):
        if i in live:
            return i == seat
        i = (i - 1) % hand.n
    return False


def _ipo(hand, seat) -> str:
    return "ip" if _in_position(hand, seat) else "oop"


def _start_bb(hand, seat) -> float:
    """Starting stack in bb -- the number the stack-bucket stats were counted on."""
    s = hand.seats[seat]
    return (s.stack + s.hand_put) / max(hand.bb, 1)


def _effective(hand, seat) -> int:
    """Chips this seat can still put in against the relevant opponent.

    Facing a bet, that is the aggressor -- a 20bb jam with a 200bb fish still
    in is not a 100bb SPR. Opening a street, the shortest live stack: that is
    who can actually go with you.
    """
    mine = hand.seats[seat].stack
    live = [(i, s.stack) for i, s in enumerate(hand.seats)
            if i != seat and not s.folded]
    if not live:
        return mine
    aggressor = hand.last_raiser
    if aggressor is not None and aggressor != seat:
        other = hand.seats[aggressor]
        if not other.folded:
            return min(mine, other.stack)
    return min(mine, min(stack for _, stack in live))


def _remain_bb(hand, seat) -> float:
    return _effective(hand, seat) / max(hand.bb, 1)


def _spr(hand, seat) -> float:
    return _effective(hand, seat) / max(hand.pot, 1)


def _raise_or_jam(hand, seat, legal, target: int) -> tuple[str, int]:
    """A raise, or a shove if *this size* would commit the stack.

    A stub behind a raise is not a plan, but an opening bet at SPR 2 still has
    1.7 pots behind after a third-pot stab. ``COMMIT_SPR`` applies only to a
    raise *of* a bet, where a small raise at SPR 2 really is a stub.
    """
    s = hand.seats[seat]
    _, to = _raise_to(hand, legal, target)
    add = max(to - s.street_put, 0)
    left = s.stack - add
    if left <= LEAVE_BEHIND_POTS * max(hand.pot, 1):
        to = legal.max_raise_to
    elif hand.raises >= 1 and _spr(hand, seat) <= COMMIT_SPR:
        to = legal.max_raise_to
    return ("raise", to)


def _price(hand, legal) -> tuple[int, float, float, float]:
    """Chips to call, bet/pot, MDF, required equity to break even."""
    B = max(legal.call_amount, 1)
    pot_before = max(hand.pot - B, 1)
    frac = B / pot_before
    mdf = 1.0 / (1.0 + frac)
    req_eq = B / (2.0 * B + pot_before)
    return B, frac, mdf, req_eq


def _facing_shove(hand, seat, legal) -> bool:
    """Whether the raise in front of them is a jam, not a standard 3-bet.

    ``fold_to_three_bet`` pools sizes. A 9bb 3-bet and a 200bb shove are not
    the same decision, and treating them as one is how a single-raised-pot
    jam got "they continue 43% at this depth."
    """
    if legal.call_amount >= hand.seats[seat].stack:
        return True
    raiser = hand.last_raiser
    if raiser is not None and getattr(hand.seats[raiser], "all_in", False):
        return True
    B = legal.call_amount
    pot_before = max(hand.pot - B, 1)
    return (B / pot_before) >= 3.0


def _continue_vs_size(base_cont: float, mdf: float) -> float:
    """Their continue vs a typical raise, shifted onto a shove's price.

    Loose players (high ``base_cont``) still continue more than nits; the
    pot odds just bind. Against a 100bb jam a station who flats 3-bets
    continues around 10%; a nit who folds them continues around 3%.
    """
    usual_mdf = 1.0 / (1.0 + USUAL_PRE_RAISE_POT)
    looseness = base_cont / TYPICAL_THREE_BET_CONTINUE
    price = max(mdf, usual_mdf * 0.12)
    return _clamp(price * looseness * SHOVE_CONTINUE_LIVE, 0.03, 0.40)


def _why_fold_continue(cont, *, shove, req_eq, level, short, cont_why) -> str:
    if shove:
        return (f"folds — outside the ~{cont:.0%} they continue vs this size"
                f" ({req_eq:.0%} pot odds)")
    if short:
        return f"folds — outside the ~{cont:.0%} that continue at this depth"
    if level <= 1:
        return f"folds — outside the ~{cont:.0%} that {cont_why}"
    if level == 2:
        return f"folds — outside the ~{cont:.0%} they continue vs a 3-bet"
    if level == 3:
        return f"folds — outside the ~{cont:.0%} they continue vs a 4-bet"
    return f"folds — outside the ~{cont:.0%} they continue vs a shove"


def _why_call_continue(cont, *, shove, req_eq, cont_why) -> str:
    if shove:
        return (f"calls — they continue ~{cont:.0%} vs this size"
                f" ({req_eq:.0%} pot odds)")
    return f"calls — {cont_why}, roughly their top {cont:.0%}"


def think_ms(profile, kind: str, street_idx: int, reason: str, rng) -> int:
    """A think time from their meters, conditioned on the action.

    Folds and polar bluffs draw from ``think:fold``; raises from
    ``think:aggro``. Missing meters fall back to a short pause, not zero.
    """
    street_label = "pf" if street_idx == 0 else STREETS[street_idx]
    keys = []
    if kind == "fold" or "bluff" in reason:
        keys.append("think:fold")
    if kind == "raise":
        keys.append("think:aggro")
    elif kind == "call":
        keys.append("think:call")
    elif kind == "check":
        keys.append("think:check")
    keys += [f"think:{street_label}", "think:all"]
    mean = None
    hit_key = None
    for key in keys:
        m = _size(profile, key, None, 5)
        if m is not None:
            mean, hit_key = m, key
            break
    if mean is None:
        return THINK_DEFAULT_MS
    sd = _size_sd(profile, hit_key) if hit_key else None
    spread = sd if (sd is not None and sd > 50) else mean * 0.35
    sample = float(rng.normal(mean, spread))
    return int(_clamp(sample, THINK_FLOOR_MS, THINK_CAP_MS))


def think_pace(profile, ms: int) -> str:
    """snap / tank / normal against this player's own mean, not an 8s floor."""
    avg = _size(profile, "think:all", None, 5)
    if avg is None or avg <= 0:
        tank, snap = 8000.0, 1200.0
    else:
        tank = max(5000.0, avg * THINK_TANK_REL)
        snap = min(2500.0, avg * THINK_SNAP_REL)
        if snap >= tank:
            tank, snap = 8000.0, 1200.0
    if ms > tank:
        return "tank"
    if ms < snap:
        return "snap"
    return "normal"


def _decide_preflop(hand, seat: int, profile, rng, lg, bb: int) -> tuple[str, int, str]:
    """The preflop half of :func:`decide`.

    Split out because it shares nothing with the postflop half but the
    hand and the legal actions: it derives its own ranking, position and
    depth, and every path through it returns. Postflop hangs off a dozen
    board-derived locals instead -- which is the only reason the two
    halves sat in one 534-line function despite neither reading the
    other.
    """
    # Ranked inside the range they can still hold, not inside the deck.
    # Every preflop frequency was counted over a denominator their own
    # earlier actions already filtered, so this is the measure the cut
    # belongs on -- see :mod:`villain.ranges`.
    open_s = _rank(hand, seat, _ORDER_OPEN)
    defend_s = _rank(hand, seat, _ORDER_DEFEND)
    strength = open_s
    pos = _position(hand, seat)
    depth = stack_bucket(_start_bb(hand, seat))
    short = _remain_bb(hand, seat) <= PUSH_FOLD_BB
    opened = hand.bet > bb
    if not opened and getattr(hand, "limpers", 0) >= 1:
        # Limpers in, nobody raised: an isolation spot, and a wider one than
        # opening a folded pot. Pooling it under rfi had every villain
        # attacking limps at their first-in rate -- PlayerE opens the button
        # 40% first in and isolates limpers at ~63%.
        iso_f = _freq_chain(profile, [f"iso:{pos}:{depth}", f"iso:{pos}", "iso",
                                      f"rfi:{pos}:{depth}", f"rfi:{pos}"], 0.25, 12)
        if lg.can_raise and _over(strength, 1 - iso_f, rng):
            _keep(hand, seat, _ORDER_OPEN, [(1 - iso_f, 1.0)])
            if short:
                _, to = _raise_or_jam(hand, seat, lg, lg.max_raise_to)
                return ("raise", to,
                        f"shoves over {hand.limpers} limper{'s' if hand.limpers > 1 else ''}"
                        f" — { _remain_bb(hand, seat):.0f}bb, isolates ~{iso_f:.0%} from {pos or 'here'}")
            obb = _clamp(_size(profile, f"iso_bb:{pos}",
                               _size(profile, "iso_bb",
                                     _size(profile, f"open_bb:{pos}", 3.5))), 2.5, 7.0)
            # An iso is sized to punish the limpers, so it grows with them.
            _, to = _raise_or_jam(hand, seat, lg, int(round(obb * bb)) + hand.limpers * bb)
            return ("raise", to,
                    f"isolates the {hand.limpers} limper{'s' if hand.limpers > 1 else ''}"
                    f" to {to / bb:.1f}bb — attacks limps ~{iso_f:.0%} from {pos or 'here'}")
        over = _freq_n(profile, "over_limp", 0.05, 12)
        over_gate = 1 - _clamp(over * 2, 0.08, 0.45)
        if lg.can_call and over > 0.05 and _over(strength, over_gate, rng) and not short:
            _keep(hand, seat, _ORDER_OPEN, [(over_gate, 1.0)])
            return ("call", 0, f"over-limps behind — comes along ~{over:.0%} in limped pots")
        if lg.can_check:
            return ("check", 0, "checks the option behind the limpers")
        return ("fold", 0, f"folds — isolates only ~{iso_f:.0%} from {pos or 'here'}")

    if not opened:                              # first in: open by position, at their size
        rfi = _freq_chain(profile, [f"rfi:{pos}:{depth}", f"rfi:{pos}", "rfi"], 0.22, 20)
        if lg.can_raise and _over(strength, 1 - rfi, rng):
            _keep(hand, seat, _ORDER_OPEN, [(1 - rfi, 1.0)])
            if short:
                _, to = _raise_or_jam(hand, seat, lg, lg.max_raise_to)
                return ("raise", to,
                        f"shoves { _remain_bb(hand, seat):.0f}bb from {pos or 'the button'}"
                        f" — push/fold at this depth, opens ~{rfi:.0%} there")
            obb = _clamp(_size(profile, f"open_bb:{pos}", _size(profile, "open_bb", 2.5)), 2.0, 5.0)
            _, to = _raise_or_jam(hand, seat, lg, int(round(obb * bb)))
            return ("raise", to,
                    f"opens to {obb:.1f}bb from {pos or 'the button'} — opens ~{rfi:.0%} there, their own size")
        limp = _freq(profile, "limp", 0.03)
        limp_gate = 1 - _clamp(limp * 3, 0.1, 0.5)
        if lg.can_call and limp > 0.06 and _over(strength, limp_gate, rng) and not short:
            # An open-limp is the slice below the opening range, not above
            # it: hands good enough to open were raised a branch ago.
            _keep(hand, seat, _ORDER_OPEN, [(limp_gate, 1 - rfi)])
            return ("call", 0, f"limps — open-limps about {limp:.0%} of the time")
        if lg.can_check:
            return ("check", 0, "checks the option")
        return ("fold", 0, f"folds — opens only ~{rfi:.0%} from {pos or 'here'}, and this is weaker")

    in_bb = pos == "BB"
    level = hand.raises          # 1 open, 2 = a 3-bet, 3 = a 4-bet, 4+ = a 5-bet+
    opener_seat = getattr(hand, "opener", None)
    if opener_seat is None:
        opener_seat = hand.last_raiser
    opener_pos = _position(hand, opener_seat) if opener_seat is not None else ""
    vs_ep = opener_pos in EP_POS
    vs_steal = opener_pos in STEAL_POS
    tbet = _chain(profile, [
        f"three_bet:{pos}:vs:{opener_pos}" if opener_pos else "",
        f"three_bet:{pos}:vs:ep" if vs_ep else "",
        "three_bet_vs_steal" if vs_steal and pos in ("SB", "BB") else "",
        f"three_bet:{pos}", f"three_bet:{depth}", "three_bet",
    ], 0.07, 20)
    call_s = open_s              # vs 3-bet+ the continue is premiums, not pairs
    if level <= 1:               # facing an open -> a 3-bet can be wide (a bluff)
        steal = vs_steal and pos in ("SB", "BB")
        ipo = _ipo(hand, seat)
        default_3x = 2.8 if ipo == "ip" else 3.5
        ratio = _clamp(_size(profile, f"three_bet_ratio:{ipo}",
                             _size(profile, "three_bet_ratio", default_3x)), 2.2, 5.0)
        rr_to = int(round(ratio * hand.bet))
        cold = _chain(profile, [
            f"cold_call:{pos}:vs:{opener_pos}" if opener_pos else "",
            f"cold_call:{pos}", "cold_call",
        ], 0.18, 25)
        if getattr(hand, "callers", 0) >= 1:
            # Cold-callers behind the open: a squeeze, which is its own
            # frequency and a much stronger claim than a plain 3-bet.
            rr_freq = _freq_n(profile, "squeeze", tbet, 15)
            rr_label = f"squeezes over {hand.callers} caller{'s' if hand.callers > 1 else ''}"
            cont = _clamp(cold, 0.02, 0.98) if _sampled(profile, "cold_call") \
                else _clamp(cold, 0.05, 0.6)
            cont_why = "flats behind the callers"
        elif steal:
            rr_freq = tbet
            rr_label = "3-bets the steal"
            if in_bb:
                cont = _chain(profile, ["bb_defend"], 0.45, 20)
            else:
                f_steal = _chain(profile, ["fold_to_steal"], 0.55, 20)
                cont = (1 - f_steal if _sampled(profile, "fold_to_steal")
                        else _clamp(1 - f_steal, 0.08, 0.7))
            cont_why = f"defends the {'BB' if in_bb else 'SB'} vs a {opener_pos} open"
        else:
            rr_freq, rr_label = tbet, "3-bets"
            if in_bb:
                cont = _chain(profile, ["bb_defend"], 0.40, 20)
            else:
                cont = _clamp(cold + tbet, 0.02, 0.98) if _sampled(profile, "cold_call") \
                    else _clamp(cold + tbet, 0.05, 0.75)
            cont_why = "defends their blind" if in_bb else "continues"
        rr_gate = 1 - rr_freq
        call_s = defend_s
    elif level == 2:             # facing a 3-bet -> a 4-bet is premiums only
        four_f = _freq_n(profile, "four_bet", 0.04, 12)   # GTO ~4%; sample-gated
        rr_gate, rr_label = 1 - four_f, "4-bets"
        rr_to = int(round(_clamp(_size(profile, "four_bet_ratio", 2.3), 2.0, 2.8) * hand.bet))
        # In and out of position facing a 3-bet are different decisions;
        # prefer the side we are actually on when it has sample.
        ipo = _ipo(hand, seat)
        # No measured rate exists for this seat -- see COLD_CALL_VS_3BET.
        if getattr(hand, "opener", None) == seat:
            f3 = _chain(profile, [f"fold_to_three_bet:{ipo}", "fold_to_three_bet"],
                        0.55, 20)
            cont = _clamp(1 - f3, 0.02, 0.98)
            cont_why = f"flats the 3-bet {ipo}"
        else:
            cont = COLD_CALL_VS_3BET
            cont_why = "cold-calls the 3-bet"
    elif level == 3:             # facing a 4-bet -> a 5-bet jam is QQ+/AK
        five_f = _freq_n(profile, "five_bet", 0.02, 10)   # GTO ~2%; sample-gated
        rr_gate, rr_label = 1 - five_f, "5-bets"
        rr_to = lg.max_raise_to
        # Same split as the 3-bet above: `fold_to_four_bet` is counted on
        # the seat that made the 3-bet. A cold caller now facing a 4-bet
        # never posted it, and lending them the 3-bettor's continue rate
        # is how a 4-bet shove gets called by a hand that folds to a raise.
        if getattr(hand, "three_bettor", None) == seat:
            f4 = _freq_n(profile, "fold_to_four_bet", 0.50, 12)
            cont = _clamp(1 - f4, 0.02, 0.98)
            cont_why = "calls the 4-bet"
        else:
            cont = COLD_CALL_VS_4BET
            cont_why = "cold-calls the 4-bet"
    else:                        # facing a 5-bet+ shove -- only the nuts
        rr_gate, rr_label = 0.99, f"{level + 2}-bets"
        rr_to = lg.max_raise_to
        cont, cont_why = 0.02, "calls the shove"
    if seat in getattr(hand, "limped", ()):
        # They already limped; this raise is an isolation, not a 3-bet.
        # limp_raise / limp_fold are the numbers counted on that seat.
        lr = _freq_n(profile, "limp_raise", None, 12)
        lf = _freq_n(profile, "limp_fold", None, 12)
        if lr is not None:
            rr_gate, rr_label = 1 - lr, "limp-raises"
        if lf is not None:
            cont = _clamp(1 - lf, 0.02, 0.98)
            cont_why = "defends the limp"
    _, _, mdf, req_eq = _price(hand, lg)
    shove = _facing_shove(hand, seat, lg)
    if shove:
        # fold_to_three_bet (and bb_defend vs an open-jam) pool sizes.
        # A shove is a different price; shift the continue cut onto it
        # so a 43% 3-bet continuer is not a 43% jam continuer.
        cont = _continue_vs_size(cont, mdf)
        cont_why = "continue vs this size"
    rr_freq = 1 - rr_gate
    # Each gate is staged against the ordering it was measured on -- raise on
    # open, continue on defend; mixing them narrows by the wrong key.
    # Short: no flat. Calling a raise with 15bb behind to play a 3bb pot is
    # what the stack knob exists to kill. 20-30bb only does the same when we
    # lack their continue number -- if they flatted 3-bets at 28bb, they flat.
    no_flat = short
    if (not short and _remain_bb(hand, seat) <= THREEBET_OR_FOLD_BB
            and lg.can_raise):
        if level <= 1:
            known = _any_sampled(profile, ["bb_defend", "cold_call",
                                           "fold_to_steal"])
        elif level == 2:
            known = _any_sampled(profile, [
                "fold_to_three_bet", "fold_to_three_bet:ip",
                "fold_to_three_bet:oop"])
        else:
            known = _sampled(profile, "fold_to_four_bet")
        no_flat = not known
    fold_why = _why_fold_continue(
        cont, shove=shove, req_eq=req_eq, level=level, short=short,
        cont_why=cont_why)
    call_why = _why_call_continue(
        cont, shove=shove, req_eq=req_eq, cont_why=cont_why)
    if no_flat and lg.can_raise:
        if _over(strength, rr_gate, rng):
            _keep(hand, seat, _ORDER_OPEN, [(rr_gate, 1.0)])
            _, to = _raise_or_jam(hand, seat, lg, lg.max_raise_to)
            return ("raise", to,
                    f"shoves { _remain_bb(hand, seat):.0f}bb — {rr_label}, "
                    f"no flatting { _remain_bb(hand, seat):.0f}bb")
        if _over(call_s, 1 - cont, rng):
            order = _ORDER_DEFEND if level <= 1 else _ORDER_OPEN
            top = rr_gate if order is _ORDER_OPEN else 1.0
            _keep(hand, seat, order, [(1 - cont, max(top, 1 - cont))])
            _, to = _raise_or_jam(hand, seat, lg, lg.max_raise_to)
            return ("raise", to,
                    f"shoves { _remain_bb(hand, seat):.0f}bb — the {cont:.0%} that "
                    f"would continue vs this size, getting it in")
        if lg.can_check:
            return ("check", 0, "checks")
        return ("fold", 0, fold_why)
    if lg.can_raise and _over(strength, rr_gate, rng):
        _keep(hand, seat, _ORDER_OPEN, [(rr_gate, 1.0)])
        _, to = _raise_or_jam(hand, seat, lg, rr_to)
        if to == lg.max_raise_to:
            return ("raise", to,
                    f"shoves { _remain_bb(hand, seat):.0f}bb — {rr_label}, "
                    f"about {rr_freq:.0%} of their range here")
        return ("raise", to,
                f"{rr_label} to {to} — about {rr_freq:.0%} of their range here")
    if lg.can_call and _over(call_s, 1 - cont, rng):
        # Continuing without raising is the band below the raise cut: the
        # hands above it took the other branch.
        order = _ORDER_DEFEND if level <= 1 else _ORDER_OPEN
        top = rr_gate if order is _ORDER_OPEN else 1.0
        _keep(hand, seat, order, [(1 - cont, max(top, 1 - cont))])
        return ("call", 0, call_why)
    if lg.can_check:
        return ("check", 0, "checks")
    return ("fold", 0, fold_why)


def decide(hand, seat: int, profile, rng: np.random.Generator, name: str = "") -> tuple[str, int, str]:
    """One action, driven by the player's own frequencies AND sizes:
    ``(kind, amount, reason)``. They open to their size from their positions at
    their rates, c-bet their sizing per street, and fold to the specific bet
    size they face. Every branch falls back gracefully when a stat is thin, and
    always returns a legal action."""
    s = hand.seats[seat]
    lg = hand.legal()
    bb = hand.bb

    if hand.street == 0:
        return _decide_preflop(hand, seat, profile, rng, lg, bb)

    street = STREETS[hand.street]
    # Two measures, because postflop asks two questions:
    #   * `strength` -- place inside the range that got here, by playability.
    #     Frequency cuts belong on it: "check-raises 9%" is 9% of the hands
    #     they still hold, and the bottom of that 9% is draws, not 72o.
    #   * `absolute` -- made-hand percentile against every possible holding.
    #     Prices belong on it: pot odds are a claim about equity.
    rs = _ranges(hand)
    cache = rs.board_cache(hand.board)
    board_order = cache.play
    strength = _rank_hi(hand, seat, board_order, hand.board)
    absolute = rs.board_percentile(s.hole, hand.board)
    monster = _is_monster(hand, seat)
    if monster:
        # Full house+ is the top of the board. A frequency that was counted
        # over air-heavy spots does not get to dump it, and playability
        # ranking does not get to check it back.
        strength = 1.0
    has_init = hand.initiative == seat
    ipo = _ipo(hand, seat)
    delayed = has_init and seat in getattr(hand, "declined_initiative", ())
    tex, hilo, mw, pot = _board_ctx(hand)
    called_prev = getattr(hand, "called_prev", set())
    depth = stack_bucket(_start_bb(hand, seat))
    spr = _spr(hand, seat)

    if lg.call_amount > 0:                          # facing a bet or a raise
        # Theory (GTO Wizard, blog.gtowizard.com/mdf-alpha): for a bet B into pot
        # P, MDF = P/(P+B) is the share of range you must defend, and pot-odds
        # equity to call = B/(2B+P). The player's own fold-to-a-bet-of-this-size
        # frequency IS their defense; MDF is the theory default when thin. Facing
        # a bet the range beats random, so the equity bar adds ~half the bet
        # fraction on top of raw pot odds -- weak-live hands fold big bets.
        level = hand.raises                          # 1 = a bet, 2 = a raise, 3+ = a re-raise
        B = lg.call_amount
        pot_before = max(hand.pot - B, 1)
        f = B / pot_before                            # bet as a fraction of the pot
        mdf = 1.0 / (1.0 + f)                          # P / (P + B)
        req_eq = B / (2.0 * B + pot_before)            # pot odds
        # ``hand.pot`` already includes the bet, so B/pot of a jam is 0.9
        # ("big") not 8x ("over"). Fold-vs-over never fired and a shove got
        # the continue rate of a pot-sized bet -- A-high called off.
        bucket = size_bucket(f)
        steep = f >= 1.0 or B >= s.stack
        if level >= 2:
            # A raise is a value claim. Ranking the jam by playability would
            # get combo draws all-in and leave a set calling -- the opposite
            # of the street. Made-hand strength is the right cut here.
            board_order = cache.score
            strength = 1.0 if monster else _rank_hi(hand, seat, board_order, hand.board)
            raise_f = _chain(profile,
                             [f"raise_vs_bet:{street}:{ipo}", f"raise_vs_bet:{street}"],
                             POOL_RAISE_VS_BET, 12)
            fold_raise = _chain(profile,
                                [f"fold_vs_raise:{street}:{ipo}", f"fold_vs_raise:{street}"],
                                None, 12)
            depth = "a re-raised pot" if level >= 3 else "a raised pot"
            if fold_raise is not None:
                # Their number. Theory does not get a second vote.
                defend = _clamp(1 - fold_raise, 0.02, 0.98)
                value_bar = 1 - min(raise_f, defend)
                price_gate = False
            else:
                # No fold-vs-raise sample: MDF scaled by how value-heavy a
                # raise is. A pot-sized raise reproduces 0.80/0.955 facing a
                # raise, 0.96/0.985 facing a re-raise.
                heavy, floor, ceil_ = ((RERAISE_VALUE_WEIGHT, 0.02, 0.12) if level >= 3
                                       else (RAISE_VALUE_WEIGHT, 0.08, 0.45))
                polarity = _clamp(raise_f / POOL_RAISE_VS_BET, 0.5, 2.0)
                defend = _clamp(mdf * heavy * polarity, floor, ceil_)
                value_bar = 1 - defend * (JAM_SHARE if level >= 3 else RERAISE_SHARE)
                price_gate = True
            if lg.can_raise and _over(strength, value_bar, rng):
                _keep(hand, seat, board_order, [(value_bar, 1.0)], hand.board)
                if level >= 3:
                    _, to = _raise_or_jam(hand, seat, lg, lg.max_raise_to)
                    return ("raise", to,
                            f"jams — {depth}; only the top {1 - value_bar:.0%} of what continues")
                frac = _bet_frac(profile, street, rng, 0.8, (pot, tex, hilo, mw, ipo))
                _, to = _raise_or_jam(hand, seat, lg, hand.bet + int(round(frac * hand.pot)))
                return ("raise", to,
                        f"re-raises for value — the top {1 - value_bar:.0%} of the range that continues here")
            if monster or (_over(strength, 1 - defend, rng, CONTINUE_MIX)
                            and (not price_gate or absolute >= req_eq)):
                _keep(hand, seat, board_order, [(1 - defend, 1.0)], hand.board)
                return ("call", 0,
                        f"calls — {depth}; they continue ~{defend:.0%} vs this size"
                        + (f" ({req_eq:.0%} pot odds)" if steep else ""))
            return ("fold", 0,
                    f"folds — {depth}; outside the ~{defend:.0%} they continue vs this size"
                    + (f" ({req_eq:.0%} pot odds)" if steep or price_gate else ""))
        # level 1: their fold number is the continue cut. MDF and pot odds
        # interpolate a thin or missing number; they do not veto a sampled one.
        vs_cbet = (not has_init) and hand.initiative is not None \
            and hand.initiative == hand.last_raiser
        fold_f = None
        fold_measured = False
        sized_key = False            # did the number already know the bet size?
        if seat in called_prev:
            fold_f = _chain(profile, [f"after_call:{street}:fold"], None, 12)
            fold_measured = fold_f is not None
        if fold_f is None and s.street_put == 0:
            fold_f = _freq_n(profile, f"check_fold:{street}", None, 12)
            fold_measured = fold_f is not None
        sized = _freq_n(profile, f"fold_vs_bet:{street}:{bucket}", None, 12)
        if fold_f is None and sized is not None:
            fold_f = sized
            fold_measured = sized_key = True
        if fold_f is None:
            fold_keys = []
            if vs_cbet:
                fold_keys.append(f"fold_to_cbet:{street}")
            fold_keys += [
                f"fold_vs_bet:{street}:{hilo}",
                f"fold_vs_bet:{street}:{mw}",
                f"fold_vs_bet:{street}:{ipo}",
                f"fold_vs_bet:{street}:stk:{depth}",
                f"fold_vs_bet:{street}",
            ]
            hit = _chain(profile, fold_keys, None, 12)
            if hit is not None:
                fold_f = hit
                fold_measured = True
            else:
                fold_f = 1 - mdf
        if fold_measured and not sized_key:
            # Every key above except the size bucket pools the sizes it was
            # counted against, so it says nothing about *this* bet. Shift it by
            # the change in breakeven between the size they usually face and
            # the one in front of them -- the difference MDF predicts. Their
            # own average when we have it, the pool's when we do not: a fifth
            # of books have a pooled fold rate and no `faced_size`, and
            # skipping the shift there prices a 175% overbet as a third-pot
            # stab.
            usual = _size(profile, f"faced_size:{street}", None, 20) \
                or POOL_FACED_SIZE.get(street, 0.75)
            be_usual = usual / (1.0 + usual)
            fold_f = _clamp(fold_f + ((1 - mdf) - be_usual), 0.02, 0.98)
        prev = getattr(hand, "last_think", {}).get(seat) if hasattr(hand, "last_think") else None
        if prev:
            pace, st, act = prev
            nxt = _freq_n(profile, f"after:{pace}:{st}:{act}:fold_next", None, 12)
            if nxt is not None:
                fold_f = _clamp(0.7 * fold_f + 0.3 * nxt, 0.02, 0.98)
        raise_keys = [
            f"after_call:{street}:raise" if seat in called_prev else "",
            f"raise_vs_bet:{street}:{ipo}", f"raise_vs_bet:{street}",
        ]
        raise_f = _chain(profile, raise_keys, 0.06, 12)
        if not _any_sampled(profile, [k for k in raise_keys if k]):
            raise_f = min(raise_f, 0.20)
        if s.street_put == 0:                          # a raise here is a check-raise
            xr = _freq_n(profile, f"check_raise:{street}", None, 15)
            if xr is not None:
                raise_f = max(raise_f, xr)
        raise_cap = RAISE_VALUE_CAP.get(street, 0.06)
        polar = _polar_bet(strength, raise_f, raise_cap, rng, spr)
        if monster and polar != "bluff":
            polar = "value" if lg.can_raise else polar
        if lg.can_raise and polar:
            _keep(hand, seat, board_order, _polar_bands(raise_f, raise_cap, spr), hand.board)
            # `raise_ratio` is to_amount / to_call, and the denominator is
            # the increment still owed -- a re-raise over a big bet owes little
            # and books an enormous ratio into a plain mean. Clamped like every
            # other size here; unclamped it produces a 9x check-raise.
            rr = _size(profile, f"raise_ratio:{street}", None, 6)
            target = (int(round(_clamp(rr, 2.0, RAISE_RATIO_CAP) * B)) if rr
                      else hand.bet + int(round(0.9 * hand.pot)))
            _, to = _raise_or_jam(hand, seat, lg, target)
            how = "for value" if polar == "value" else "as a bluff"
            return ("raise", to, f"raises {how} — polar vs a {street} bet, ~{raise_f:.0%}")
        # Raise used playability so a combo draw can still be the air side.
        # Calling a polar-sized bet does not: that is a showdown claim, and
        # ranking it by draws is how a flush draw looks up a jam. How *far*
        # down the made-hand ranking they call is still their fold number —
        # a nit's 25% continue is top pair, a station's 85% is underpairs.
        if bucket in POLAR_FACE_BUCKETS or steep:
            board_order = cache.score
            strength = 1.0 if monster else _rank_hi(hand, seat, board_order, hand.board)
        continue_frac = _clamp(1 - fold_f, 0.02, 0.98)
        clears = monster or _over(strength, 1 - continue_frac, rng, CONTINUE_MIX)
        # A sampled fold rate averages the sizes they faced, so even after
        # the MDF shift a station's 15% calls A-high off a jam -- the frequency
        # cut says "top 40%" and pot odds never get a vote. Overbets and
        # all-ins always need the price; small bets keep playability.
        priced = monster or (not steep) or strength >= req_eq
        if clears and priced and (monster or fold_measured or absolute >= req_eq):
            # A call does not deny the nuts. Keep the whole continue slice,
            # including value that mixed a call or could not raise.
            _keep(hand, seat, board_order, [(1 - continue_frac, 1.0)], hand.board)
            return ("call", 0,
                    f"calls — they continue ~{continue_frac:.0%} vs this size"
                    + ("" if fold_measured else f", and this clears the {req_eq:.0%} pot odds"))
        return ("fold", 0,
                f"folds — outside the ~{continue_frac:.0%} they continue vs this size"
                + (" — the price of this bet does not clear" if steep and clears else ""))

    # checked to
    if has_init:                                     # only the aggressor c-bets
        # A flop check with the lead is a declined c-bet; betting the turn is
        # delayed_cbet, not cbet:turn. Pooling them made the second barrel
        # (having bet the flop) count a spot that never happened.
        if delayed:
            cbet_f = _freq_n(profile, f"delayed_cbet:{street}", 0.32, 12)
            fire = "delayed c-bet"
        else:
            cbet_f = _chain(profile, [
                f"cbet:{street}:{pot}",
                f"cbet:{street}:{tex}",
                f"cbet:{street}:{mw}",
                f"cbet:{street}:{ipo}",
                f"cbet:{street}:{depth}",
                f"cbet:{street}",
            ], 0.5, 15)
            fire = "c-bet"
        cap = _street_value_cap(profile, street, cbet_f, VALUE_CAP.get(street, 0.45))
        polar = _polar_bet(strength, cbet_f, cap, rng, spr)
        if monster and polar != "bluff":
            polar = "value"
        if lg.can_raise and polar:
            _keep(hand, seat, board_order, _polar_bands(cbet_f, cap, spr), hand.board)
            frac = _bet_frac(profile, street, rng, 0.6, (pot, tex, hilo, mw, ipo),
                             delayed=delayed, polar=polar)
            _, to = _raise_or_jam(hand, seat, lg, int(round(frac * hand.pot)))
            how = "value" if polar == "value" else "a bluff"
            return ("raise", to,
                    f"{fire}s {frac:.0%} pot ({how}) — fires ~{cbet_f:.0%} here, their own sizing")
        value_frac, bluff_frac = _polar_split(cbet_f, cap, spr)
        _keep(hand, seat, board_order, [(bluff_frac, 1 - value_frac)], hand.board)
        return ("check", 0, f"checks back — outside the ~{cbet_f:.0%} they {fire} here")
    # No initiative of our own -- two different spots:
    #   * a live aggressor who has not acted yet -> leading is a DONK (rare)
    #   * a limped or checked-through pot -> betting is a STAB / probe (normal)
    donking = hand.initiative is not None and hand.initiative not in hand.acted
    if donking:
        lead_f = _freq(profile, f"donk:{street}", 0.04)
        bet_why = f"leads out — donks ~{lead_f:.0%} into the raiser, with a hand worth it"
        check_why = f"checks — hardly ever donks into the raiser (~{lead_f:.0%})"
        cap = RAISE_VALUE_CAP.get(street, 0.08)
    elif seat in called_prev:
        # Called last street and checked to: a float / delayed stab, not a
        # probe of a dead pot. IP and OOP are different numbers.
        stab_keys = ([f"after_call:{street}:stab:ip"] if ipo == "ip" else [])
        stab_keys += [f"after_call:{street}:stab",
                      f"probe:{street}:{ipo}", f"probe:{street}"]
        lead_f = _freq_chain(profile, stab_keys, 0.30)
        bet_why = f"floats — stabs ~{lead_f:.0%} after calling last street"
        check_why = "checks — not a hand they take a stab with after calling"
        cap = _street_value_cap(profile, street, lead_f, VALUE_CAP.get(street, 0.45))
    else:
        lead_f = _freq_chain(profile, [f"probe:{street}:{ipo}", f"probe:{street}"], 0.30)
        bet_why = f"stabs — bets ~{lead_f:.0%} at a pot no one has bet"
        check_why = f"checks — not a hand they stab here (stabs ~{lead_f:.0%})"
        cap = _street_value_cap(profile, street, lead_f, VALUE_CAP.get(street, 0.45))
    polar = _polar_bet(strength, lead_f, cap, rng, spr)
    if monster and polar != "bluff":
        polar = "value"
    if lg.can_raise and lead_f > 0.02 and polar:
        _keep(hand, seat, board_order, _polar_bands(lead_f, cap, spr), hand.board)
        frac = _bet_frac(profile, street, rng, 0.55, (pot, tex, hilo, mw, ipo),
                         polar=polar)
        _, to = _raise_or_jam(hand, seat, lg, int(round(frac * hand.pot)))
        return ("raise", to, bet_why)
    if lg.can_check:
        value_frac, bluff_frac = _polar_split(lead_f, cap, spr)
        _keep(hand, seat, board_order, [(bluff_frac, 1 - value_frac)], hand.board)
        return ("check", 0, check_why)
    return ("fold", 0, "folds")
