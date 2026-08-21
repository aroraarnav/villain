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

"""

from __future__ import annotations

import numpy as np

from .cards import RANKS, card_text, evaluate
from .holdem import STREETS
from .model import positions_for
from .ranges import Ranges, class_scores
from .reads import texture
from .stats import VS_HERO, size_bucket, stack_bucket

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


def _polar_split(freq: float, value_cap: float) -> tuple[float, float]:
    """The value share and the bluff share of a betting frequency."""
    freq = _clamp(freq, 0.0, 1.0)
    return min(freq, value_cap), max(0.0, freq - value_cap)


def _polar_bet(strength: float, freq: float, value_cap: float) -> str | None:
    """``value``, ``bluff``, or ``None`` (check / fold) for a betting frequency."""
    value_frac, bluff_frac = _polar_split(freq, value_cap)
    if strength >= 1 - value_frac:
        return "value"
    if bluff_frac > 0 and strength <= bluff_frac:
        return "bluff"
    return None


def _polar_bands(freq: float, value_cap: float) -> list[tuple[float, float]]:
    """The range a polarised bet represents: a value slice and an air slice."""
    value_frac, bluff_frac = _polar_split(freq, value_cap)
    bands = [(1 - value_frac, 1.0)]
    if bluff_frac > 0:
        bands.append((0.0, bluff_frac))
    return bands


def _street_value_cap(profile, street: str, freq: float, default_cap: float) -> float:
    """Value share of a betting frequency, from their shown-down betting range.

    ``river_bet_bluff`` is the fraction of river bets that went to showdown
    as junk. That *is* the polar split on the river: 40% bluffs means 40% of
    the betting frequency is air, not whatever VALUE_CAP says. ``sd_strength``
    is the weaker prior on earlier streets, and on the river when the bluff
    sample is thin.
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


def _freq(profile, stat: str, default: float) -> float:
    est = profile.stats.get(stat) if profile is not None else None
    return est.value if est is not None else default


def _raise_to(hand, legal, target: int) -> tuple[str, int]:
    to = int(min(max(target, legal.min_raise_to), legal.max_raise_to))
    return ("raise", to)


def _size(profile, key, default, min_n=5.0):
    """A measured size from ``profile.means`` -- used only when it has sample."""
    means = getattr(profile, "means", None)
    if not means:
        return default
    v = means.get(key)
    return v if (v is not None and means.get(key + "#n", 0.0) >= min_n) else default


def _freq_n(profile, stat, default, min_opps=15.0):
    est = profile.stats.get(stat) if profile is not None else None
    return est.value if (est is not None and est.opps >= min_opps) else default


def _sampled(profile, stat, min_opps=15.0) -> bool:
    """Whether this key is a real number, not a prior we would be overriding."""
    est = profile.stats.get(stat) if profile is not None else None
    return est is not None and est.opps >= min_opps


def _any_sampled(profile, keys, min_opps=15.0) -> bool:
    return any(_sampled(profile, k, min_opps) for k in keys if k)


def _freq_chain(profile, keys, default, min_opps=15.0):
    """First key that has enough sample, else ``default``.

    Position and stack splits are real and measured, but they are thin. A
    missing or 4-hand ``three_bet:UTG`` must not become the policy -- that is
    how a pooled 8% turned into a 50% 3-bet off one hand. Walk the more
    specific keys first and fall back.
    """
    for key in keys:
        if not key:
            continue
        value = _freq_n(profile, key, None, min_opps)
        if value is not None:
            return value
    return default


def _chain(profile, keys, default, min_opps=15.0, vs=False):
    """``_freq_chain`` that prefers the against-you slice when it has sample.

    ``vs:fold_vs_bet:flop`` is counted and was never read -- so a player who
    folds 70% to *your* c-bets still defended at their pool rate against you.
    The vs: keys are thinner; 12 opportunities is enough to prefer them.
    """
    if vs:
        vs_keys = [VS_HERO + k for k in keys if k]
        hit = _freq_chain(profile, vs_keys, None, 12)
        if hit is not None:
            return hit
    return _freq_chain(profile, keys, default, min_opps)


