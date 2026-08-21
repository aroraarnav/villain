"""Turn a villain's measured profile into table decisions.

The idea is simple and faithful: a frequency *is* a strength threshold. A player
who folds 60% to flop bets folds their weakest 60%; one who opens 22% of hands
opens their best 22%. So the policy ranks the current hand -- preflop by a
static ranking, postflop by its percentile against every holding the board
allows -- and acts on the villain's own measured frequency as the cut line.
Loose players call wider, tight players fold more, aggressive ones bet and
raise more, all straight out of their numbers.

Two rankings, because one cannot. Opening is high-card heavy (22 is a button
open, not an UTG open). Defending a blind is not: even a tight BB calls every
pocket pair against a steal, and folds KTo before it folds 33. Chen's formula
treated those as the same hand, so a monotonic top-X% defend folded 33
three-handed -- a hand nobody who plays this game folds there. Combos are
weighted (6/4/12), because "defends 45%" is a share of dealt hands, not of
the 169 names.

Betting is polarized once the frequency exceeds what value can explain. A
maniac who c-bets 80% does not have the best 80%; they have a value slice and
the rest is air. Betting only the top taught folding to aggression, which is
the opposite of the exploit. A nit who bets 30% still only bets the top 30%.

It is a heuristic, not a solver: it reproduces how a villain *tends* to play,
so you can get reps against people who play like them. A little noise on the
cut keeps it from folding the exact same hand every time.
"""

from __future__ import annotations

import numpy as np

from .cards import RANKS, evaluate
from .holdem import STREETS
from .model import positions_for
from .stats import size_bucket

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
#: bottom of the range as bluffs. A 30% c-bet stays top-30% (they have it);
#: an 80% c-bet is the value cap plus air, so calling down is the lesson.
VALUE_CAP = {"flop": 0.55, "turn": 0.40, "river": 0.28}


def _polar_bet(strength: float, freq: float, value_cap: float) -> str | None:
    """``value``, ``bluff``, or ``None`` (check / fold) for a betting frequency."""
    freq = _clamp(freq, 0.0, 1.0)
    value_frac = min(freq, value_cap)
    bluff_frac = max(0.0, freq - value_cap)
    if strength >= 1 - value_frac:
        return "value"
    if bluff_frac > 0 and strength <= bluff_frac:
        return "bluff"
    return None


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

#: Pool baseline for raising a bet, the reference their own rate modulates.
POOL_RAISE_VS_BET = 0.06


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


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