def _vs_hero(hand, seat: int) -> bool:
    """Whether this decision is against the hero -- the vs: denominator."""
    hero = getattr(hand, "hero_seat", None)
    if hero is None or hero == seat:
        return False
    if hand.last_raiser == hero:
        return True
    live = [i for i, s in enumerate(hand.seats) if not s.folded]
    return len(live) == 2 and hero in live


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


def _bet_frac(profile, street: str, rng, default: float = 0.6) -> float:
    """Their c-bet size, with overbets emitted at the measured overbet rate."""
    frac = _clamp(_size(profile, f"cbet_size:{street}",
                        _size(profile, f"bet_size:{street}", default)), 0.2, 2.0)
    over_f = _freq_n(profile, f"overbet:{street}", 0.0, 12)
    if over_f and over_f >= 0.05 and rng.random() < over_f:
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

#: Pool baseline for raising a bet, the reference their own rate modulates.
POOL_RAISE_VS_BET = 0.06

#: Half-width of the mixed-strategy band around a frequency cut. Inside it the
#: call is randomised so the same hand is not always played the same way;
#: outside it the cut is hard.
#:
#: This replaces additive noise on the percentile itself. The 3-bet, 4-bet and
#: 5-bet gates can sit 0.02 apart, and the old ``N(0, 0.05)`` term was wider
#: than the gaps between them -- so the noise, not the hand, chose the action,
#: and KQo 5-bet jammed because a draw from the tail carried it two gates up.
#: Noise belongs on the decision at the boundary, never on the ranking.
MIX_BAND = 0.015

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


def _over(strength: float, gate: float, rng) -> bool:
    """Whether a hand clears a frequency cut, mixed inside :data:`MIX_BAND`."""
    if strength >= gate + MIX_BAND:
        return True
    if strength <= gate - MIX_BAND:
        return False
    p = (strength - (gate - MIX_BAND)) / (2 * MIX_BAND)
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


def _keep(hand, seat, order, bands, board=None) -> None:
    """Stage the range this action implies, for the engine to commit if played.

    Acting on a frequency *is* a statement about the range: a player who
    4-bets the top 16% of what they hold here has, by 4-betting, told you they
    hold that slice. Recording it is what keeps the next decision's cut honest
    -- without it every node re-reads the frequency against the full deck and
    the range never narrows, which is the whole defect.
    """
    rs = _ranges(hand)
    hand.stage(seat, lambda: rs.narrow(seat, order, bands, board))


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
    """Chips this seat can still put in against the biggest remaining opponent."""
    mine = hand.seats[seat].stack
    others = [s.stack for i, s in enumerate(hand.seats)
              if i != seat and not s.folded]
    if not others:
        return mine
    return min(mine, max(others))


def _remain_bb(hand, seat) -> float:
    return _effective(hand, seat) / max(hand.bb, 1)


def _spr(hand, seat) -> float:
    return _effective(hand, seat) / max(hand.pot, 1)


def _raise_or_jam(hand, seat, legal, target: int) -> tuple[str, int]:
    """A raise, or a shove if the raise would commit the stack.

    SPR already low, or the raise leaving a stub behind: the chips go in. The
    frequency cut still chose *whether* to raise; this only changes the size
    so 20bb and 200bb are not the same line.
    """
    s = hand.seats[seat]
    _, to = _raise_to(hand, legal, target)
    add = max(to - s.street_put, 0)
    left = s.stack - add
    if _spr(hand, seat) <= COMMIT_SPR or left <= LEAVE_BEHIND_POTS * max(hand.pot, 1):
        to = legal.max_raise_to
    return ("raise", to)


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
        vs = _vs_hero(hand, seat)
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
        ], 0.07, 20, vs=vs)
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
                    cont = _chain(profile, ["bb_defend"], 0.45, 20, vs=vs)
                else:
                    f_steal = _chain(profile, ["fold_to_steal"], 0.55, 20, vs=vs)
                    cont = (1 - f_steal if _any_sampled(profile, ["fold_to_steal", VS_HERO + "fold_to_steal"])
                            else _clamp(1 - f_steal, 0.08, 0.7))
                cont_why = f"defends the {'BB' if in_bb else 'SB'} vs a {opener_pos} open"
            else:
                rr_freq, rr_label = tbet, "3-bets"
                if in_bb:
                    cont = _chain(profile, ["bb_defend"], 0.40, 20, vs=vs)
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
            # `fold_to_three_bet` is counted only for the player who *opened*
            # (features.py gates it on ``d.seat == opener``). Someone who cold
            # called the open and now faces a 3-bet never posted that number,
            # and handing them the opener's continue rate is how a blind ends
            # up cold-calling a 3-bet with a hand nobody cold-calls -- there is
            # no measured rate for that spot at all, so it takes the tight
            # default it deserves.
            if getattr(hand, "opener", None) == seat:
                f3 = _chain(profile, [f"fold_to_three_bet:{ipo}", "fold_to_three_bet"],
                            0.55, 20, vs=vs)
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
        # The raise gate is read on the open ordering, the continue gate on the
        # defend ordering, so each is staged against the ordering it was
        # measured on -- mixing them would narrow the range by the wrong key.
        #
        # Short: there is no flat. The continue range shoves; calling a raise
        # with 15bb behind to play a 3bb pot is the line the stack knob is
        # supposed to kill. 20-30bb only does the same when we do *not* have
        # their continue number -- if they flatted 3-bets at 28bb, they flat.
        no_flat = short
        if (not short and _remain_bb(hand, seat) <= THREEBET_OR_FOLD_BB
                and lg.can_raise):
            if level <= 1:
                known = _any_sampled(profile, ["bb_defend", "cold_call",
                                               "fold_to_steal", VS_HERO + "bb_defend"])
            elif level == 2:
                known = _any_sampled(profile, [
                    "fold_to_three_bet", "fold_to_three_bet:ip",
                    "fold_to_three_bet:oop", VS_HERO + "fold_to_three_bet"])
            else:
                known = _sampled(profile, "fold_to_four_bet")
            no_flat = not known
        if no_flat and lg.can_raise:
            if _over(strength, rr_gate, rng):
                _keep(hand, seat, _ORDER_OPEN, [(rr_gate, 1.0)])
                _, to = _raise_or_jam(hand, seat, lg, lg.max_raise_to)
                return ("raise", to,
                        f"shoves { _remain_bb(hand, seat):.0f}bb — {rr_label} at this depth, "
                        f"no flatting {THREEBET_OR_FOLD_BB:.0f}bb")
            if _over(call_s, 1 - cont, rng):
                order = _ORDER_DEFEND if level <= 1 else _ORDER_OPEN
                top = rr_gate if order is _ORDER_OPEN else 1.0
                _keep(hand, seat, order, [(1 - cont, max(top, 1 - cont))])
                _, to = _raise_or_jam(hand, seat, lg, lg.max_raise_to)
                return ("raise", to,
                        f"shoves { _remain_bb(hand, seat):.0f}bb — the {cont:.0%} that "
                        f"would continue, getting it in")
            if lg.can_check:
                return ("check", 0, "checks")
            return ("fold", 0, f"folds — outside the ~{cont:.0%} that continue at this depth")
        if lg.can_raise and _over(strength, rr_gate, rng):
            _keep(hand, seat, _ORDER_OPEN, [(rr_gate, 1.0)])
            _, to = _raise_or_jam(hand, seat, lg, rr_to)
            # The blind is the 1-bet and an open is the 2-bet, so raising at
            # `level` makes an (level + 2)-bet -- which is what rr_label
            # already says ("3-bets" facing an open). The reason said one
            # less and contradicted the label in the same sentence.
            return ("raise", to, f"{rr_label} to {to} — a premium at {level + 2}-bet depth")
        if lg.can_call and _over(call_s, 1 - cont, rng):
            # Continuing without raising is the band below the raise cut: the
            # hands above it took the other branch.
            order = _ORDER_DEFEND if level <= 1 else _ORDER_OPEN
            top = rr_gate if order is _ORDER_OPEN else 1.0
            _keep(hand, seat, order, [(1 - cont, max(top, 1 - cont))])
            return ("call", 0, f"{cont_why} — roughly their top {cont:.0%} facing this")
        if lg.can_check:
            return ("check", 0, "checks")
        return ("fold", 0, f"folds — outside the ~{cont:.0%} that continue at this depth")

    street = STREETS[hand.street]
    # Two measures, because postflop asks two different questions and the old
    # code answered both with one number.
    #
    #   * `strength` -- where this hand sits inside the range that got here,
    #     ranked by playability (made hand plus draws). Frequency cuts belong
    #     on this: "check-raises 9%" is 9% of the hands they still hold, and
    #     the bottom of that 9% is draws, not 72o.
    #   * `absolute` -- made-hand percentile against every holding an opponent
    #     could have. Prices belong on this: pot odds are a claim about equity.
    rs = _ranges(hand)
    cache = rs.board_cache(hand.board)
    board_order = cache.play
    strength = _rank(hand, seat, board_order, hand.board)
    absolute = rs.board_percentile(s.hole, hand.board)
    has_init = hand.initiative == seat
    ipo = _ipo(hand, seat)
    delayed = has_init and seat in getattr(hand, "declined_initiative", ())
    vs = _vs_hero(hand, seat)
    tex, hilo, mw, pot = _board_ctx(hand)

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
        bucket = size_bucket(B / max(hand.pot, 1))
        if level >= 2:
            # A raise is a value claim. Ranking the jam by playability would
            # get combo draws all-in and leave a set calling -- the opposite
            # of the street. Made-hand strength is the right cut here.
            board_order = cache.score
            strength = _rank(hand, seat, board_order, hand.board)
            raise_f = _chain(profile,
                             [f"raise_vs_bet:{street}:{ipo}", f"raise_vs_bet:{street}"],
                             POOL_RAISE_VS_BET, 12, vs=vs)
            fold_raise = _chain(profile,
                                [f"fold_vs_raise:{street}:{ipo}", f"fold_vs_raise:{street}"],
                                None, 12, vs=vs)
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
                frac = _bet_frac(profile, street, rng, 0.8)
                _, to = _raise_or_jam(hand, seat, lg, hand.bet + int(round(frac * hand.pot)))
                return ("raise", to,
                        f"re-raises for value — the top {1 - value_bar:.0%} of the range that continues here")
            if _over(strength, 1 - defend, rng) and (not price_gate or absolute >= req_eq):
                _keep(hand, seat, board_order, [(1 - defend, value_bar)], hand.board)
                return ("call", 0,
                        f"calls — {depth}; they raise ~{raise_f:.0%} here, so the top "
                        f"{defend:.0%} continues")
            return ("fold", 0,
                    f"folds — {depth}; below the top {defend:.0%} that continues"
                    + (f", and {req_eq:.0%} pot odds do not rescue one pair" if price_gate else ""))
        # level 1: their fold number is the continue cut. MDF and pot odds
        # interpolate a thin or missing number; they do not veto a sampled one.
        called_prev = getattr(hand, "called_prev", set())
        vs_cbet = (not has_init) and hand.initiative is not None \
            and hand.initiative == hand.last_raiser
        fold_f = None
        fold_measured = False
        sized_key = False            # did the number already know the bet size?
        if seat in called_prev:
            fold_f = _chain(profile, [f"after_call:{street}:fold"], None, 12, vs=vs)
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
                f"fold_vs_bet:{street}",
            ]
            hit = _chain(profile, fold_keys, None, 12, vs=vs)
            if hit is not None:
                fold_f = hit
                fold_measured = True
            else:
                fold_f = 1 - mdf
        if fold_measured and not sized_key:
            # Every key above except the size bucket pools the sizes it was
            # counted against, so it says nothing about *this* bet. Shift it by
            # the change in breakeven between the size they usually face and
            # the one in front of them -- exactly the difference MDF predicts.
            #
            # Their own average when we have it, the pool's when we do not.
            # Skipping the shift on a missing `faced_size` is how a 175%-pot
            # overbet got the same continue threshold as a third-pot stab: a
            # fifth of the books have a pooled fold rate and no faced_size, and
            # for those the price stopped binding entirely once a measured
            # number began overriding the pot-odds gate.
            usual = _size(profile, f"faced_size:{street}", None, 20) \
                or POOL_FACED_SIZE.get(street, 0.75)
            be_usual = usual / (1.0 + usual)
            fold_f = _clamp(fold_f + ((1 - mdf) - be_usual), 0.02, 0.98)
        raise_f = _chain(profile,
                         [f"raise_vs_bet:{street}:{ipo}", f"raise_vs_bet:{street}"],
                         0.06, 12, vs=vs)
        if not _any_sampled(profile, [f"raise_vs_bet:{street}:{ipo}", f"raise_vs_bet:{street}",
                                      VS_HERO + f"raise_vs_bet:{street}"]):
            raise_f = min(raise_f, 0.20)
        if s.street_put == 0:                          # a raise here is a check-raise
            xr = _freq_n(profile, f"check_raise:{street}", None, 15)
            if xr is not None:
                raise_f = max(raise_f, xr)
        raise_cap = RAISE_VALUE_CAP.get(street, 0.06)
        polar = _polar_bet(strength, raise_f, raise_cap)
        if lg.can_raise and polar:
            _keep(hand, seat, board_order, _polar_bands(raise_f, raise_cap), hand.board)
            # `raise_ratio` is measured as to_amount / to_call, a ratio whose
            # denominator is the increment still owed. A re-raise over a big
            # bet owes little and books an enormous ratio, and the stat is a
            # plain mean, so one such hand drags the average past anything a
            # person would do. Every other size in this file is clamped to
            # what the action can actually be; this one was not, and a 9x
            # check-raise of a c-bet is how a top pair got all in.
            rr = _size(profile, f"raise_ratio:{street}", None, 6)
            target = (int(round(_clamp(rr, 2.0, RAISE_RATIO_CAP) * B)) if rr
                      else hand.bet + int(round(0.9 * hand.pot)))
            _, to = _raise_or_jam(hand, seat, lg, target)
            how = "for value" if polar == "value" else "as a bluff"
            return ("raise", to, f"raises {how} — polar vs a {street} bet, ~{raise_f:.0%}")
        continue_frac = _clamp(1 - fold_f, 0.02, 0.98)
        # Polar raises take both ends; the call band is the middle.
        value_frac, bluff_frac = _polar_split(raise_f, raise_cap)
        call_lo = max(bluff_frac, 1 - continue_frac)
        call_hi = 1 - value_frac
        clears = _over(strength, 1 - continue_frac, rng)
        if clears and (fold_measured or absolute >= req_eq):
            if call_hi > call_lo:
                _keep(hand, seat, board_order, [(call_lo, call_hi)], hand.board)
            else:
                _keep(hand, seat, board_order,
                      [(1 - continue_frac, 1 - raise_f)], hand.board)
            return ("call", 0,
                    f"calls — they continue ~{continue_frac:.0%} vs this size"
                    + ("" if fold_measured else f", and this clears the {req_eq:.0%} pot odds"))
        return ("fold", 0,
                f"folds — outside the ~{continue_frac:.0%} they continue vs this size")

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
                f"cbet:{street}",
            ], 0.5, 15, vs=vs)
            fire = "c-bet"
        cap = _street_value_cap(profile, street, cbet_f, VALUE_CAP.get(street, 0.45))
        polar = _polar_bet(strength, cbet_f, cap)
        if lg.can_raise and polar:
            _keep(hand, seat, board_order, _polar_bands(cbet_f, cap), hand.board)
            frac = _bet_frac(profile, street, rng, 0.6)
            _, to = _raise_or_jam(hand, seat, lg, int(round(frac * hand.pot)))
            how = "value" if polar == "value" else "a bluff"
            return ("raise", to,
                    f"{fire}s {frac:.0%} pot ({how}) — fires ~{cbet_f:.0%} here, their own sizing")
        value_frac, bluff_frac = _polar_split(cbet_f, cap)
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
    else:
        lead_f = _freq_chain(profile, [f"probe:{street}:{ipo}", f"probe:{street}"], 0.30)
        bet_why = f"stabs — bets ~{lead_f:.0%} at a pot no one has bet"
        check_why = f"checks — not a hand they stab here (stabs ~{lead_f:.0%})"
        cap = _street_value_cap(profile, street, lead_f, VALUE_CAP.get(street, 0.45))
    polar = _polar_bet(strength, lead_f, cap)
    if lg.can_raise and lead_f > 0.02 and polar:
        _keep(hand, seat, board_order, _polar_bands(lead_f, cap), hand.board)
        frac = _bet_frac(profile, street, rng, 0.55)
        _, to = _raise_or_jam(hand, seat, lg, int(round(frac * hand.pot)))
        return ("raise", to, bet_why)
    if lg.can_check:
        value_frac, bluff_frac = _polar_split(lead_f, cap)
        _keep(hand, seat, board_order, [(bluff_frac, 1 - value_frac)], hand.board)
        return ("check", 0, check_why)
    return ("fold", 0, "folds")