def decide(hand, seat: int, profile, rng: np.random.Generator, name: str = "") -> tuple[str, int, str]:
    """One action, driven by the player's own frequencies AND sizes:
    ``(kind, amount, reason)``. They open to their size from their positions at
    their rates, c-bet their sizing per street, and fold to the specific bet
    size they face. Every branch falls back gracefully when a stat is thin, and
    always returns a legal action."""
    s = hand.seats[seat]
    lg = hand.legal()
    jitter = float(rng.normal(0, 0.05))
    bb = hand.bb

    if hand.street == 0:
        open_s = preflop_strength(s.hole, "open") + jitter
        defend_s = preflop_strength(s.hole, "defend") + jitter
        strength = open_s
        pos = _position(hand, seat)
        opened = hand.bet > bb
        if not opened and getattr(hand, "limpers", 0) >= 1:
            # Limpers in, nobody raised: an isolation spot, and a wider one than
            # opening a folded pot. Pooling it under rfi had every villain
            # attacking limps at their first-in rate -- PlayerE opens the button
            # 40% first in and isolates limpers at ~63%.
            iso_f = _freq_n(profile, f"iso:{pos}",
                            _freq(profile, "iso", _freq(profile, f"rfi:{pos}", 0.25)), 12)
            if lg.can_raise and strength >= 1 - iso_f:
                obb = _clamp(_size(profile, f"iso_bb:{pos}",
                                   _size(profile, "iso_bb",
                                         _size(profile, f"open_bb:{pos}", 3.5))), 2.5, 7.0)
                # An iso is sized to punish the limpers, so it grows with them.
                _, to = _raise_to(hand, lg, int(round(obb * bb)) + hand.limpers * bb)
                return ("raise", to,
                        f"isolates the {hand.limpers} limper{'s' if hand.limpers > 1 else ''}"
                        f" to {to / bb:.1f}bb — attacks limps ~{iso_f:.0%} from {pos or 'here'}")
            over = _freq_n(profile, "over_limp", 0.05, 12)
            if lg.can_call and over > 0.05 and strength >= 1 - _clamp(over * 2, 0.08, 0.45):
                return ("call", 0, f"over-limps behind — comes along ~{over:.0%} in limped pots")
            if lg.can_check:
                return ("check", 0, "checks the option behind the limpers")
            return ("fold", 0, f"folds — isolates only ~{iso_f:.0%} from {pos or 'here'}")

        if not opened:                              # first in: open by position, at their size
            rfi = _freq_n(profile, f"rfi:{pos}", _freq(profile, "rfi", 0.22), 20)
            if lg.can_raise and strength >= 1 - rfi:
                obb = _clamp(_size(profile, f"open_bb:{pos}", _size(profile, "open_bb", 2.5)), 2.0, 5.0)
                _, to = _raise_to(hand, lg, int(round(obb * bb)))
                return ("raise", to,
                        f"opens to {obb:.1f}bb from {pos or 'the button'} — opens ~{rfi:.0%} there, their own size")
            limp = _freq(profile, "limp", 0.03)
            if lg.can_call and limp > 0.06 and strength >= 1 - _clamp(limp * 3, 0.1, 0.5):
                return ("call", 0, f"limps — open-limps about {limp:.0%} of the time")
            if lg.can_check:
                return ("check", 0, "checks the option")
            return ("fold", 0, f"folds — opens only ~{rfi:.0%} from {pos or 'here'}, and this is weaker")

        in_bb = pos == "BB"
        level = hand.raises          # 1 open, 2 = a 3-bet, 3 = a 4-bet, 4+ = a 5-bet+
        tbet = _freq_n(profile, "three_bet", 0.07, 20)
        call_s = open_s              # vs 3-bet+ the continue is premiums, not pairs
        if level <= 1:               # facing an open -> a 3-bet can be wide (a bluff)
            opener_pos = (_position(hand, hand.last_raiser)
                          if hand.last_raiser is not None else "")
            steal = opener_pos in ("CO", "BTN", "SB") and pos in ("SB", "BB")
            ratio = _clamp(_size(profile, "three_bet_ratio", 3.0), 2.2, 5.0)
            rr_to = int(round(ratio * hand.bet))
            if getattr(hand, "callers", 0) >= 1:
                # Cold-callers behind the open: a squeeze, which is its own
                # frequency and a much stronger claim than a plain 3-bet.
                rr_freq = _freq_n(profile, "squeeze", tbet, 15)
                rr_label = f"squeezes over {hand.callers} caller{'s' if hand.callers > 1 else ''}"
                cont = _clamp(_freq_n(profile, "cold_call", 0.18, 25), 0.05, 0.6)
                cont_why = "flats behind the callers"
            elif steal:
                rr_freq = _freq_n(profile, "three_bet_vs_steal", tbet, 20)
                rr_label = "3-bets the steal"
                cont = (_freq(profile, "bb_defend", 0.45) if in_bb
                        else _clamp(1 - _freq(profile, "fold_to_steal", 0.55), 0.08, 0.7))
                cont_why = f"defends the {'BB' if in_bb else 'SB'} vs a {opener_pos} open"
            else:
                rr_freq, rr_label = tbet, "3-bets"
                cont = (_freq(profile, "bb_defend", 0.40) if in_bb
                        else _clamp(_freq(profile, "cold_call", 0.18) + tbet, 0.05, 0.75))
                cont_why = "defends their blind" if in_bb else "continues"
            rr_gate = 1 - rr_freq
            call_s = defend_s
        elif level == 2:             # facing a 3-bet -> a 4-bet is premiums only
            four_f = _freq_n(profile, "four_bet", 0.04, 12)   # GTO ~4%; sample-gated
            rr_gate, rr_label = 1 - four_f, "4-bets"
            rr_to = int(round(_clamp(_size(profile, "four_bet_ratio", 2.3), 2.0, 2.8) * hand.bet))
            # In and out of position facing a 3-bet are different decisions;
            # prefer the side we are actually on when it has sample.
            ipo = "ip" if _in_position(hand, seat) else "oop"
            f3 = _freq_n(profile, f"fold_to_three_bet:{ipo}",
                         _freq_n(profile, "fold_to_three_bet", 0.55, 20), 12)
            cont = _clamp(1 - f3, 0.05, 0.16)
            cont_why = f"flats the 3-bet {ipo}"
        elif level == 3:             # facing a 4-bet -> a 5-bet jam is QQ+/AK
            five_f = _freq_n(profile, "five_bet", 0.02, 10)   # GTO ~2%; sample-gated
            rr_gate, rr_label = 1 - five_f, "5-bets"
            rr_to = lg.max_raise_to
            cont = _clamp(1 - _freq_n(profile, "fold_to_four_bet", 0.50, 12), 0.03, 0.08)
            cont_why = "calls the 4-bet"
        else:                        # facing a 5-bet+ shove -- only the nuts
            rr_gate, rr_label = 0.99, f"{level + 2}-bets"
            rr_to = lg.max_raise_to
            cont, cont_why = 0.02, "calls the shove"
        if lg.can_raise and strength >= rr_gate:
            _, to = _raise_to(hand, lg, rr_to)
            # The blind is the 1-bet and an open is the 2-bet, so raising at
            # `level` makes an (level + 2)-bet -- which is what rr_label
            # already says ("3-bets" facing an open). The reason said one
            # less and contradicted the label in the same sentence.
            return ("raise", to, f"{rr_label} to {to} — a premium at {level + 2}-bet depth")
        if lg.can_call and call_s >= 1 - cont:
            return ("call", 0, f"{cont_why} — roughly their top {cont:.0%} facing this")
        if lg.can_check:
            return ("check", 0, "checks")
        return ("fold", 0, f"folds — outside the ~{cont:.0%} that continue at this depth")

    street = STREETS[hand.street]
    strength = hand_strength(s.hole, hand.board) + jitter
    has_init = hand.initiative == seat

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
            # A raised pot is still MDF, but MDF assumes the aggressor bluffs
            # at equilibrium. Nobody raises a made bet as a bluff anywhere near
            # that often, so defending P/(P+B) here hands money away. The
            # defended share is MDF scaled down by how value-heavy a raise at
            # this depth is, then modulated by how often this player actually
            # raises: someone who raises twice as often as the pool has twice
            # the bluffs in the range, and more of ours has to continue.
            #
            # At a pot-sized raise (mdf 0.50) this reproduces the cutoffs it
            # replaces -- 0.80/0.955 facing a raise, 0.96/0.985 facing a
            # re-raise -- but now a small raise is defended wider and an
            # overbet tighter, which a flat constant could never do.
            heavy, floor, ceil_ = ((RERAISE_VALUE_WEIGHT, 0.02, 0.12) if level >= 3
                                   else (RAISE_VALUE_WEIGHT, 0.08, 0.45))
            raise_f = _freq_n(profile, f"raise_vs_bet:{street}", POOL_RAISE_VS_BET, 12)
            polarity = _clamp(raise_f / POOL_RAISE_VS_BET, 0.5, 2.0)
            defend = _clamp(mdf * heavy * polarity, floor, ceil_)
            value_bar = 1 - defend * (JAM_SHARE if level >= 3 else RERAISE_SHARE)
            depth = "a re-raised pot" if level >= 3 else "a raised pot"
            if lg.can_raise and strength >= value_bar:
                if level >= 3:
                    _, to = _raise_to(hand, lg, lg.max_raise_to)
                    return ("raise", to,
                            f"jams — {depth}; only the top {1 - value_bar:.0%} of what continues")
                frac = _clamp(_size(profile, f"cbet_size:{street}", 0.8), 0.4, 2.0)
                _, to = _raise_to(hand, lg, hand.bet + int(round(frac * hand.pot)))
                return ("raise", to,
                        f"re-raises for value — the top {1 - value_bar:.0%} of the range that continues here")
            if strength >= 1 - defend:
                return ("call", 0,
                        f"calls — {depth}; they raise ~{raise_f:.0%} here, so the top "
                        f"{defend:.0%} continues")
            return ("fold", 0,
                    f"folds — {depth}; below the top {defend:.0%} that continues, and "
                    f"{req_eq:.0%} pot odds do not rescue one pair")
        # level 1: MDF defense, value-raise the top, call to pot odds.
        # Their own rate sets the level; the price sets the slope. A pooled
        # fold frequency was measured against the sizes they usually face, so
        # applying it unchanged to a 175%-pot overbet made the bot's continue
        # threshold identical at every size on every street -- MDF and pot
        # odds were computed and then never bound. Shift the pooled rate by
        # the change in breakeven between their average faced size and this
        # one, which is exactly what MDF says the difference should be.
        fold_f = _freq_n(profile, f"fold_vs_bet:{street}:{bucket}", None, 12)
        if fold_f is None:
            fold_f = _freq_n(profile, f"fold_vs_bet:{street}", 1 - mdf, 20)
            usual = _size(profile, f"faced_size:{street}", None, 20)
            if usual and usual > 0:
                be_usual = usual / (1.0 + usual)
                fold_f = _clamp(fold_f + ((1 - mdf) - be_usual), 0.02, 0.98)
        raise_f = min(_freq_n(profile, f"raise_vs_bet:{street}", 0.06, 12), 0.20)
        if s.street_put == 0:                          # a raise here is a check-raise
            raise_f = max(raise_f, min(_freq_n(profile, f"check_raise:{street}", raise_f, 15), 0.20))
        if lg.can_raise and strength >= 1 - raise_f:
            rr = _size(profile, f"raise_ratio:{street}", None, 6)
            target = int(round(rr * B)) if rr else hand.bet + int(round(0.9 * hand.pot))
            _, to = _raise_to(hand, lg, target)
            return ("raise", to, f"raises for value — the top of the range vs a {street} bet")
        continue_frac = _clamp(1 - fold_f, 0.02, 0.98)
        if strength >= 1 - continue_frac and strength >= req_eq:
            return ("call", 0,
                    f"calls — they continue ~{continue_frac:.0%} vs this size, "
                    f"and this clears the {req_eq:.0%} pot odds")
        return ("fold", 0,
                f"folds — outside the ~{continue_frac:.0%} they continue vs this size")

    # checked to
    if has_init:                                     # only the aggressor c-bets
        cbet_f = _freq(profile, f"cbet:{street}", 0.5)
        polar = _polar_bet(strength, cbet_f, VALUE_CAP.get(street, 0.45))
        if lg.can_raise and polar:
            frac = _clamp(_size(profile, f"cbet_size:{street}", _size(profile, f"bet_size:{street}", 0.6)), 0.2, 2.0)
            _, to = _raise_to(hand, lg, int(round(frac * hand.pot)))
            how = "value" if polar == "value" else "a bluff"
            return ("raise", to,
                    f"c-bets {frac:.0%} pot ({how}) — fires ~{cbet_f:.0%} here, their own sizing")
        return ("check", 0, f"checks back — outside the ~{cbet_f:.0%} they c-bet here")
    # No initiative of our own -- two different spots:
    #   * a live aggressor who has not acted yet -> leading is a DONK (rare)
    #   * a limped or checked-through pot -> betting is a STAB / probe (normal)
    donking = hand.initiative is not None and hand.initiative not in hand.acted
    if donking:
        lead_f = _freq(profile, f"donk:{street}", 0.04)
        bet_why = f"leads out — donks ~{lead_f:.0%} into the raiser, with a hand worth it"
        check_why = f"checks — hardly ever donks into the raiser (~{lead_f:.0%})"
    else:
        lead_f = _freq(profile, f"probe:{street}", 0.30)     # nobody has bet -> stab it
        bet_why = f"stabs — bets ~{lead_f:.0%} at a pot no one has bet"
        check_why = f"checks — not a hand they stab here (stabs ~{lead_f:.0%})"
    polar = _polar_bet(strength, lead_f, VALUE_CAP.get(street, 0.45))
    if lg.can_raise and lead_f > 0.02 and polar:
        frac = _clamp(_size(profile, f"cbet_size:{street}", _size(profile, f"bet_size:{street}", 0.55)), 0.2, 1.5)
        _, to = _raise_to(hand, lg, int(round(frac * hand.pot)))
        return ("raise", to, bet_why)
    if lg.can_check:
        return ("check", 0, check_why)
    return ("fold", 0, "folds")
